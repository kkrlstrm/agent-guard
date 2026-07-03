"""agent-guard — a tiny, stdlib-only policy engine for Claude Code PreToolUse hooks.

Two failure biases, one engine:
  - fail-OPEN nudges (telemetry-driven reminders injected as additionalContext),
  - fail-CLOSED blocks (a hard read-only firewall for a least-privilege sub-agent).

Rules live in JSON (see ../rules/*.rules.json); each verdict is appended to a
hash-chained, tamper-evident audit log (see audit.py). The absolute invariant is
that a guard bug must never wedge a session: any unexpected error falls open.
"""
