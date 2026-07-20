#!/usr/bin/env bash
#
# benchmark.sh — 10-scenario reproducible benchmark for codex-handoff.
#
# Same machinery as try-it.sh, scaled up and measured. Spins up ten throwaway
# git repos under a single temp directory, each holding a small piece of
# unfinished work with an automated pass/fail check written BEFORE the run,
# hands each one to Codex for real via `codex_handoff.py now`, then reports
# what actually happened.
#
# Difficulty is graded on purpose: scenarios 1-3 are easy (expected to pass),
# 4-7 are medium, 8-10 are hard and may genuinely fail. A benchmark reports,
# it doesn't gate — this script always exits 0.
#
# Safe by construction:
#   - only ever touches repos it just created under a fresh temp directory
#   - reports go to an isolated CODEX_HANDOFF_HOME, never your real
#     ~/.claude/codex-handoff
#   - never commits, never pushes, never touches a repo you care about
#
# It makes ten real Codex calls (roughly 30s each, so a full run takes a few
# minutes). That is the point: this measures the handoff working end to end,
# rather than asserting it.
#
# Usage:
#   ./benchmark.sh          run all 10 scenarios
#   ./benchmark.sh 8        run only scenario 8
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
mkdir -p "$WORK/checks" "$WORK/logs"

PASSED=0
TOTAL=0
TIMES=()

echo "Working in $WORK (throwaway — delete it whenever you like)"
echo "CODEX_HANDOFF_HOME=$CODEX_HANDOFF_HOME"

newrepo() {
  d="$WORK/$1"
  mkdir -p "$d" && cd "$d" || exit 1
  git init -q && git config user.name demo && git config user.email demo@example.com
}

# handoff <repo-dir> <task text...>
handoff() {
  local dir="$1" task="$2" logfile="$3"
  python3 "$CH" now --repo "$dir" --task "$task" >"$logfile" 2>&1
}

# check <name> <elapsed-seconds> <shell test command> <logfile>
check() {
  local name="$1" elapsed="$2" cmd="$3" logfile="$4"
  TOTAL=$((TOTAL + 1))
  TIMES+=("$elapsed")
  if eval "$cmd" >/dev/null 2>&1; then
    printf '%-24s | %-4s | %ss\n' "$name" "pass" "$elapsed"
    PASSED=$((PASSED + 1))
  else
    printf '%-24s | %-4s | %ss\n' "$name" "FAIL" "$elapsed"
    outcome="$(grep -m1 '^codex handoff' "$logfile" 2>/dev/null || echo '(no codex-handoff outcome line found)')"
    echo "  -> $outcome"
  fi
}

printf '%-24s | %-4s | %s\n' "scenario" "res." "time"
printf -- '-------------------------------------------\n'

########################################################################
# 1. stub-fn — implement a stubbed function against given tests (easy)
if [ "$ONLY" = "all" ] || [ "$ONLY" = "1" ]; then
newrepo stub-fn
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

SECONDS=0
handoff "$WORK/stub-fn" \
  "Implement slugify() in slugify.py so all three tests in test_slugify.py pass. Do NOT modify the tests." \
  "$WORK/logs/stub-fn.log"
check stub-fn "$SECONDS" "python3 -m unittest test_slugify" "$WORK/logs/stub-fn.log"
fi

########################################################################
# 2. logic-bug — fix a bug a test already exposes (easy)
if [ "$ONLY" = "all" ] || [ "$ONLY" = "2" ]; then
newrepo logic-bug
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

SECONDS=0
handoff "$WORK/logic-bug" \
  "test_stats.py fails on the even-length cases. Find and fix the bug in stats.py. Do NOT modify the tests." \
  "$WORK/logs/logic-bug.log"
check logic-bug "$SECONDS" "python3 -m unittest test_stats" "$WORK/logs/logic-bug.log"
fi

########################################################################
# 3. flag-and-docs — add a CLI flag and document it, keep default behavior (easy)
if [ "$ONLY" = "all" ] || [ "$ONLY" = "3" ]; then
newrepo flag-and-docs
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

