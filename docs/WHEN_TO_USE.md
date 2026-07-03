# When to fail open vs. fail closed

This is the whole idea behind agent-guard. A guard hook sits between the model and
a tool call; the only real question is what it does when a rule matches. Two biases,
and picking the right one per rule is the design work.

## Fail open (nudge) — the default

Inject a reminder as `additionalContext` and let the call proceed. Use this when:

- The action is **recoverable** — a failed command, a fixable mistake, a better
  path exists.
- The rule is **heuristic** — it might false-positive, and blocking a capable model
  that could have self-corrected is worse than a stray reminder.
- You're encoding a **learned failure**, not a safety boundary.

The premise: modern models react to feedback and try something different. Hand them
the signal and get out of the way. Most rules should be nudges.

> Caveat: nudges cost attention. Every injected token competes with the task. Keep
> them terse, keep them rare, and only fire where the correct behavior isn't already
> obvious. An over-eager nudge library trains the model to ignore it.

## Fail closed (block) — the exception

Hard-block with exit 2. Use this only when:

- The action is **irreversible** — data mutation, a destructive delete.
- The check is **deterministic** — a binary "is this a mutation?" with no judgment
  call, so a hard block can't be wrong in a way self-correction would fix.
- The cost of one false negative is **unacceptable**.

A deterministic hook is a better judge here than the model itself: an LLM asked to
review its own action skews positive and can rationalize past its own rules. A hook
can't be talked out of its verdict.

**Precision on "fail closed."** This means the rule blocks *on a match*. It does
not mean the hook process fails closed: on any guard error (bad rules file,
malformed input, a bug) the hook still exits 0 and allows the call, because a
guard must never wedge a session. So a `block` rule is a hard-blocking *backstop*,
not a boundary. For something that truly must not happen (a mutation from a
read-only sub-agent), back the rule with a real boundary — a `SELECT`-only DB
role. See [THREAT_MODEL.md](./THREAT_MODEL.md).

## The decision, in one line

> **Nudge when the model can recover. Block when it can't.**

## `deny` vs `block`

Both stop the call. `deny` (permissionDecision) is the clean, reasoned refusal for
the main session — it hands the model a reason and exits 0. `block` (exit 2) is the
hard stop for a least-privilege sub-agent — it survives a parent's
`bypassPermissions`, which `deny` does not. Use `block` for the read-only backstop;
`deny` for "this tool is the wrong path" in a trusted session.

## `monitor` — the on-ramp

A new rule you're unsure about ships as `monitor`: it logs to the audit trail and
does nothing else. Watch it against real traffic (or run the whole ruleset under
`AGENT_GUARD_DRYRUN=1`), confirm it fires where you expect and nowhere else, then
promote it to `nudge` or `block` by changing one field. This is how a
telemetry-derived candidate (see [TELEMETRY.md](./TELEMETRY.md)) earns its way to
enforcement without ever surprising anyone.

## Guards go stale — prune them

Every rule encodes an assumption about what the model can't do on its own. Those
assumptions expire as models improve. Record `meta.why` and `meta.added` on each
rule, and periodically remove the ones that no longer earn their keep. The rule
library is supposed to shrink over time — that's the tool working, not rotting.
