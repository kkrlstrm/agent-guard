#!/usr/bin/env python3
"""PreToolUse guard for the MAIN Claude Code session (fail-OPEN).

Wire this into .claude/settings.json for the tools you want guarded (Bash, and
any MCP surface). It loads a ruleset and lets the shared engine decide: rules
that would fail anyway become non-blocking nudges (injected as additionalContext
so the model self-corrects); a couple of destructive patterns block.

Ruleset resolution:
  $AGENT_GUARD_RULES  -> use that file (point it at your own tuned ruleset)
  else                -> rules/starter.rules.json next to this script

All logic lives in guard/ (engine, runner, audit); rules live in JSON. Safety
bias: any error -> exit 0 (allow). See README.md and docs/WHEN_TO_USE.md.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from guard.runner import run  # noqa: E402

RULES = os.environ.get("AGENT_GUARD_RULES") or os.path.join(HERE, "rules", "starter.rules.json")

if __name__ == "__main__":
    try:
        run(RULES)
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # absolute fail-open backstop
