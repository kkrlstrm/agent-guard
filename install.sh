#!/usr/bin/env bash
# agent-guard installer — wire the main-session guard into a project's
# .claude/settings.json (idempotent) and optionally drop in the read-only analyst
# sub-agent. Validates existing settings before touching them, makes a timestamped
# backup, prints exactly what it added, and runs doctor to prove it fired.
#
#   ./install.sh /path/to/project              # wire the Bash guard
#   ./install.sh /path/to/project --db-reader  # also copy the read-only sub-agent
#   ./install.sh /path/to/project --uninstall  # remove agent-guard's hook
#
set -euo pipefail

GUARD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${1:-}"
MODE="${2:-}"

if [ -z "$PROJECT" ] || [ ! -d "$PROJECT" ]; then
  echo "usage: ./install.sh /path/to/project [--db-reader | --uninstall]" >&2
  exit 1
fi

SETTINGS="$PROJECT/.claude/settings.json"
HOOK_CMD="python3 \"$GUARD_DIR/pretooluse-guard.py\""

# --- uninstall -------------------------------------------------------------
if [ "$MODE" = "--uninstall" ]; then
  [ -f "$SETTINGS" ] || { echo "no $SETTINGS — nothing to remove"; exit 0; }
  cp "$SETTINGS" "$SETTINGS.bak.$(date +%Y%m%d%H%M%S)"
  python3 - "$SETTINGS" <<'PY'
import json, sys
p = sys.argv[1]
s = json.load(open(p))
pt = s.get("hooks", {}).get("PreToolUse", [])
kept = [e for e in pt
        if not any("pretooluse-guard.py" in h.get("command", "") for h in e.get("hooks", []))]
s.setdefault("hooks", {})["PreToolUse"] = kept
json.dump(s, open(p, "w"), indent=2)
print(f"  removed agent-guard hook(s) from {p}")
PY
  echo "Done. (The db-reader agent, if installed, was left in place.)"
  exit 0
fi

# --- validate existing settings BEFORE writing -----------------------------
mkdir -p "$PROJECT/.claude"
if [ -f "$SETTINGS" ]; then
  if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$SETTINGS" 2>/dev/null; then
    echo "ERROR: $SETTINGS exists but is not valid JSON. Fix it first; not overwriting." >&2
    exit 1
  fi
  cp "$SETTINGS" "$SETTINGS.bak.$(date +%Y%m%d%H%M%S)"
else
  echo '{}' > "$SETTINGS"
fi

python3 - "$SETTINGS" "$HOOK_CMD" <<'PY'
import json, sys
settings_path, hook_cmd = sys.argv[1], sys.argv[2]
s = json.load(open(settings_path))
hooks = s.setdefault("hooks", {}).setdefault("PreToolUse", [])
already = any("pretooluse-guard.py" in h.get("command", "")
             for entry in hooks for h in entry.get("hooks", []))
if already:
    print(f"  agent-guard already wired in {settings_path} (no change)")
else:
    entry = {"matcher": "Bash", "hooks": [{"type": "command", "command": hook_cmd}]}
    hooks.append(entry)
    json.dump(s, open(settings_path, "w"), indent=2)
    print(f"  wired this PreToolUse hook into {settings_path}:")
    for line in json.dumps(entry, indent=2).splitlines():
        print("    " + line)
PY

# --- optional read-only sub-agent ------------------------------------------
if [ "$MODE" = "--db-reader" ]; then
  mkdir -p "$PROJECT/.claude/agents"
  DEST="$PROJECT/.claude/agents/db-reader.md"
  if [ -f "$DEST" ]; then
    echo "  $DEST exists — not overwriting"
  else
    sed "s#__AGENT_GUARD_DIR__#$GUARD_DIR#g" "$GUARD_DIR/agents/db-reader.md" > "$DEST"
    echo "  installed read-only sub-agent -> $DEST (project agent; do NOT ship as a plugin — plugins ignore hooks)"
  fi
fi

# --- prove it works --------------------------------------------------------
echo
echo "Running doctor to verify the wiring..."
python3 "$GUARD_DIR/bin/doctor.py" --project "$PROJECT" || true
echo
echo "Review the settings diff, then commit it."
echo "Point the guard at your own rules with AGENT_GUARD_RULES=/path/to/rules.json"
