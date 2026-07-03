#!/usr/bin/env python3
"""derive_rules.py — turn recurring tool-failures into candidate guard rules.

The telemetry-feedback half of agent-guard's loop: a failure that keeps happening
is a job the model can't do reliably on its own yet — i.e. a candidate for a
nudge. This tool surfaces those clusters and emits *candidate `monitor` rules*
(log-only, never auto-armed). A human reviews them, refines the regex + message,
and promotes monitor -> nudge/block. See docs/TELEMETRY.md.

Two input modes (both stdlib — cc-logger mode shells out to `psql`, no driver):

  # From a cc-logger Postgres DB (the rich path):
  python3 bin/derive_rules.py --from-cc-logger --days 7 --min-count 3 \
      --db-url "$NEON_CC_LOGGER_URL" --out candidates.rules.json

  # From a JSONL log of tool calls (zero-dependency path):
  python3 bin/derive_rules.py --from-log tool_calls.jsonl --out candidates.rules.json

A log line is any JSON object with a tool name, (optional) command, and a failure
signal — flexible keys: tool_name/tool, tool_input.command/command, and
status=="failure"/error/is_error. Non-failures are ignored.
"""
import os
import re
import sys
import json
import argparse
import hashlib
import subprocess
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SQL_FILE = os.path.join(REPO, "sql", "recurring_failures.sql")

# Command multiplexers where the 2nd token carries the real meaning.
MULTIPLEXERS = {"git", "npm", "pnpm", "yarn", "docker", "kubectl", "cargo", "go",
                "pip", "pip3", "python", "python3", "psql", "aws", "gcloud", "make",
                "brew", "apt", "apt-get", "systemctl", "launchctl"}


# --------------------------------------------------------------------------- #
# Normalization (mirrors sql/recurring_failures.sql so both modes cluster alike)
# --------------------------------------------------------------------------- #
def normalize_error(err):
    s = (err or "").lower()
    s = re.sub(r"'[^']*'|\"[^\"]*\"", "'S'", s)
    s = re.sub(r"[0-9]+", "#", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:200]


def _first_tokens(command):
    toks = [t for t in re.split(r"\s+", (command or "").strip()) if t]
    if not toks:
        return []
    head = os.path.basename(toks[0])
    # skip leading ENV=val assignments
    i = 0
    while i < len(toks) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[i]):
        i += 1
    if i >= len(toks):
        return []
    head = os.path.basename(toks[i])
    if head in MULTIPLEXERS and i + 1 < len(toks) and not toks[i + 1].startswith("-"):
        return [head, toks[i + 1]]
    return [head]


def _candidate_pattern(command):
    toks = _first_tokens(command)
    if not toks:
        return None
    return r"\b" + r"\s+".join(re.escape(t) for t in toks) + r"\b"


def _tool_group(tool):
    """Group a non-Bash tool for aggregation. MCP tools (mcp__Server__method)
    collapse to their server surface (mcp__Server__*); everything else groups by
    exact name. Returns (group_key, tool_glob)."""
    if tool.startswith("mcp__"):
        parts = tool.split("__")
        if len(parts) >= 3 and parts[1]:
            return (f"mcp__{parts[1]}__", f"mcp__{parts[1]}__*")
    return (tool, tool)


def _rule_id(tool, tokens, signature):
    base = (tool + "-" + "-".join(tokens)) if tokens else tool
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    h = hashlib.sha256(signature.encode()).hexdigest()[:6]
    return f"derived-{base}-{h}"


