"""Rule loading + matching + precedence resolution. Pure stdlib, no I/O side effects.

A rule is a dict:
  {
    "id":       "bare-psql-no-target",   # unique, stable
    "tool":     "Bash",                  # fnmatch glob against tool_name ("*" ok)
    "field":    "command",               # tool_input field to match (default "command")
    "any":      ["<regex>", ...],        # fires if ANY matches; omit for a tool-only rule
    "unless":   ["<regex>", ...],        # if ANY matches, the rule does NOT fire (exceptions)
    "flags":    "i",                     # "i" -> re.IGNORECASE (default case-sensitive)
    "norm":     true,                    # collapse \n\t to spaces before matching (default false)
    "severity": 50,                      # higher wins on precedence
    "action":   "nudge",                 # nudge | deny | block | monitor
    "message":  "...",                   # shown to the model (nudge/deny) or operator (block/monitor)
    "meta":     {"why": "...", "added": "YYYY-MM-DD", "telemetry_ref": "..."}
  }

Actions:
  nudge   -> fail-OPEN: inject message as additionalContext, tool proceeds.
  deny    -> block via permissionDecision:deny (exit 0 + JSON); reason given to model.
  block   -> hard block via exit 2 + stderr; survives parent bypassPermissions.
  monitor -> log-only: recorded to the audit trail, never surfaced, tool proceeds.
             (the staging rung for a candidate rule before it graduates to nudge/block.)
"""
import re
import fnmatch

# Higher rank = more restrictive; used to break severity ties.
ACTION_RANK = {"monitor": 0, "nudge": 1, "deny": 2, "block": 3}


def _norm(s):
    return s.replace("\n", " ").replace("\t", " ")


def _flags(rule):
    return re.IGNORECASE if "i" in (rule.get("flags") or "") else 0


def rule_fires(rule, tool_name, tool_input):
    """True iff this rule matches the call. Never raises on a normal rule/input."""
    if not fnmatch.fnmatchcase(tool_name, rule.get("tool", "*")):
        return False

    patterns = rule.get("any")
    if not patterns:
        # Tool-only rule (e.g. deny an entire MCP surface regardless of args).
        return True

    field = rule.get("field", "command")
    val = tool_input.get(field, "")
    if not isinstance(val, str) or not val.strip():
        return False

    text = _norm(val) if rule.get("norm") else val
    flags = _flags(rule)

    if not any(re.search(p, text, flags) for p in patterns):
        return False
    for p in rule.get("unless", []):
        if re.search(p, text, flags):
            return False
    return True


def evaluate(rules, tool_name, tool_input):
    """Return the list of rules that fired, in file order."""
    return [r for r in rules if rule_fires(r, tool_name, tool_input)]


def effective_action(rule, nudge_as_block=False):
    action = rule.get("action", "nudge")
    if nudge_as_block and action == "nudge":
        return "block"
    return action


def resolve(fired, nudge_as_block=False):
    """Collapse the fired rules into a single decision.

    Returns a dict:
      {"decision": "allow"|"nudge"|"deny"|"block",
       "winner": <rule or None>,       # the blocking rule for deny/block
       "notes":  [<message>, ...]}     # nudge messages (for "nudge")
    Precedence (most-restrictive-wins, the safe default for a guard): a more
    restrictive action always beats a less restrictive one — block > deny >
    nudge > monitor — regardless of severity. Severity only breaks ties WITHIN
    an action class (e.g. which block message is the winner). If only monitor
    rules fired, the decision is "allow" (log-only).
    """
    if not fired:
        return {"decision": "allow", "winner": None, "notes": []}

    acting = [r for r in fired if effective_action(r, nudge_as_block) != "monitor"]
    if not acting:
        return {"decision": "allow", "winner": None, "notes": []}

    winner = max(
        acting,
        key=lambda r: (ACTION_RANK[effective_action(r, nudge_as_block)], r.get("severity", 0)),
    )
    waction = effective_action(winner, nudge_as_block)
    if waction in ("deny", "block"):
        return {"decision": waction, "winner": winner, "notes": []}

    notes = [r["message"] for r in acting if effective_action(r, nudge_as_block) == "nudge"]
    return {"decision": "nudge", "winner": None, "notes": notes}


def verify_rules(rules):
    """Static sanity check of a ruleset. Returns a list of problem strings ([] = ok)."""
    problems = []
    seen = set()
    for i, r in enumerate(rules):
        rid = r.get("id")
        if not rid:
            problems.append(f"rule #{i}: missing id")
        elif rid in seen:
            problems.append(f"duplicate id: {rid}")
        else:
            seen.add(rid)
        action = r.get("action", "nudge")
        if action not in ACTION_RANK:
            problems.append(f"{rid}: unknown action {action!r}")
        if action in ("nudge", "deny") and not r.get("message"):
            problems.append(f"{rid}: {action} rule needs a message")
        for p in (r.get("any") or []) + (r.get("unless") or []):
            try:
                re.compile(p)
            except re.error as e:
                problems.append(f"{rid}: bad regex {p!r}: {e}")
    return problems
