"""The shared PreToolUse entry point. Both hook scripts call run(<ruleset>).

Flow: read hook JSON on stdin -> evaluate ruleset -> audit the verdict
(best-effort) -> emit the decision. Absolute invariant: any unexpected error
falls open (exit 0). Auditing never blocks a call.

Env toggles:
  AGENT_GUARD_DRYRUN=1          -> compute + audit, but never block/nudge (observe only)
  AGENT_GUARD_NUDGE_AS_BLOCK=1  -> promote every nudge to a hard block (exit 2)
  AGENT_GUARD_AUDIT=<path>      -> override the audit-log location
"""
import os
import sys
import json

from guard.engine import evaluate, resolve, effective_action
from guard import audit


def _emit_nudge(notes):
    reminder = "agent-guard (telemetry-driven):\n- " + "\n- ".join(notes)
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": reminder,
        }
    }))
    sys.exit(0)


def _emit_deny(reason):
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def _emit_block(message):
    # Exit 2 is the hard-block contract: stderr is fed back to the model, and it
    # holds even when a parent session runs with bypassPermissions.
    sys.stderr.write("BLOCKED by agent-guard: " + message + "\n")
    sys.exit(2)


def run(ruleset_path, load_rules_fn=None):
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)  # malformed input -> never block

    try:
        tool_name = data.get("tool_name", "") or ""
        tool_input = data.get("tool_input", {}) or {}

        if load_rules_fn is not None:
            rules = load_rules_fn()
        else:
            with open(ruleset_path) as f:
                rules = json.load(f).get("rules", [])

        fired = evaluate(rules, tool_name, tool_input)

        nudge_as_block = os.environ.get("AGENT_GUARD_NUDGE_AS_BLOCK") == "1"
        dryrun = os.environ.get("AGENT_GUARD_DRYRUN") == "1"
        verdict = resolve(fired, nudge_as_block=nudge_as_block)

        # Best-effort audit — a logging failure must never block the call.
        try:
            if fired:
                audit.append({
                    "tool": tool_name,
                    "ruleset": os.path.basename(ruleset_path) if ruleset_path else "inline",
                    "fired": [r.get("id") for r in fired],
                    "actions": [effective_action(r, nudge_as_block) for r in fired],
                    "decision": "allow (dryrun)" if dryrun else verdict["decision"],
                    "winner": (verdict["winner"] or {}).get("id"),
                })
        except Exception:
            pass

        if dryrun:
            sys.exit(0)

        decision = verdict["decision"]
        if decision == "block":
            _emit_block(verdict["winner"]["message"])
        elif decision == "deny":
            _emit_deny(verdict["winner"]["message"])
        elif decision == "nudge" and verdict["notes"]:
            _emit_nudge(verdict["notes"])
        sys.exit(0)

    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # absolute fail-open backstop
