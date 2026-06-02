#!/usr/bin/env bash
# Rebuild the self-contained python-audit.skill: sync the CLI package into the
# skill's bundled copy, then zip it. Run this after changing the CLI so the
# distributed skill stays in step with the source of truth (this repo).
#
# Usage:  scripts/build-skill.sh [path-to-skill-dir]
#   default skill dir: ~/.claude/skills/python-audit
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL="${1:-$HOME/.claude/skills/python-audit}"

if [[ ! -f "$SKILL/SKILL.md" ]]; then
  echo "[ERROR] no SKILL.md at $SKILL — pass the skill dir as arg 1" >&2
  exit 1
fi

# sync the package (single source of truth = this repo) into the bundled copy
mkdir -p "$SKILL/scripts/pyhygiene"
cp "$REPO"/pyhygiene/*.py "$SKILL/scripts/pyhygiene/"
rm -rf "$SKILL/scripts/pyhygiene/__pycache__"

OUT="$REPO/python-audit.skill"
rm -f "$OUT"
( cd "$(dirname "$SKILL")" && zip -rq "$OUT" "$(basename "$SKILL")" \
    -x '*/evals/*' -x '*__pycache__*' -x '*.pyc' )

echo "built: $OUT"
python3 -c "import zipfile; print('\n'.join('  '+n for n in zipfile.ZipFile('$OUT').namelist()))"
