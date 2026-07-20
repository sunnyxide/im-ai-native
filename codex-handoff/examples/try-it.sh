#!/usr/bin/env bash
#
# try-it.sh — see codex-handoff actually work, safely.
#
# Spins up three throwaway git repos in a temp directory, each holding a small
# realistic piece of unfinished work, and hands each one to Codex for real.
# Then it shows you the before and after.
#
# Safe by construction:
#   - only ever touches repos it just created under a fresh temp directory
#   - reports go to an isolated state dir, never your real ~/.claude/codex-handoff
#   - never commits, never pushes, never touches a repo you care about
#
# It does make three real Codex calls (roughly 30 seconds each), so it uses a
# little of your Codex quota. That is the point: this proves the handoff works
# end to end, rather than asserting it.
#
# Usage:
#   ./try-it.sh          run all three
#   ./try-it.sh 2        run only scenario 2
#
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CH="$HERE/../codex_handoff.py"
ONLY="${1:-all}"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI not found on PATH. Install it first, then re-run." >&2
  exit 1
fi
if [ ! -f "$CH" ]; then
  echo "Cannot find codex_handoff.py next to this script (looked at $CH)." >&2
  exit 1
fi

WORK="$(mktemp -d)"
export CODEX_HANDOFF_HOME="$WORK/state"
PASSED=0; FAILED=0

echo "Working in $WORK (throwaway — delete it whenever you like)"

newrepo() {
  d="$WORK/$1"; mkdir -p "$d"; cd "$d" || exit 1
  git init -q && git config user.name demo && git config user.email demo@example.com
}

# check <name> <description> <shell test command>
check() {
  if eval "$3" >/dev/null 2>&1; then
    echo "  RESULT: pass — $2"; PASSED=$((PASSED + 1))
  else
    echo "  RESULT: FAIL — $2"; FAILED=$((FAILED + 1))
  fi
}

########################################################################
if [ "$ONLY" = "all" ] || [ "$ONLY" = "1" ]; then
echo
echo "=== 1. Finish an implementation someone left half-done ==="
newrepo one
cat > slugify.py <<'EOF'
def slugify(text):
    """Convert text to a URL slug: lowercase, spaces to hyphens, drop non-alphanumerics."""
    raise NotImplementedError("TODO")
EOF
cat > test_slugify.py <<'EOF'
import unittest
from slugify import slugify

class T(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Hello World"), "hello-world")
    def test_punct(self):
        self.assertEqual(slugify("Cafe & Bar!"), "cafe-bar")
    def test_collapse(self):
        self.assertEqual(slugify("  a   b  "), "a-b")
EOF
git add -A && git commit -qm "baseline"
printf '\n# NOTE: started this, ran out of session before finishing.\n' >> slugify.py

echo "  before: $(python3 -m unittest test_slugify 2>&1 | tail -1)"
python3 "$CH" now --repo "$WORK/one" \
  --task "Implement slugify() in slugify.py so all three tests in test_slugify.py pass. Do NOT modify the tests."
echo "  after:  $(python3 -m unittest test_slugify 2>&1 | tail -1)"
check one "the unfinished function now passes its tests" "python3 -m unittest test_slugify"
fi

########################################################################
if [ "$ONLY" = "all" ] || [ "$ONLY" = "2" ]; then
echo
echo "=== 2. Fix the bug behind a failing test ==="
newrepo two
cat > stats.py <<'EOF'
def median(nums):
    """Return the median. For an even count, the average of the two middle values."""
    s = sorted(nums)
    n = len(s)
    mid = n // 2
    if n % 2 == 0:
        return s[mid]
    return s[mid]
EOF
cat > test_stats.py <<'EOF'
import unittest
from stats import median

class T(unittest.TestCase):
    def test_odd(self):
        self.assertEqual(median([3, 1, 2]), 2)
    def test_even(self):
        self.assertEqual(median([1, 2, 3, 4]), 2.5)
    def test_even_two(self):
        self.assertEqual(median([10, 20]), 15)
EOF
git add -A && git commit -qm "baseline"
printf '\n# debugging: the even-length case looks wrong\n' >> stats.py

echo "  before: $(python3 -m unittest test_stats 2>&1 | tail -1)"
python3 "$CH" now --repo "$WORK/two" \
  --task "test_stats.py fails on the even-length cases. Find and fix the bug in stats.py. Do NOT modify the tests."
echo "  after:  $(python3 -m unittest test_stats 2>&1 | tail -1)"
check two "the bug is fixed and the tests pass" "python3 -m unittest test_stats"
fi

########################################################################
if [ "$ONLY" = "all" ] || [ "$ONLY" = "3" ]; then
echo
echo "=== 3. Change code and docs together, without breaking what worked ==="
newrepo three
cat > cli.py <<'EOF'
import argparse

def build_parser():
    p = argparse.ArgumentParser(description="Greet someone.")
    p.add_argument("name")
    return p

def main():
    args = build_parser().parse_args()
    print(f"Hello, {args.name}!")

if __name__ == "__main__":
    main()
EOF
cat > README.md <<'EOF'
# greet

    python3 cli.py <name>

Prints a greeting.
EOF
git add -A && git commit -qm "baseline"
printf '\n<!-- TODO: document the json output option -->\n' >> README.md

python3 "$CH" now --repo "$WORK/three" \
  --task "Add a --json flag to cli.py that prints the greeting as JSON like {\"greeting\": \"Hello, World!\"} instead of plain text, and document the flag in README.md. Keep the existing plain-text output as the default."
echo "  plain output: $(python3 cli.py World 2>&1)"
echo "  json output:  $(python3 cli.py World --json 2>&1)"
check three "the new flag works AND the old default still works" \
  "python3 cli.py World | grep -q 'Hello, World!' && python3 cli.py World --json | grep -q greeting && grep -q -- '--json' README.md"
fi

########################################################################
echo
echo "=== Reports codex-handoff wrote ==="
find "$CODEX_HANDOFF_HOME/runs" -name report.md 2>/dev/null | while read -r r; do echo "  $r"; done
echo
echo "Passed $PASSED, failed $FAILED."
echo "Open one of the reports above to see what a real handoff report looks like:"
echo "what Codex did, the diff, how to revert it, and how to resume the session."
echo
echo "Throwaway files are in $WORK — remove with: rm -rf $WORK"
[ "$FAILED" -eq 0 ] || exit 1
