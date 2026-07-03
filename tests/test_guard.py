#!/usr/bin/env python3
"""Replay/red-team harness for agent-guard. Stdlib unittest only — no pytest.

Run:  python3 -m unittest discover -s tests -p 'test_*.py'
  or: python3 tests/test_guard.py

Two layers:
  * FixtureReplay  — drives the REAL entry scripts as subprocesses with hook JSON
    on stdin, asserting exit code + stdout/stderr. This is the behavioural
    contract (exit 2 blocks, deny JSON, additionalContext nudge).
  * Engine/Audit   — unit tests for precedence, dry-run, promotion, and the
    hash-chained audit log's tamper detection.

Every real failure you encode as a rule should also land here as a fixture: the
fixture is the rule's spec, and CI catches nudge/firewall regressions.
"""
import os
import sys
import json
import tempfile
import subprocess
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
sys.path.insert(0, REPO)

from guard import engine, audit  # noqa: E402

ENTRY = {
    "main": os.path.join(REPO, "pretooluse-guard.py"),
    "readonly": os.path.join(REPO, "validate-readonly.py"),
}


def _drive(ruleset, tool_name, tool_input, env=None):
    payload = {"tool_name": tool_name, "tool_input": tool_input}
    run_env = dict(os.environ)
    run_env.setdefault("AGENT_GUARD_AUDIT", os.path.join(tempfile.gettempdir(), "agent-guard-test-audit.jsonl"))
    if env:
        run_env.update(env)
    return subprocess.run(
        [sys.executable, ENTRY[ruleset]],
        input=json.dumps(payload), capture_output=True, text=True, env=run_env,
    )


class FixtureReplay(unittest.TestCase):
    pass


def _make_case(ruleset, case):
    def test(self):
        tool_input = case.get("tool_input", {"command": case.get("command", "")})
        proc = _drive(ruleset, case["tool_name"], tool_input)
        exp = case["expect"]
        self.assertEqual(proc.returncode, exp["returncode"],
                         msg=f"{case['name']}: rc={proc.returncode} out={proc.stdout!r} err={proc.stderr!r}")
        if exp.get("stdout_empty"):
            self.assertEqual(proc.stdout.strip(), "", msg=f"{case['name']}: expected empty stdout, got {proc.stdout!r}")
        for key in ("stdout_contains", "stdout_contains2"):
            if key in exp:
                self.assertIn(exp[key], proc.stdout, msg=f"{case['name']}: stdout={proc.stdout!r}")
        if "stderr_contains" in exp:
            self.assertIn(exp["stderr_contains"], proc.stderr, msg=f"{case['name']}: stderr={proc.stderr!r}")
    return test


def _load_fixtures():
    for fname in sorted(os.listdir(FIXTURES)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(FIXTURES, fname)) as f:
            data = json.load(f)
        ruleset = data["ruleset"]
        for i, case in enumerate(data["cases"]):
            name = f"test_{ruleset}_{i:02d}_" + "".join(c if c.isalnum() else "_" for c in case["name"])
            setattr(FixtureReplay, name, _make_case(ruleset, case))


_load_fixtures()


class EngineUnit(unittest.TestCase):
    def _rules(self, name):
        with open(os.path.join(REPO, "rules", name)) as f:
            return json.load(f)["rules"]

    def test_shipped_rulesets_are_valid(self):
        for name in ("starter.rules.json", "readonly-db.rules.json"):
            problems = engine.verify_rules(self._rules(name))
            self.assertEqual(problems, [], msg=f"{name}: {problems}")

    def test_example_ruleset_is_valid(self):
        with open(os.path.join(REPO, "examples", "gtm-repo.rules.json")) as f:
            problems = engine.verify_rules(json.load(f)["rules"])
        self.assertEqual(problems, [])

    def test_precedence_block_beats_nudge(self):
        rules = [
            {"id": "n", "tool": "Bash", "any": ["foo"], "severity": 99, "action": "nudge", "message": "n"},
            {"id": "b", "tool": "Bash", "any": ["foo"], "severity": 1, "action": "block", "message": "b"},
        ]
        verdict = engine.resolve(engine.evaluate(rules, "Bash", {"command": "foo"}))
        self.assertEqual(verdict["decision"], "block")
        self.assertEqual(verdict["winner"]["id"], "b")

    def test_monitor_only_is_allow(self):
        rules = [{"id": "m", "tool": "Bash", "any": ["foo"], "action": "monitor", "message": "m"}]
        verdict = engine.resolve(engine.evaluate(rules, "Bash", {"command": "foo"}))
        self.assertEqual(verdict["decision"], "allow")

    def test_nudge_as_block_promotion(self):
        rules = [{"id": "n", "tool": "Bash", "any": ["foo"], "action": "nudge", "message": "n"}]
        fired = engine.evaluate(rules, "Bash", {"command": "foo"})
        self.assertEqual(engine.resolve(fired)["decision"], "nudge")
        self.assertEqual(engine.resolve(fired, nudge_as_block=True)["decision"], "block")

    def test_dryrun_does_not_block(self):
        proc = _drive("readonly", "Bash", {"command": "psql -c 'DELETE FROM t'"},
                      env={"AGENT_GUARD_DRYRUN": "1"})
        self.assertEqual(proc.returncode, 0, msg=f"dryrun should not block: {proc.stderr!r}")


class AuditChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        os.unlink(self.path)

    def test_chain_verifies(self):
        for i in range(4):
            audit.append({"tool": "Bash", "n": i}, path=self.path)
        ok, bad = audit.verify(self.path)
        self.assertTrue(ok)
        self.assertIsNone(bad)

    def test_tamper_is_detected(self):
        for i in range(4):
            audit.append({"tool": "Bash", "n": i}, path=self.path)
        with open(self.path) as f:
            lines = f.readlines()
        rec = json.loads(lines[1])
        rec["n"] = 999
        lines[1] = json.dumps(rec) + "\n"
        with open(self.path, "w") as f:
            f.writelines(lines)
        ok, bad = audit.verify(self.path)
        self.assertFalse(ok)
        self.assertEqual(bad, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
