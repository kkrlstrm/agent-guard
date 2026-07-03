# Threat model

Be precise about what this is. agent-guard is a **runtime control layer** for
Claude Code, not a complete security boundary. It's honest about the line, and
knowing where that line is *is* the feature.

## What agent-guard protects against

- **Obvious destructive Bash commands** — `rm -rf /`, force-pushing a protected
  branch, piping a remote script into a shell. Blocked or nudged at the tool
  boundary before they run.
- **Recurring, recoverable tool-use failures** — the same broken command shape
  the model keeps retrying. A nudge hands it the fix so it self-corrects instead
  of burning turns. These come from your own telemetry (see
  [TELEMETRY.md](./TELEMETRY.md)).
- **Accidental DB mutations by a read-only analyst sub-agent** — the read-only
  backstop hard-blocks `INSERT`/`UPDATE`/`DELETE`/DDL/DCL from a sub-agent that's
  only supposed to read.
- **Tool-boundary policy violations that are visible in the hook input** — if the
  dangerous intent is in the command string or a matchable argument, a rule can
  catch it.
- **Secrets pasted inline into commands** — nudged before they land in shell
  history or logs (and the audit log itself stores only a hash + a redacted
  preview, never the raw command).

## What agent-guard does NOT protect against

- **Write-capable credentials.** The read-only backstop is regex over a command
  string. It's a backstop, not a boundary. If the sub-agent holds a DB role that
  can write, a sufficiently creative command gets through. The durable guarantee
  is a `SELECT`-only role whose environment never holds write creds — config-bound
  beats parse-bound.
- **Dynamically generated SQL / indirection.** A shell or Python script that
  builds SQL at runtime, `EXECUTE 'DROP ...'`, a `WITH ... (DELETE ... RETURNING)`
  CTE, or a write hidden behind a function call can evade regex. Obfuscation
  (`DEL/**/ETE`, string concatenation, escapes) can too.
- **Prompt injection that abuses an *allowed* path.** If reading data is permitted
  and the model is manipulated into exfiltrating it through an allowed tool, a
  content-blind rule won't see that.
- **A compromised local machine.** The guard, its rules, and its audit log all
  live on the same box. An attacker with local write access can edit rules, delete
  the audit log, or unwire the hook.
- **Disabled or unwired hooks.** If the `PreToolUse` hook isn't installed (or the
  rules file is broken, so the guard falls open), nothing fires. Run
  `python3 bin/doctor.py --project <dir>` to confirm it's actually wired.
- **Plugin-packaged sub-agents.** Claude Code ignores `hooks:` on sub-agents
  loaded from a plugin, so the read-only backstop silently won't fire there.
  Install the agent under `.claude/agents/`, never as a plugin (see
  [GOTCHAS.md](./GOTCHAS.md)).

## On the audit log

The hash-chained log is **tamper-evident for local review**: if someone edits an
earlier line in place, `guard/audit.py verify` catches it. It is **not** a
compliance vault. An attacker with write access to the file can delete it or
rewrite the whole chain from genesis, because the head hash isn't anchored
anywhere external. If you need stronger guarantees, ship each line (or the head
hash) to an append-only external sink. agent-guard doesn't claim more than local
tamper-evidence.

## The honest one-liner

> agent-guard provides a deterministic local hook layer that blocks obvious
> dangerous tool calls, nudges recurring recoverable failures, and backs up a
> proper least-privilege credential boundary. It does not replace that boundary.
