# Gotchas

Things that will silently weaken or disable a guard if you don't know about them.

## The read-only firewall is a backstop, not a boundary

`validate-readonly.py` matches SQL with regex. Regex can be fooled — string
concatenation, comments (`DEL/**/ETE`), unusual whitespace, dynamic SQL. Treat it as
the cheap outer layer. **The real guarantee is a `SELECT`-only database role** whose
environment never contains write-capable credentials. Give the sub-agent that role
and the regex becomes belt-and-suspenders instead of the only thing standing between
a prompt injection and your data. Config-bound beats parse-bound: a sub-agent that
physically holds read-only creds can't be talked into escalating.

## Plugin sub-agents ignore `hooks:`

For security reasons, sub-agents loaded from a **plugin** ignore the `hooks`,
`mcpServers`, and `permissionMode` frontmatter fields. If you ship the `db-reader`
agent inside a plugin, **the firewall silently does not fire.** Install it as a
project (`.claude/agents/`) or user (`~/.claude/agents/`) agent instead.

## `bypassPermissions` overrides permission modes — but not exit 2

If a parent session runs with `bypassPermissions` (or `acceptEdits`), that overrides
a child sub-agent's permission mode. Permission-based defenses don't hold there. The
**exit-2 hard block still holds**, which is the whole reason the firewall uses a hook
that exits 2 rather than a permission setting. Use `block` (not `deny`) for anything
that must survive a bypass-mode parent.

## Nudges cost attention

Every nudge injected as `additionalContext` spends tokens the model could be using on
the task. Symptoms of overdoing it: the model starts ignoring reminders, or its
answers get worse near long contexts. Keep nudges terse, de-duplicate them, and only
fire where the correct behavior isn't already obvious. If a rule fires on almost every
call, it's probably wrong or too broad.

## Fail-open means a broken guard lets everything through

By design, any error — malformed hook JSON, a missing or broken rules file, a bad
regex — makes the guard exit 0 (allow). A guard bug must never wedge a session. The
tradeoff: a syntactically broken `rules.json` disables your guards silently. Guard
against it by running the test suite in CI (it validates every shipped ruleset) and
by keeping `verify_rules()` green.

## Latency: the hook runs on every matching call

A `PreToolUse` hook fires on every tool call it matches. Keep it fast — the shipped
engine is pure regex over one string and runs in single-digit milliseconds. If you
add rules, avoid catastrophic backtracking in your regexes; a slow rule is a bug even
if it's correct.

## The audit log is best-effort

Writing a verdict to the audit log is wrapped in a try/except — if the disk is full
or the path is unwritable, the tool call still proceeds. The log is for review and
tamper-evidence, never a gate. Verify its integrity with
`python3 guard/audit.py verify`.