SECONDS=0
handoff "$WORK/flag-and-docs" \
  'Add a --json flag to cli.py that prints the greeting as JSON like {"greeting": "Hello, World!"} instead of plain text, and document the flag in README.md. Keep the existing plain-text output as the default.' \
  "$WORK/logs/flag-and-docs.log"
check flag-and-docs "$SECONDS" \
  "python3 cli.py World | grep -qx 'Hello, World!' && python3 cli.py World --json | python3 -c \"import json,sys; d=json.loads(sys.stdin.read()); assert d.get('greeting')=='Hello, World!'\" && grep -q -- '--json' README.md" \
  "$WORK/logs/flag-and-docs.log"
fi

########################################################################
# 4. write-tests — add validation AND write the tests for it (medium)
if [ "$ONLY" = "all" ] || [ "$ONLY" = "4" ]; then
newrepo write-tests
cat > port.py <<'EOF'
def parse_port(s):
    """Parse a port number from a string. Currently does no validation."""
    return int(s)
EOF
git add -A && git commit -qm "baseline"
printf '\n# TODO: add validation + tests, have not started\n' >> port.py

cat > "$WORK/checks/write-tests.py" <<'PYEOF'
import glob, importlib, subprocess, sys

try:
    port = importlib.import_module("port")
except Exception as e:
    print("import failed:", e)
    sys.exit(1)

ok = True
try:
    if port.parse_port("8080") != 8080:
        ok = False
except Exception:
    ok = False

for bad in ("abc", "0", "70000", "-5", "3.5", ""):
    try:
        port.parse_port(bad)
        ok = False  # should have raised
    except Exception:
        pass

test_files = [
    f for f in glob.glob("test_*.py") + glob.glob("*_test.py")
    if "parse_port" in open(f, encoding="utf-8").read()
]
if not test_files:
    ok = False
else:
    # The task never said which test framework to use, so accept either.
    # (The first published run scored this scenario FAIL because this check
    # only ran unittest while Codex had written valid pytest-style tests.)
    mod_names = [f[:-3] for f in test_files]
    r = subprocess.run([sys.executable, "-m", "unittest", *mod_names], capture_output=True, text=True)
    collected_nothing = "Ran 0 tests" in (r.stdout + r.stderr)
    if r.returncode != 0 or collected_nothing:
        r2 = subprocess.run([sys.executable, "-m", "pytest", "-q", *test_files], capture_output=True, text=True)
        if r2.returncode != 0:
            ok = False
            if "No module named pytest" in (r2.stdout + r2.stderr):
                print("tests look pytest-style but pytest is not installed; cannot verify")

sys.exit(0 if ok else 1)
PYEOF

SECONDS=0
handoff "$WORK/write-tests" \
  "parse_port(s) in port.py currently does no input validation - int(s) will accept garbage and out-of-range values. Make it reject non-integer strings and integers outside the valid TCP port range (1-65535) by raising a ValueError with a clear message, and keep valid ports (1-65535) working as before. Then add a test file that covers: a valid port parsing correctly, a non-integer string raising, and out-of-range integers (too low and too high) raising." \
  "$WORK/logs/write-tests.log"
check write-tests "$SECONDS" "(cd '$WORK/write-tests' && python3 - < '$WORK/checks/write-tests.py')" "$WORK/logs/write-tests.log"
fi

########################################################################
# 5. refactor-no-regression — dedupe a 3x-duplicated block, keep behavior (medium)
if [ "$ONLY" = "all" ] || [ "$ONLY" = "5" ]; then
newrepo refactor-no-regression
cat > discounts.py <<'EOF'
def apply_discount(price):
    # NORMALIZE_STEP: clamp and round the price
    if price < 0:
        price = 0
    price = round(price * 0.9, 2)
    if price > 1_000_000:
        price = 1_000_000
    return price


def apply_bulk_discount(price, qty):
    # NORMALIZE_STEP: clamp and round the price
    if price < 0:
        price = 0
    price = round(price * 0.9, 2)
    if price > 1_000_000:
        price = 1_000_000
    return price * qty


