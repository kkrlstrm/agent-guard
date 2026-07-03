#!/usr/bin/env python3
"""PreToolUse read-only firewall for a least-privilege sub-agent (fail-CLOSED).

Wire this into the sub-agent's frontmatter (Bash matcher) so it constrains ONLY
that sub-agent, never the main session. It hard-blocks (exit 2) any command that
mutates a database. Read-only commands pass.

Ruleset resolution:
  $AGENT_GUARD_READONLY_RULES -> use that file
  else                        -> rules/readonly-db.rules.json next to this script

Bias: false positives (blocking a harmless read) are acceptable; false negatives
are not. The regex layer is the cheap backstop — pair it with a SELECT-only DB
role for the real guarantee (see agents/db-reader.md, docs/GOTCHAS.md).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from guard.runner import run  # noqa: E402

RULES = os.environ.get("AGENT_GUARD_READONLY_RULES") or os.path.join(HERE, "rules", "readonly-db.rules.json")

if __name__ == "__main__":
    try:
        run(RULES)
    except SystemExit:
        raise
    except Exception:
        # A guard bug must never wedge the sub-agent; the SELECT-only DB role is
        # the durable backstop when this layer can't run.
        sys.exit(0)
