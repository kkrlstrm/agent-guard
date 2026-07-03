#!/usr/bin/env bash
# agent-guard installer — wire the main-session guard into a project's
# .claude/settings.json (idempotent) and (optionally) drop in the read-only
# analyst sub-agent. Pure jq/python; makes a timestamped backup before editing.
#
#   ./install.sh /path/to/your/project        # wire the Bash guard
#   ./install.sh /path/to/your/project --db-reader   # also copy the sub-agent
#
# Review the change it prints, then commit it. Uninstall = revert that diff.
set -euo pipefail

GUARD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${1:-}"
WITH_DB_READER="${2:-}"

if [ -z "$PROJECT" ] || [ ! -d "$PROJECT" ]; then
  echo "usage: ./install.sh /path/to/project [--db-reader]" >&2
  exit 1
fi

SETTINGS="$PROJECT/.claude/settings.json"
mkdir -p "$PROJECT/.claude"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
cp "$SETTINGS" "$SETTINGS.bak.$(date +%Y%m%d%H%M%S)"

HOOK_CMD="python3 \"$GUARD_DIR/pretooluse-guard.py\""

python3 - "$SETTINGS" "$HOOK_CMD" <<'PY'
import json, sys
settings_path, hook_cmd = sys.argv[1], sys.argv[2]
with open(settings_path) as f:
    s = json.load(f)
hooks = s.setdefault("hooks", {}).setdefault("PreToolUse", [])
# Skip if an agent-guard hook is already wired.
already = any("pretooluse-guard.py" in h.get("command", "")
             for entry in hooks for h in entry.get("hooks", []))
if not already:
    hooks.append({"matcher": "Bash",
                  "hooks": [{"type": "command", "command": hook_cmd}]})
    with open(settings_path, "w") as f:
        json.dump(s, f, indent=2)
    print(f"  wired Bash guard into {settings_path}")
else:
    print(f"  agent-guard already wired in {settings_path} (no change)")
PY

if [ "$WITH_DB_READER" = "--db-reader" ]; then
  mkdir -p "$PROJECT/.claude/agents"
  DEST="$PROJECT/.claude/agents/db-reader.md"
  if [ -f "$DEST" ]; then
    echo "  $DEST exists — not overwriting"
  else
    sed "s#__AGENT_GUARD_DIR__#$GUARD_DIR#g" "$GUARD_DIR/agents/db-reader.md" > "$DEST"
    echo "  installed read-only sub-agent -> $DEST"
  fi
fi

echo "Done. Review the settings diff, then commit it."
echo "Point the guard at your own rules with AGENT_GUARD_RULES=/path/to/rules.json"