def preview_discount(price):
    # NORMALIZE_STEP: clamp and round the price
    if price < 0:
        price = 0
    price = round(price * 0.9, 2)
    if price > 1_000_000:
        price = 1_000_000
    return f"${price}"
EOF
cat > test_discounts.py <<'EOF'
import unittest
from discounts import apply_discount, apply_bulk_discount, preview_discount

class T(unittest.TestCase):
    def test_apply(self):
        self.assertEqual(apply_discount(100), 90.0)
    def test_bulk(self):
        self.assertEqual(apply_bulk_discount(100, 3), 270.0)
    def test_preview(self):
        self.assertEqual(preview_discount(100), "$90.0")
    def test_negative_clamped(self):
        self.assertEqual(apply_discount(-50), 0.0)
EOF
git add -A && git commit -qm "baseline"
printf '\n# TODO: this block is copy-pasted 3x, extract it\n' >> discounts.py

SECONDS=0
handoff "$WORK/refactor-no-regression" \
  "The 6-line block marked with the NORMALIZE_STEP comment is duplicated identically in apply_discount, apply_bulk_discount, and preview_discount in discounts.py. Extract it into one shared helper so the block (and its NORMALIZE_STEP comment) appears exactly once in the source, without changing the observable behavior of any of the three functions. Do NOT modify test_discounts.py." \
  "$WORK/logs/refactor-no-regression.log"
check refactor-no-regression "$SECONDS" \
  "python3 -m unittest test_discounts && [ \"\$(grep -c NORMALIZE_STEP discounts.py)\" -eq 1 ]" \
  "$WORK/logs/refactor-no-regression.log"
fi

########################################################################
# 6. cross-file-trace — bug is in a file the failing test never imports directly (medium)
if [ "$ONLY" = "all" ] || [ "$ONLY" = "6" ]; then
newrepo cross-file-trace
cat > formatting.py <<'EOF'
def format_currency(cents):
    """Format an integer number of cents as a dollar string, e.g. 1050 -> '$10.50'."""
    dollars = cents // 100
    remainder = cents % 100
    return f"${dollars}.{remainder}"
EOF
cat > report.py <<'EOF'
from formatting import format_currency

def build_line_item(name, cents):
    return f"{name}: {format_currency(cents)}"

def build_report(items):
    lines = [build_line_item(name, cents) for name, cents in items]
    return "\n".join(lines)
EOF
cat > test_report.py <<'EOF'
import unittest
from report import build_report, build_line_item

class T(unittest.TestCase):
    def test_line_item(self):
        self.assertEqual(build_line_item("Widget", 1005), "Widget: $10.05")
    def test_report(self):
        out = build_report([("Widget", 1005), ("Gadget", 250)])
        self.assertIn("$10.05", out)
        self.assertIn("$2.50", out)
EOF
git add -A && git commit -qm "baseline"
printf '\n# NOTE: test_report.py is failing, investigating\n' >> report.py

SECONDS=0
handoff "$WORK/cross-file-trace" \
  "test_report.py is failing. Find and fix the underlying bug. Do NOT modify test_report.py." \
  "$WORK/logs/cross-file-trace.log"
check cross-file-trace "$SECONDS" \
  "python3 -m unittest test_report && git diff HEAD --name-only | grep -qx formatting.py" \
  "$WORK/logs/cross-file-trace.log"
fi

########################################################################
# 7. finish-class — implement two methods against a docstring contract + given tests (medium)
if [ "$ONLY" = "all" ] || [ "$ONLY" = "7" ]; then
newrepo finish-class
cat > ledger.py <<'EOF'
class Ledger:
    """A simple append-only transaction ledger.

    add(amount, note="") -- record a transaction (amount can be positive or
        negative) and return nothing.
    balance() -- return the sum of all recorded amounts (0 if none).
    history() -- return a list of (amount, note) tuples in the order they
        were added.
    """

    def __init__(self):
        self._entries = []

    def add(self, amount, note=""):
        self._entries.append((amount, note))

    def balance(self):
        raise NotImplementedError("TODO")

    def history(self):
        raise NotImplementedError("TODO")
