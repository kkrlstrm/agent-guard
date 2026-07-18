# agent-guard

<!-- portfolio-status -->
**Status:** Production-used — I run this against my own live agent workflows. · **Layer:** Execution controls · **[Portfolio map ›](https://github.com/kkrlstrm)**

**Runtime controls for Claude Code, derived from your own agent telemetry.**

[cc-logger](https://github.com/kkrlstrm/cc-logger) shows you how your agents
actually behave: which tools they call, where they fail, where they drift, and
which mistakes repeat. agent-guard closes the loop by turning those observations
into lightweight `PreToolUse` controls: a nudge when the model can recover, a
block when the action is irreversible.

> **cc-logger is the flight recorder. agent-guard is the control surface.**

## The loop

```
        Claude Code run
              │
              ▼
   cc-logger records prompts, tools, sub-agents, failures, drift
              │
              ▼
   derive recurring failure patterns          (bin/derive_rules.py)
              │
              ▼
   agent-guard promotes them into monitor / nudge / deny / block rules
              │
              ▼
   tamper-evident audit log  ──▶  new telemetry
              └──────────────◀───────────────┘
```

First you observe your agents like game film. Then you promote repeated failures
into rules. Over time your local Claude Code setup gets less improvisational and
more operationally reliable. This is the same loop every production system
eventually needs: observability, conformance, controls, auditability. cc-logger
gives you the first two; agent-guard gives you the last two.

## Recovery-first guardrails

Most guardrails shouldn't block. They should hand the model the exact missing
context and let it recover. Modern coding models react to feedback and try
something different, so hard-blocking a recoverable mistake wastes a turn the
model would've fixed on its own. You reserve blocks for what can't be undone.

| Situation | Response | What it does |
|-----------|----------|--------------|
| Recoverable mistake | **Nudge** | inject the missing context, let the call proceed |
| Policy violation | **Deny** | refuse with a reason the model can act on |
| Irreversible / verifiable risk | **Block** | hard stop; survives a parent's permission bypass |
| Unknown pattern | **Monitor** | log it, gather evidence before you enforce |

## Why this is different

Most guardrails are written from imagination: *what might go wrong?* agent-guard
starts from evidence: *what has actually gone wrong in my runs?*

Over-blocking slows agents down; under-blocking lets the same mistake repeat
forever. agent-guard gives you the middle path: watch failures, promote the
recurring ones into nudges, and save hard blocks for cases where recovery is
impossible or the blast radius is real.

No dependencies. No network. No model calls. Rules are JSON, not code. The guard
runs in-process at the tool boundary and writes a tamper-evident audit log.

## Install

```bash
git clone https://github.com/kkrlstrm/agent-guard
./agent-guard/install.sh /path/to/your/project            # wire the Bash guard
./agent-guard/install.sh /path/to/your/project --db-reader # + the read-only sub-agent
```

`install.sh` adds a `PreToolUse` hook to your project's `.claude/settings.json`
(idempotent, with a backup) and optionally drops in the `db-reader` sub-agent.
Review the diff and commit it. Point the guard at your own rules with
`AGENT_GUARD_RULES=/path/to/rules.json`.

## How it works

```
tool call ─▶ PreToolUse hook ─▶ engine: match rules ─▶ resolve (most-restrictive-wins) ─▶ verdict
                                     │                                                       │
                                rules/*.json                                  nudge / deny / block / allow
                                                                                             │
                                                                          hash-chained audit log (tamper-evident)
```

Two entry scripts share one engine:

| Entry | Wired via | Ruleset | Bias |
|-------|-----------|---------|------|
| `pretooluse-guard.py` | `.claude/settings.json` (Bash, MCP) | `rules/starter.rules.json` | fail-open |
| `validate-readonly.py` | a sub-agent's frontmatter (Bash) | `rules/readonly-db.rules.json` | hard-block backstop¹ |

¹ It hard-blocks a *matched* mutation, but the hook still falls open on any guard
error (a guard bug must never wedge a session), so it isn't a true fail-closed
boundary. Pair it with a `SELECT`-only DB role for the real guarantee. See
[docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

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

Each action maps to a real Claude Code hook mechanism:

| action | mechanism | tool runs? |
|--------|-----------|------------|
| `nudge` | `additionalContext` reminder (exit 0) | yes |
| `deny` | `permissionDecision: deny` (exit 0) | no |
| `block` | exit 2 + stderr (survives `bypassPermissions`) | no |
| `monitor` | audited only, never surfaced | yes |

Precedence is **most-restrictive-wins** (`block > deny > nudge > monitor`);
severity only breaks ties within an action. `field` defaults to `command` (Bash),
and takes a **dotted path** for nested MCP arguments (`"field": "args.repository"`,
`"field": "params.0.name"`) so a rule can match structured tool input, not just a
flat string. Full schema in [rules/](rules/) and the engine in
[guard/engine.py](guard/engine.py). The decision guide for which bias to pick is
[docs/WHEN_TO_USE.md](docs/WHEN_TO_USE.md).

Each rule can carry an `examples` block (`should_fire` / `should_not_fire`) that
doubles as its spec and is checked by the validator below.

## Verify it's working

The biggest real failure mode for a hook tool is silent: it isn't wired, or a
rules file is broken, so nothing fires. Two commands make that checkable.

```bash
python3 bin/doctor.py --project /path/to/project   # end-to-end self-test
python3 bin/check_rules.py rules/*.json mine.json  # validate rules + run examples
```

`doctor` drives the real entry scripts (does `rm -rf /` block? does `SELECT`
pass?), checks the audit path is writable, confirms the hook is wired in your
project, and warns if the `db-reader` agent is installed under a plugin path where
hooks are ignored. `check_rules` parses every rule, compiles every regex, lints
for catastrophic-backtracking shapes, and runs each rule's `examples` as
assertions. Both are stdlib and exit non-zero on failure, so they drop into CI.

## Grow rules from your own telemetry

This is the point of the loop. Don't hand-write rules from a list of things that
might go wrong; mine what actually went wrong.

```bash
# From a cc-logger Postgres DB:
python3 bin/derive_rules.py --from-cc-logger --days 7 --out candidates.rules.json
# Or zero-dependency, from any JSONL tool-call log:
python3 bin/derive_rules.py --from-log tool_calls.jsonl --out candidates.rules.json
```

Recurring failures come back as candidate `monitor` rules (never auto-armed).
Review, refine the regex + message, and promote `monitor → nudge/block`. A whole
tool surface that keeps failing (a flaky MCP server) rolls up to one candidate
(`mcp__Server__*`). See [docs/TELEMETRY.md](docs/TELEMETRY.md).

## Audit log

```bash
python3 guard/audit.py verify   # OK / TAMPERED at line N
python3 guard/audit.py tail     # last 20 verdicts
```

Every fired verdict appends to a hash-chained JSONL (each line hashes the prior
line + its payload), so any later in-place edit breaks the chain. Each event
carries a timestamp, the fired rule ids + decision, the agent-guard version, the
session id and cwd (when the hook provides them), and — secret-safely — a SHA-256
of the command plus a **redacted** preview, so a command containing a live key is
never persisted verbatim. That log is the evidence layer, and it's the telemetry
the next round of rule derivation reads. Default `~/.agent-guard/audit.jsonl`;
override with `$AGENT_GUARD_AUDIT`.

It's tamper-*evident* for local review, not a compliance vault: someone with
write access can rewrite the whole chain from genesis unless you anchor the head
hash externally. See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Stdlib `unittest`, no pytest. Fixtures drive the real entry scripts as
subprocesses, so the behavioral contract (exit 2 blocks, deny JSON, nudge
context) is what's tested, plus false-positive guards (`WHERE status='CREATED'`
passes, `echo psql` doesn't nudge). Every real failure you encode as a rule
should land here as a fixture too.

## Scope

This isn't a universal agent-security platform, and it doesn't try to be. It's a
tiny, local control layer for Claude Code workflows, aligned with where agent
security is heading (runtime tool-call boundaries, side-effect authorization) but
deliberately small. The read-only DB backstop is a layer above a real credential
boundary (a `SELECT`-only role), not a replacement for one. The full protects /
doesn't-protect list is in [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

**A guard bug must never wedge a session.** Every entry point exits 0 (allow) on
any unexpected error. Read [docs/GOTCHAS.md](docs/GOTCHAS.md) before relying on
it, especially the plugin-hooks and `bypassPermissions` notes.

## Docs

- [docs/WHEN_TO_USE.md](docs/WHEN_TO_USE.md) — recovery-first: which bias to pick
- [docs/TELEMETRY.md](docs/TELEMETRY.md) — deriving rules from your own logs
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) — what it protects against, and what it doesn't
- [docs/GOTCHAS.md](docs/GOTCHAS.md) — plugin hooks, bypassPermissions, regex limits

## License

GNU AGPL-3.0 — see [LICENSE](LICENSE). Copyright (C) 2026 Kai Karlstrom.

---

<!-- portfolio-footer -->
## Where this fits

Part of a portfolio of **governed, AI-native GTM systems** — reference implementations and reusable patterns extracted from a private production stack. In that system this is the recovery-first control surface that turns observed failures into runtime guardrails.

**Full portfolio map → [github.com/kkrlstrm](https://github.com/kkrlstrm)**

Works with:
- [cc-logger](https://github.com/kkrlstrm/cc-logger) — supplies the telemetry rules are derived from
- [model-eval-gate](https://github.com/kkrlstrm/model-eval-gate) — the policy gate for model economics
