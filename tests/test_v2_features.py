#!/usr/bin/env python3
"""Tests for the v0.2 additions: dotted-field matching, regex lint, enriched +
secret-safe audit events, the rule-validation CLI, and doctor."""
import os
import sys
import json
import tempfile
import subprocess
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)
from guard import engine  # noqa: E402

MAIN = os.path.join(REPO, "pretooluse-guard.py")


class DottedField(unittest.TestCase):
    def test_get_field_nested_dict(self):
        ti = {"args": {"repository": "acme/prod", "op": "delete"}}
        self.assertEqual(engine.get_field(ti, "args.repository"), "acme/prod")

    def test_get_field_list_index(self):
        ti = {"params": [{"name": "x"}, {"name": "drop"}]}
        self.assertEqual(engine.get_field(ti, "params.1.name"), "drop")

    def test_get_field_missing_returns_empty(self):
        self.assertEqual(engine.get_field({"a": {}}, "a.b.c"), "")

    def test_get_field_nonstring_leaf_is_json(self):
        self.assertIn("delete", engine.get_field({"args": {"ops": ["delete", "x"]}}, "args.ops"))

    def test_rule_matches_nested_mcp_arg(self):
        rule = {"id": "gh-delete", "tool": "mcp__GitHub__*", "field": "args.operation",
                "any": ["delete|force_merge"], "action": "deny", "message": "no"}
        self.assertTrue(engine.rule_fires(rule, "mcp__GitHub__repo", {"args": {"operation": "delete"}}))
        self.assertFalse(engine.rule_fires(rule, "mcp__GitHub__repo", {"args": {"operation": "read"}}))


class RegexLint(unittest.TestCase):
    def test_flags_nested_quantifier(self):
        warns = engine.warn_rules([{"id": "bad", "tool": "Bash", "any": ["(a+)+b"]}])
        self.assertTrue(any("bad" in w for w in warns))

    def test_clean_rule_no_warning(self):
        self.assertEqual(engine.warn_rules([{"id": "ok", "tool": "Bash", "any": ["\\bfoo\\b"]}]), [])

    def test_shipped_rules_have_no_lint_warnings(self):
        for name in ("starter.rules.json", "readonly-db.rules.json"):
            with open(os.path.join(REPO, "rules", name)) as f:
                rules = json.load(f)["rules"]
            self.assertEqual(engine.warn_rules(rules), [], msg=name)


class AuditEnrichment(unittest.TestCase):
    def _drive(self, command, tool="Bash"):
        audit_path = tempfile.mktemp(suffix=".jsonl")
        env = dict(os.environ, AGENT_GUARD_AUDIT=audit_path)
        payload = {"tool_name": tool, "tool_input": {"command": command},
                   "session_id": "sess-123", "cwd": "/tmp/proj"}
        subprocess.run([sys.executable, MAIN], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
        rows = [json.loads(l) for l in open(audit_path) if l.strip()]
        os.unlink(audit_path)
        return rows

    def test_event_has_metadata(self):
        rows = self._drive("psql -c 'SELECT 1'")  # fires bare-psql nudge
        self.assertTrue(rows)
        ev = rows[-1]
        for key in ("ts", "version", "command_sha256", "command_preview", "session_id", "cwd", "dryrun"):
            self.assertIn(key, ev)
        self.assertEqual(ev["session_id"], "sess-123")

    def test_secret_is_redacted_not_logged(self):
        secret = "AKIAIOSFODNN7EXAMPLE"
        rows = self._drive(f"AWS_KEY={secret} aws s3 ls")  # fires secret-inline nudge
        ev = rows[-1]
        self.assertNotIn(secret, json.dumps(ev))          # raw secret never persisted
        self.assertIn("***", ev["command_preview"])        # but redacted preview kept
        self.assertEqual(len(ev["command_sha256"]), 64)    # hash gives exact identity


class CLIs(unittest.TestCase):
    def test_check_rules_passes_on_shipped(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO, "bin", "check_rules.py"),
             os.path.join(REPO, "rules", "starter.rules.json"),
             os.path.join(REPO, "rules", "readonly-db.rules.json")],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("example assertion", proc.stdout)

    def test_check_rules_fails_on_broken_example(self):
        bad = tempfile.mktemp(suffix=".json")
        json.dump({"rules": [{"id": "x", "tool": "Bash", "any": ["\\bfoo\\b"], "action": "nudge",
                              "message": "m", "examples": {"should_fire": ["bar only"]}}]}, open(bad, "w"))
        proc = subprocess.run([sys.executable, os.path.join(REPO, "bin", "check_rules.py"), bad],
                              capture_output=True, text=True)
        os.unlink(bad)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("example FAIL", proc.stdout)

    def test_doctor_runs_clean(self):
        proc = subprocess.run([sys.executable, os.path.join(REPO, "bin", "doctor.py")],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("HEALTHY", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