# --------------------------------------------------------------------------- #
# Core: rows -> candidate rules
# --------------------------------------------------------------------------- #
def derive_from_rows(rows, window_days, min_count=3, added=None):
    """rows: list of dicts with tool_name, error_signature, fail_count,
    sample_command, sample_error (+ optional first_seen/last_seen).
    Returns a list of candidate rule dicts (all action=monitor).

    Bash rows become per-signature command-pattern candidates (the command shape
    is what matters). Non-Bash tools (MCP servers, WebFetch, …) are aggregated
    into ONE tool-wide candidate — a whole tool that keeps failing is a candidate
    to deny/monitor at the tool surface, regardless of the exact error. The
    min_count threshold is applied here (per-signature for Bash, per-tool total
    for others) so tool-wide signals can form from sub-threshold signatures."""
    from collections import defaultdict
    added = added or date.today().isoformat()
    candidates = []

    bash_rows = [r for r in rows if (r.get("tool_name") or "Bash") == "Bash"]
    other_rows = [r for r in rows if (r.get("tool_name") or "Bash") != "Bash"]

    for r in bash_rows:
        sig = r.get("error_signature") or normalize_error(r.get("sample_error"))
        count = int(r.get("fail_count", 0))
        if count < min_count:
            continue
        cmd = r.get("sample_command")
        tokens = _first_tokens(cmd) if cmd else []
        rule = {
            "id": _rule_id("Bash", tokens, sig + str(count)),
            "tool": "Bash",
            "severity": 40,
            "action": "monitor",
            "message": (
                f"DRAFT — recurring Bash failure ({count}x in {window_days}d): "
                f"\"{(r.get('sample_error') or sig)[:160]}\". "
                "Refine this pattern + write a helpful message, then promote monitor -> nudge/block."
            ),
            "meta": {
                "why": f"{count} logged failures in {window_days}d; auto-surfaced by derive_rules.",
                "added": added, "telemetry_ref": "cc-logger",
                "fail_count": count, "error_signature": sig, "sample_command": cmd,
            },
        }
        if cmd:
            pat = _candidate_pattern(cmd)
            if pat:
                rule["any"] = [pat]
                rule["field"] = "command"
        candidates.append(rule)

    by_tool = defaultdict(list)
    for r in other_rows:
        by_tool[_tool_group(r["tool_name"])].append(r)
    for (group_key, tool_glob), rs in sorted(by_tool.items(), key=lambda kv: -sum(int(x.get("fail_count", 0)) for x in kv[1])):
        total = sum(int(x.get("fail_count", 0)) for x in rs)
        if total < min_count:
            continue
        sample = rs[0].get("sample_error") or rs[0].get("error_signature") or ""
        candidates.append({
            "id": _rule_id(tool_glob, [], group_key + str(total)),
            "tool": tool_glob,
            "severity": 60,
            "action": "monitor",
            "message": (
                f"DRAFT — tool surface '{tool_glob}' failed {total}x in {window_days}d across "
                f"{len(rs)} method/signature(s), e.g. \"{sample[:140]}\". If this tool is "
                "unreliable/misconfigured, consider action=deny with a pointer to the working path."
            ),
            "meta": {
                "why": f"{total} logged failures in {window_days}d on {tool_glob}; auto-surfaced by derive_rules.",
                "added": added, "telemetry_ref": "cc-logger",
                "fail_count": total, "distinct_signatures": len(rs),
            },
        })
    return candidates


# --------------------------------------------------------------------------- #
# Input mode: cc-logger (via psql)
# --------------------------------------------------------------------------- #
def load_rows_from_cc_logger(db_url, days, min_count):
    with open(SQL_FILE) as f:
        sql = f.read()
    # Parameterize the bundled query.
    sql = re.sub(r"interval '\d+ days'", f"interval '{int(days)} days'", sql)
    sql = re.sub(r"HAVING count\(\*\) >= \d+", f"HAVING count(*) >= {int(min_count)}", sql)
    sql = sql.strip().rstrip(";")
    wrapped = f"SELECT coalesce(json_agg(t), '[]') FROM (\n{sql}\n) t"
    proc = subprocess.run(
        ["psql", db_url, "-tAX", "-c", wrapped],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout.strip() or "[]")


# --------------------------------------------------------------------------- #
# Input mode: JSONL tool-call log (zero-dep)
# --------------------------------------------------------------------------- #
def _looks_failed(obj):
    status = (obj.get("status") or "").lower()
    if status:
        return status == "failure"
    if obj.get("is_error") is True:
        return True
    err = obj.get("error")
    return bool(err) and str(err).strip() != ""