EOF
cat > test_ledger.py <<'EOF'
import unittest
from ledger import Ledger

class T(unittest.TestCase):
    def test_balance_empty(self):
        self.assertEqual(Ledger().balance(), 0)
    def test_balance_sum(self):
        l = Ledger()
        l.add(100)
        l.add(-30, "refund")
        self.assertEqual(l.balance(), 70)
    def test_history_order(self):
        l = Ledger()
        l.add(5, "a")
        l.add(10, "b")
        self.assertEqual(l.history(), [(5, "a"), (10, "b")])
    def test_history_empty(self):
        self.assertEqual(Ledger().history(), [])
EOF
git add -A && git commit -qm "baseline"
printf '\n    # TODO: balance() and history() still raise NotImplementedError\n' >> ledger.py

SECONDS=0
handoff "$WORK/finish-class" \
  "Implement Ledger.balance() and Ledger.history() in ledger.py per the contract described in the class docstring. Do NOT modify add() or test_ledger.py." \
  "$WORK/logs/finish-class.log"
check finish-class "$SECONDS" "python3 -m unittest test_ledger" "$WORK/logs/finish-class.log"
fi

########################################################################
# 8. mutable-default — classic Python footgun, a test exposes the leak (hard)
if [ "$ONLY" = "all" ] || [ "$ONLY" = "8" ]; then
newrepo mutable-default
cat > collector.py <<'EOF'
def collect(item, into=[]):
    """Append item to a list and return it. Each call made with no `into`
    given should start from a fresh empty list -- it must NOT remember items
    from previous calls."""
    into.append(item)
    return into
EOF
cat > test_collector.py <<'EOF'
import unittest
from collector import collect

class T(unittest.TestCase):
    def test_fresh_each_call(self):
        self.assertEqual(collect("a"), ["a"])
        self.assertEqual(collect("b"), ["b"])
    def test_explicit_list_still_works(self):
        bucket = []
        collect("x", into=bucket)
        collect("y", into=bucket)
        self.assertEqual(bucket, ["x", "y"])
EOF
git add -A && git commit -qm "baseline"
printf '\n# BUG: collect() seems to remember old items between calls?\n' >> collector.py

cat > "$WORK/checks/mutable-default.py" <<'PYEOF'
import ast, subprocess, sys

r = subprocess.run([sys.executable, "-m", "unittest", "test_collector"], capture_output=True, text=True)
tests_ok = (r.returncode == 0)

tree = ast.parse(open("collector.py", encoding="utf-8").read())
mutable_default = False
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for default in list(node.args.defaults) + list(node.args.kw_defaults):
            if default is not None and isinstance(default, (ast.List, ast.Dict, ast.Set)):
                mutable_default = True

sys.exit(0 if (tests_ok and not mutable_default) else 1)
PYEOF

SECONDS=0
handoff "$WORK/mutable-default" \
  "test_collector.py's test_fresh_each_call is failing because collect() in collector.py leaks state between calls via a mutable default argument. Fix collect() so each call made without an explicit into= starts from a fresh empty list, while collect(item, into=some_list) still appends to the list the caller passed in. Do NOT modify test_collector.py." \
  "$WORK/logs/mutable-default.log"
check mutable-default "$SECONDS" "(cd '$WORK/mutable-default' && python3 - < '$WORK/checks/mutable-default.py')" "$WORK/logs/mutable-default.log"
fi

########################################################################
# 9. two-file-consistency — two files must change together (hard)
if [ "$ONLY" = "all" ] || [ "$ONLY" = "9" ]; then
newrepo two-file-consistency
cat > schema.py <<'EOF'
# Bump this whenever the on-disk record format changes.
SCHEMA_VERSION = 1
EOF
cat > codec.py <<'EOF'
from schema import SCHEMA_VERSION

def serialize(record):
    """Serialize record dict -> 'v<SCHEMA_VERSION>|k=v|k=v|...'."""
    parts = "|".join(f"{k}={v}" for k, v in record.items())
    return f"v{SCHEMA_VERSION}|{parts}"

