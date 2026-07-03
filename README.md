# agent-guard

**Telemetry-grounded PreToolUse guard-hooks for Claude Code.** One tiny,
stdlib-only policy engine, deployed with two opposite safety biases:

- **Nudge when the model can recover** (fail-open). Commands that would fail anyway
  get a non-blocking reminder injected as `additionalContext`, so the agent
  self-corrects instead of burning a turn.
- **Block when it can't** (fail-closed). A hard read-only firewall for a
  least-privilege analyst sub-agent — database mutations are stopped with exit 2.

No dependencies. No network. No model calls. Rules are JSON, not code. Clone it,
point Claude Code's hooks at it, done.

> The premise: modern coding models react to feedback and try something different,
> so most guardrails should hand the model a signal and get out of the way. You only
> hard-block the irreversible, verifiable cases. See
> [docs/WHEN_TO_USE.md](docs/WHEN_TO_USE.md).

## Why this exists

Every guard encodes an assumption about what the model can't do on its own — and the
best guards are grounded in what actually goes wrong, not what might. agent-guard's
starter rules came from real tool-failure telemetry, and
[`bin/derive_rules.py`](bin/derive_rules.py) lets you grow your own the same way:
mine your logs for recurring failures, and each one becomes a candidate rule.

It's the stdlib, in-process version of what agent-security startups sell as network
gateways. A `PreToolUse` hook *is* in-process interception at zero added latency —
and this one is honest that the regex firewall is a backstop above a real credential
boundary, not a substitute for one.

## Install

```bash
git clone https://github.com/…/agent-guard
./agent-guard/install.sh /path/to/your/project            # wire the Bash guard
./agent-guard/install.sh /path/to/your/project --db-reader # + the read-only sub-agent
```

`install.sh` adds a `PreToolUse` hook to your project's `.claude/settings.json`
(idempotent, with a backup) and optionally drops in the `db-reader` sub-agent. Review
the diff and commit it. Point the guard at your own rules with
`AGENT_GUARD_RULES=/path/to/rules.json`.

## How it works

```
tool call ──> PreToolUse hook ──> engine: match rules ──> resolve (most-restrictive-wins) ──> verdict
                                        │                                                        │
                                   rules/*.json                                    nudge / deny / block / allow
                                                                                                 │
                                                                              hash-chained audit log (tamper-evident)
```

Two entry scripts share one engine:

| Entry | Wired via | Ruleset | Bias |
|-------|-----------|---------|------|
| `pretooluse-guard.py` | `.claude/settings.json` (Bash, MCP) | `rules/starter.rules.json` | fail-open |
| `validate-readonly.py` | a sub-agent's frontmatter (Bash) | `rules/readonly-db.rules.json` | fail-closed |

### Rules are config

```json
{
  "id": "curl-pipe-shell",
  "tool": "Bash",
  "any": ["\\bcurl\\b[^|]*\\|\\s*(sudo\\s+)?(sh|bash)\\b"],
  "unless": [],
  "severity": 50,
  "action": "nudge",
  "message": "Piping a downloaded script into a shell runs unreviewed remote code. Download, inspect, then run.",
  "meta": {"why": "RCE footgun; recoverable, so nudge.", "added": "2026-07-03"}
}
```

| action | mechanism | tool runs? |
|--------|-----------|------------|
| `nudge` | `additionalContext` reminder (exit 0) | yes |
| `deny` | `permissionDecision: deny` (exit 0) | no |
| `block` | exit 2 + stderr (survives `bypassPermissions`) | no |
| `monitor` | audited only, never surfaced | yes |

Precedence is **most-restrictive-wins** (`block > deny > nudge > monitor`); severity
only breaks ties within an action. Full schema in
[rules/](rules/) and the engine in [guard/engine.py](guard/engine.py).

## Grow rules from your own telemetry

```bash
# From a cc-logger Postgres DB:
python3 bin/derive_rules.py --from-cc-logger --days 7 --out candidates.rules.json
# Or zero-dependency, from any JSONL tool-call log:
python3 bin/derive_rules.py --from-log tool_calls.jsonl --out candidates.rules.json
```

Recurring failures come back as candidate `monitor` rules (never auto-armed). Review,
refine the regex + message, and promote `monitor → nudge/block`. See
[docs/TELEMETRY.md](docs/TELEMETRY.md).

**Pairs with [cc-logger](https://github.com/…/cc-logger)** — cc-logger *observes*
every tool call; agent-guard *acts on* the failures it records; the audit log feeds
the next round. Neither requires the other.

## Audit log

```bash
python3 guard/audit.py verify   # OK / TAMPERED at line N
python3 guard/audit.py tail     # last 20 verdicts
```

Every fired verdict appends to a hash-chained JSONL (each line hashes the prior line
+ its payload), so any later edit breaks the chain. Default
`~/.agent-guard/audit.jsonl`; override with `$AGENT_GUARD_AUDIT`.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Stdlib `unittest`, no pytest. Fixtures drive the real entry scripts as subprocesses,
so the behavioral contract (exit 2 blocks, deny JSON, nudge context) is what's tested
— plus false-positive guards (`WHERE status='CREATED'` passes, `echo psql` doesn't
nudge). Every real failure you encode as a rule should land here as a fixture too.

## Safety invariant

**A guard bug must never wedge a session.** Every entry point exits 0 (allow) on any
unexpected error. For the read-only firewall, the durable guarantee is a `SELECT`-only
DB role — the regex is the cheap backstop above it. Read
[docs/GOTCHAS.md](docs/GOTCHAS.md) before relying on it.

## Docs

- [docs/WHEN_TO_USE.md](docs/WHEN_TO_USE.md) — fail-open vs fail-closed, the decision guide
- [docs/TELEMETRY.md](docs/TELEMETRY.md) — deriving rules from your own logs
- [docs/GOTCHAS.md](docs/GOTCHAS.md) — plugin hooks, bypassPermissions, regex limits

## License

MIT.
