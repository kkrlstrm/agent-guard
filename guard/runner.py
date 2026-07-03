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
import re
import sys
import json
import hashlib
from datetime import datetime, timezone

from guard.engine import evaluate, resolve, effective_action, get_field
from guard import audit
from guard import __version__

# Patterns whose *values* must never land in the audit log's command preview.
_SECRET_PREVIEW = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"glpat-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(postgres(?:ql)?://[^:@\s]+):[^@\s]+@", re.I),  # DSN password
    re.compile(r"(?i)(password|secret|api[_-]?key|token)(\s*[=:]\s*)\S+"),
]


def _redacted_preview(cmd, limit=120):
    """A short, secret-scrubbed preview of the command for the audit log. We log a
    hash for exact identity and only a redacted preview for human legibility, so a
    command containing a live key doesn't get persisted verbatim."""
    s = cmd[:limit]
    for pat in _SECRET_PREVIEW:
        if pat.groups >= 2:
            s = pat.sub(lambda m: m.group(1) + m.group(2) + "***", s)
        elif pat.groups == 1:
            s = pat.sub(lambda m: m.group(1) + ":***@", s)
        else:
            s = pat.sub("***", s)
    return s + ("…" if len(cmd) > limit else "")


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
                cmd = get_field(tool_input, "command")
                event = {
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "tool": tool_name,
                    "ruleset": os.path.basename(ruleset_path) if ruleset_path else "inline",
                    "fired": [r.get("id") for r in fired],
                    "actions": [effective_action(r, nudge_as_block) for r in fired],
                    "decision": "allow (dryrun)" if dryrun else verdict["decision"],
                    "winner": (verdict["winner"] or {}).get("id"),
                    "version": __version__,
                    "dryrun": dryrun,
                    "nudge_as_block": nudge_as_block,
                }
                if data.get("session_id"):
                    event["session_id"] = data["session_id"]
                if data.get("cwd"):
                    event["cwd"] = data["cwd"]
                if cmd:
                    # Hash for exact identity; only a redacted preview is stored in the clear.
                    event["command_sha256"] = hashlib.sha256(cmd.encode("utf-8", "replace")).hexdigest()
                    event["command_preview"] = _redacted_preview(cmd)
                audit.append(event)
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