def load_rows_from_log(path, days, min_count):
    from collections import defaultdict
    clusters = defaultdict(lambda: {"fail_count": 0, "sample_command": None, "sample_error": None})
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not _looks_failed(obj):
                continue
            tool = obj.get("tool_name") or obj.get("tool") or "Bash"
            ti = obj.get("tool_input") or {}
            cmd = ti.get("command") if isinstance(ti, dict) else None
            cmd = cmd or obj.get("command")
            err = obj.get("error") or obj.get("tool_response") or ""
            if isinstance(err, (dict, list)):
                err = json.dumps(err)[:300]
            sig = normalize_error(str(err))
            key = (tool, sig)
            c = clusters[key]
            c["fail_count"] += 1
            if c["sample_command"] is None and cmd:
                c["sample_command"] = cmd
            if c["sample_error"] is None and err:
                c["sample_error"] = str(err)[:300]
    # Return ALL clusters (no threshold here) so derive_from_rows can aggregate
    # sub-threshold signatures into tool-wide signals before thresholding.
    rows = [{"tool_name": tool, "error_signature": sig, **c} for (tool, sig), c in clusters.items()]
    rows.sort(key=lambda r: r["fail_count"], reverse=True)
    return rows


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Derive candidate agent-guard rules from tool-failure telemetry.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-cc-logger", action="store_true", help="query a cc-logger Postgres DB via psql")
    src.add_argument("--from-log", metavar="JSONL", help="read a JSONL log of tool calls")
    ap.add_argument("--db-url", help="Postgres URL (default $NEON_CC_LOGGER_URL or $DATABASE_URL)")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--min-count", type=int, default=3)
    ap.add_argument("--out", help="write candidate ruleset JSON to this file")
    ap.add_argument("--json", action="store_true", help="print the full candidate ruleset JSON to stdout")
    args = ap.parse_args()

    if args.from_cc_logger:
        db_url = args.db_url or os.environ.get("NEON_CC_LOGGER_URL") or os.environ.get("DATABASE_URL")
        if not db_url:
            ap.error("no DB url (pass --db-url or set NEON_CC_LOGGER_URL / DATABASE_URL)")
        rows = load_rows_from_cc_logger(db_url, args.days, args.min_count)
    else:
        rows = load_rows_from_log(args.from_log, args.days, args.min_count)

    candidates = derive_from_rows(rows, args.days, min_count=args.min_count)
    ruleset = {
        "ruleset": "derived-candidates",
        "bias": "fail-open",
        "description": (
            f"Auto-derived candidate rules from {'cc-logger' if args.from_cc_logger else args.from_log} "
            f"({len(candidates)} clusters, >= {args.min_count} fails in {args.days}d). "
            "All action=monitor (log-only). Review, refine the regex + message, then promote."
        ),
        "rules": candidates,
    }

    if args.out:
        with open(args.out, "w") as f:
            json.dump(ruleset, f, indent=2)
        sys.stderr.write(f"wrote {len(candidates)} candidate rule(s) -> {args.out}\n")

    if args.json or not args.out:
        # Default to a compact human summary unless --json asked for the full thing.
        if args.json:
            print(json.dumps(ruleset, indent=2))
        else:
            if not candidates:
                print(f"No recurring failures (>= {args.min_count} in {args.days}d). Nothing to propose.")
            else:
                print(f"{len(candidates)} candidate rule(s) from recurring failures "
                      f"(>= {args.min_count} in {args.days}d):\n")
                for r in candidates:
                    m = r["meta"]
                    print(f"  [{m['fail_count']:>3}x] {r['tool']:<16} {r.get('any', ['(tool-wide)'])[0]}")
                    detail = m.get("error_signature") or f"{m.get('distinct_signatures', '?')} distinct signature(s)"
                    print(f"         {detail[:100]}")
                print("\nRe-run with --out <file> to write the candidate monitor ruleset.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
