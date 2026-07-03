#!/usr/bin/env python3
"""Tests for bin/derive_rules.py — the telemetry -> candidate-rules path.

Uses the zero-dependency --from-log mode (no DB needed). Verifies clustering,
thresholding, MCP-namespace aggregation, and that derived candidates are valid
rules the engine accepts.
"""
import os
import sys
import json
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "bin"))

from guard import engine  # noqa: E402
import derive_rules  # noqa: E402

LOG = [
    {"tool_name": "Bash", "tool_input": {"command": "psql -c 'SELECT 1'"}, "status": "failure", "error": 'FATAL: database "a" does not exist'},
    {"tool_name": "Bash", "tool_input": {"command": "psql -c 'SELECT 2'"}, "status": "failure", "error": 'FATAL: database "b" does not exist'},
    {"tool_name": "Bash", "tool_input": {"command": "psql -c 'SELECT 3'"}, "status": "failure", "error": 'FATAL: database "c" does not exist'},
    {"tool_name": "Bash", "tool_input": {"command": "ls"}, "status": "success"},
    {"tool_name": "mcp__Neon__run_sql", "tool_input": {}, "status": "failure", "error": "401 unauthorized"},
    {"tool_name": "mcp__Neon__run_sql", "tool_input": {}, "status": "failure", "error": "403 forbidden 9"},
    {"tool_name": "mcp__Neon__list_projects", "tool_input": {}, "status": "failure", "error": "401 unauthorized"},
    {"tool_name": "WebFetch", "tool_input": {"url": "http://x"}, "status": "failure", "error": "timeout"},
]


class DeriveRules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl", mode="w")
        for row in LOG:
            self.tmp.write(json.dumps(row) + "\n")
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        os.unlink(self.path)

    def _candidates(self, min_count=3):
        rows = derive_rules.load_rows_from_log(self.path, days=7, min_count=min_count)
        return derive_rules.derive_from_rows(rows, 7, min_count=min_count)

    def test_bash_cluster_becomes_command_pattern(self):
        cands = self._candidates()
        psql = [c for c in cands if c["tool"] == "Bash"]
        self.assertEqual(len(psql), 1)
        self.assertIn(r"\bpsql\b", psql[0]["any"])
        self.assertEqual(psql[0]["action"], "monitor")
        self.assertEqual(psql[0]["meta"]["fail_count"], 3)

    def test_mcp_namespace_aggregation(self):
        # 2 run_sql + 1 list_projects = 3 across the mcp__Neon__ surface.
        cands = self._candidates()
        neon = [c for c in cands if c["tool"] == "mcp__Neon__*"]
        self.assertEqual(len(neon), 1)
        self.assertEqual(neon[0]["meta"]["fail_count"], 3)
        self.assertNotIn("any", neon[0])  # tool-wide

    def test_threshold_excludes_singletons(self):
        cands = self._candidates(min_count=3)
        self.assertFalse(any(c["tool"] == "WebFetch" for c in cands))  # only 1 failure

    def test_candidates_are_valid_rules(self):
        cands = self._candidates()
        self.assertEqual(engine.verify_rules(cands), [])
        # And they all ship as monitor (never auto-armed).
        self.assertTrue(all(c["action"] == "monitor" for c in cands))

    def test_normalize_clusters_across_ids(self):
        a = derive_rules.normalize_error('FATAL: database "kai" does not exist')
        b = derive_rules.normalize_error('FATAL: database "bob" does not exist')
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main(verbosity=2)
