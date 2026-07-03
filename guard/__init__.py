"""agent-guard — a tiny, stdlib-only policy engine for Claude Code PreToolUse hooks.

Two enforcement biases, one engine:
  - fail-OPEN nudges: telemetry-driven reminders injected as additionalContext;
  - hard-BLOCK backstop: a read-only-DB firewall for a least-privilege sub-agent
    (blocks detected mutations with exit 2). Note this is a backstop, not a true
    fail-closed boundary — the hook itself falls open on any guard error, so the
    durable guarantee is a SELECT-only DB role. See docs/THREAT_MODEL.md.

Rules live in JSON (see ../rules/*.rules.json); each verdict is appended to a
hash-chained, tamper-evident audit log (see audit.py). The absolute invariant is
that a guard bug must never wedge a session: any unexpected error falls open.
"""

__version__ = "0.2.0"
