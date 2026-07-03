# Turning your telemetry into rules

The three rules agent-guard was born from weren't guessed — they came from ~37k
logged tool calls in a real repo, where a handful of failures kept recurring. A
failure that keeps happening is a job the model can't do reliably on its own yet.
That's the definition of a candidate rule.

`bin/derive_rules.py` finds those clusters for you and writes them out as candidate
`monitor` rules. It never arms anything — you review, refine, and promote.

## The loop

```
observe failures  ──>  derive candidates  ──>  review + refine  ──>  promote      ──>  audit
(cc-logger / logs)     (derive_rules.py)       (edit the JSON)       monitor→nudge/block   (hash-chained)
        ^                                                                                    │
        └────────────────────────────  the audit log is the next round's telemetry  ─────────┘
```

## From a cc-logger database (the rich path)

[cc-logger](https://github.com/…/cc-logger) records every tool call — `tool_name`,
`tool_input`, `status`, `error` — into Postgres. That's exactly the substrate a rule
needs. `derive_rules.py` shells out to `psql` (no Python driver required) and runs
[`sql/recurring_failures.sql`](../sql/recurring_failures.sql):

```bash
python3 bin/derive_rules.py --from-cc-logger --days 7 --min-count 3 \
    --db-url "$NEON_CC_LOGGER_URL" --out candidates.rules.json
```

You can also run the query by hand to eyeball the clusters first:

```bash
psql "$NEON_CC_LOGGER_URL" -f sql/recurring_failures.sql
```

## From a JSONL log (zero dependencies)

No cc-logger? Point it at any newline-delimited JSON log of tool calls. A line needs
a tool name, an optional command, and a failure signal (`status: "failure"`, a
non-empty `error`, or `is_error: true`):

```bash
python3 bin/derive_rules.py --from-log tool_calls.jsonl --out candidates.rules.json
```

## What you get

- **Bash failures** cluster by normalized error signature and become a candidate with
  a starting command pattern (`\bpsql\b`, `\bgit\s+push\b`, …). The regex is a
  *draft* — tighten it and write a message that tells the model what to do instead.
- **A whole tool surface that keeps failing** (an MCP server, `WebFetch`) becomes one
  tool-wide candidate. MCP methods roll up to their server (`mcp__Neon__*`), so a
  misconfigured server surfaces as a single signal — this is exactly how the original
  "wrong Neon account" deny rule was found.

Every candidate ships as `action: "monitor"` with `meta` recording the fail count,
the error signature, and the window. Nothing blocks or nudges until you promote it.

## Refine, then promote

1. Open `candidates.rules.json`. For each candidate worth keeping:
   - tighten the `any` regex (the draft matches on the leading command token only),
   - rewrite `message` to say what the model should do instead (add a correct example),
   - decide the action: `nudge` (recoverable) or `block` (irreversible) — see
     [WHEN_TO_USE.md](./WHEN_TO_USE.md).
2. Add a fixture in `tests/fixtures/` for the new rule (the fixture is its spec).
3. Merge it into your `rules/starter.rules.json` (or a repo-specific ruleset you
   point `AGENT_GUARD_RULES` at).

## Automating the review

The derive step is deterministic; the *judgment* (which candidates matter, how to
phrase them) is not. A natural pattern is a weekly agent that runs `derive_rules.py`
against your cc-logger, reasons over the candidates, and proposes rule/system changes
for you to approve. The failure counts tell you where the model keeps tripping — and
sometimes the right fix isn't a guard rule at all, but a helper script or a doc fix
so the failure can't happen in the first place.