def deserialize(s):
    """Inverse of serialize(). Raises ValueError on a version mismatch."""
    head, _, rest = s.partition("|")
    if head != f"v{SCHEMA_VERSION}":
        raise ValueError(f"unsupported schema version: {head!r}, expected v{SCHEMA_VERSION}")
    record = {}
    for part in rest.split("|"):
        if not part:
            continue
        k, _, v = part.partition("=")
        record[k] = v
    return record
EOF
cat > test_codec.py <<'EOF'
import unittest
from codec import serialize, deserialize
from schema import SCHEMA_VERSION

class T(unittest.TestCase):
    def test_round_trip_has_checksum(self):
        record = {"id": "42"}
        s = serialize(record)
        self.assertIn("checksum=", s)
        restored = deserialize(s)
        self.assertEqual(restored["id"], "42")
        self.assertIn("checksum", restored)

    def test_schema_version_bumped(self):
        self.assertGreaterEqual(SCHEMA_VERSION, 2)
EOF
git add -A && git commit -qm "baseline"
printf '\n# TODO: checksum support needed, see test_codec.py\n' >> codec.py

SECONDS=0
handoff "$WORK/two-file-consistency" \
  "test_codec.py is failing. Make it pass by having serialize() in codec.py compute and include a checksum field automatically in the serialized output, and deserialize() round-trip it back out. This is a record-format change, so also bump SCHEMA_VERSION in schema.py to reflect it. Do NOT modify test_codec.py." \
  "$WORK/logs/two-file-consistency.log"
check two-file-consistency "$SECONDS" \
  "python3 -m unittest test_codec && git diff HEAD --name-only | grep -qx schema.py && git diff HEAD --name-only | grep -qx codec.py" \
  "$WORK/logs/two-file-consistency.log"
fi

########################################################################
# 10. spec-only — no tests given, implement per a README spec; check is hidden (hard)
if [ "$ONLY" = "all" ] || [ "$ONLY" = "10" ]; then
newrepo spec-only
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

## Planned: --repeat

`--repeat N` should print the greeting N times, one line per repeat.
Default (no flag) prints it once. Not implemented yet.
EOF
git add -A && git commit -qm "baseline"
printf '\n# TODO: implement --repeat per README\n' >> cli.py

# Hidden check: written here, never committed into the scenario repo, so
# the agent never sees it.
cat > "$WORK/checks/spec-only.py" <<'PYEOF'
import subprocess, sys

def run(args):
    return subprocess.run([sys.executable, "cli.py", *args], capture_output=True, text=True)

default = run(["World"])
lines_default = [l for l in default.stdout.splitlines() if l.strip()]
ok = (default.returncode == 0 and len(lines_default) == 1 and lines_default[0] == "Hello, World!")

triple = run(["World", "--repeat", "3"])
lines_triple = [l for l in triple.stdout.splitlines() if l.strip()]
ok = ok and (triple.returncode == 0 and len(lines_triple) == 3
             and all(l == "Hello, World!" for l in lines_triple))

sys.exit(0 if ok else 1)
PYEOF

SECONDS=0
handoff "$WORK/spec-only" \
  "Implement the --repeat N flag described in the 'Planned: --repeat' section of README.md." \
  "$WORK/logs/spec-only.log"
check spec-only "$SECONDS" "(cd '$WORK/spec-only' && python3 - < '$WORK/checks/spec-only.py')" "$WORK/logs/spec-only.log"
fi

########################################################################
echo
echo "=== Reports codex-handoff wrote ==="
find "$CODEX_HANDOFF_HOME/runs" -name report.md 2>/dev/null | while read -r r; do echo "  $r"; done

if [ "$TOTAL" -gt 0 ]; then
  MEDIAN="$(python3 -c "import statistics,sys; print(f'{statistics.median([float(x) for x in sys.argv[1:]]):.1f}')" "${TIMES[@]}")"
else
  MEDIAN="n/a"
fi

echo
echo "$PASSED/$TOTAL passed, median ${MEDIAN}s"
echo
echo "Throwaway files are in $WORK -- remove with: rm -rf $WORK"

exit 0
