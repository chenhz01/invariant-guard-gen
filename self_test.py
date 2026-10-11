#!/usr/bin/env python3
"""invariant-guard-gen self-test: [A] functional, [B] exception paths, [C] edges.

Run:  python self_test.py          (expect: SUMMARY: PASS n/n)
"""

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guard_gen  # noqa: E402

RESULTS = []
TMP = tempfile.mkdtemp(prefix="igg_test_")


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    return bool(cond)


def w(name, text):
    path = os.path.join(TMP, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def cli(argv):
    proc = subprocess.run([sys.executable, os.path.join(HERE, "guard_gen.py")] + argv,
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


GOODMOD = w("goodmod.py", '''\
"""Good module: every invariant holds."""


def sort_copy(items):
    """Return an ascending copy.

    Invariant: the result is sorted ascending.
    Invariant: the result contains all elements of the input items.
    """
    return sorted(items)


def make_row(n):
    """Build a row of n zeros.

    invariant: len(result) >= 0
    """
    return [0] * max(n, 0)


def convex(x):
    """Invariant: the result is positive."""
    return x * x + 1.0


def safe_div(a, b):
    """Integer division that never raises.

    Invariant: never raises.
    """
    return a // b if b else 0


def asserted_sum(values):
    """Sum with an inline sanity assert."""
    assert len(values) >= 0
    return sum(values)


def mystery(text):
    """Transmute text.

    Invariant: the result is auspicious.
    """
    return text
''')

BADMOD = w("badmod.py", '''\
"""Bad module: two latent violations."""


def double(n):
    """Double n.

    Invariant: the result is positive.
    """
    return n * 2 if n else 0


def explode(b):
    """Invariant: never raises."""
    if b < 0:
        raise ValueError("boom")
    return b
''')

SYNTAX_BAD = w("syntax_bad.py", "def broken(:\n    pass\n")
EMPTY = w("empty.py", "")
BINARY = os.path.join(TMP, "binary.py")
with open(BINARY, "wb") as fh:
    fh.write(b"\x80\x81\x82def f():\n")  # invalid UTF-8

# ---------------------------------------------------------------- [A] functional
rc, out = cli(["list", GOODMOD])
check("A01 list shows all functions", all(f in out for f in
      ("sort_copy", "make_row", "convex", "safe_div", "asserted_sum", "mystery")))

check("A02 SORTED pattern translated", "[SORTED]" in out)

check("A03 PRESERVES_ALL pattern translated", "[PRESERVES_ALL]" in out)

check("A04 LEN_REL with numeric operand", "len(result) >= 0" in out)

check("A05 POSITIVE pattern translated", "[POSITIVE]" in out)

check("A06 NO_RAISE marked harness-enforced", "[NO_RAISE] (harness-enforced)" in out)

check("A07 unknown phrasing marked UNVERIFIED", "[UNVERIFIED] the result is auspicious" in out)

check("A08 assert source extracted verbatim", "assert len(values) >= 0" in out)

check("A09 lowercase 'invariant:' tag accepted",
      "len(result) >= 0" in out and "make_row" in out)

check("A10 module-level assert ignored with note",
      "module-level assert ignored" in cli(["list", w("mla.py",
      "x = 1\nassert x >= 0\n\ndef f(a):\n    'Invariant: the result is positive.'\n    return a+1\n")])[1])

rc, out = cli(["list", w("cls.py", "class K:\n    def m(self):\n        'Invariant: the result is positive.'\n        return 1\n")])
check("A11 class methods skipped with note",
      "methods are not extracted" in out and "def m" not in out)

rc, out = cli(["list", w("asyncf.py", "async def f(a):\n    'Invariant: the result is positive.'\n    return a\n")])
check("A12 async functions skipped with note", "skipped async function" in out)

rc, out = cli(["list", w("varargs.py", "def f(a, *rest):\n    'Invariant: the result is positive.'\n    return a\n")])
check("A13 *args flagged unsupported", "not generated" in out)

gen_out = os.path.join(TMP, "guards_good.py")
rc, out = cli(["gen", GOODMOD, "-o", gen_out])
check("A14 gen exits 0 and writes file", rc == 0 and os.path.isfile(gen_out))

src = open(gen_out, encoding="utf-8").read()
check("A15 generated file compiles", compile(src, gen_out, "exec") is not None)

check("A16 generated file has generator table", "GENERATORS['sort_copy']" in src)

check("A17 generated file records unverified invariant", "UNVERIFIED invariant in mystery" in src)

check("A18 generated file stdlib-only imports", all(
    m in ("argparse", "importlib.util", "random", "sys")
    for m in re.findall(r"^import (\S+)", src, re.M)))

rc, out = cli(["check", GOODMOD, "--trials", "120", "--seed", "7"])
check("A19 check on good module exits 0", rc == 0 and "passed" in out)

check("A20 check reports unverified count", "1 unverified invariants" in out)

SKIPMOD = w("skipmod.py", '''\
def risky(x):
    """No documented invariants; raises on zero."""
    return 1.0 / x
''')
rc, out = cli(["check", SKIPMOD, "--trials", "80", "--seed", "3"])
check("A21 out-of-domain exceptions counted as skipped, not failures",
      rc == 0 and "skipped (out-of-domain)" in out)

# ------------------------------------------------------------------ [B] exception
rc, out = cli(["list", "does_not_exist_xyz.py"])
check("B01 missing target exits 2 with message", rc == 2 and "not found" in out)

rc, out = cli(["list", SYNTAX_BAD])
check("B02 syntax-error target exits 2", rc == 2 and "not valid Python" in out)

rc, out = cli(["list", EMPTY])
check("B03 empty file: no functions, exit 0", rc == 0 and "no module-level functions" in out)

check("B04 no subcommand exits 2", cli([])[0] == 2)

check("B05 unknown subcommand exits 2", cli(["frobnicate"])[0] == 2)

rc, out = cli(["check", BADMOD, "--trials", "200", "--seed", "42"])
check("B06 counterexample found exits 1", rc == 1)

check("B07 report names function and seed",
      "COUNTEREXAMPLE" in out and "seed 42" in out)

check("B08 shrinking report present", "shrunk :" in out or "shrunk" in out)

gen_bad = os.path.join(TMP, "guards_bad.py")
cli(["gen", BADMOD, "-o", gen_bad])
rc, out2 = subprocess_run = (lambda p: (p.returncode, p.stdout + p.stderr))(
    subprocess.run([sys.executable, gen_bad, "--trials", "-1"],
                   capture_output=True, text=True))
check("B09 negative --trials rejected by harness", rc == 2 and "--trials must be >= 0" in out2)

orphan = os.path.join(TMP, "orphan.py")
w("orphan.py", "def f(a):\n    'Invariant: the result is positive.'\n    return a\n")
gen_orph = os.path.join(TMP, "guards_orphan.py")
cli(["gen", orphan, "-o", gen_orph])
os.remove(orphan)
rc, out = (lambda p: (p.returncode, p.stdout + p.stderr))(
    subprocess.run([sys.executable, gen_orph], capture_output=True, text=True))
check("B10 deleted target -> harness import error exit 2",
      rc == 2 and "cannot import target module" in out)

def cli_stdout(argv):
    proc = subprocess.run([sys.executable, os.path.join(HERE, "guard_gen.py")] + argv,
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout  # stderr carries per-run "wrote <tmp>" line


rc1, out1 = cli_stdout(["check", BADMOD, "--trials", "200", "--seed", "42"])
rc2, out2 = cli_stdout(["check", BADMOD, "--trials", "200", "--seed", "42"])
check("B11 same seed -> deterministic report", rc1 == rc2 == 1 and out1 == out2)

rc, out = cli(["gen", TMP])  # a directory, not a file
check("B12 directory as target exits 2", rc == 2)

# ------------------------------------------------------------------ [C] edges
UNI_SRC = (
    "def f(s):\n"
    '    """密文.\n'
    "\n"
    "    Invariant: 結果は常に正の整数。\n"
    '    """\n'
    "    return 1\n"
)
rc, out = cli(["list", w("uni.py", UNI_SRC)])
check("C01 unicode invariant parsed, marked UNVERIFIED", rc == 0 and "[UNVERIFIED]" in out)

long_inv = "Invariant: " + "very long constraint " * 300
rc, out = cli(["gen", w("longinv.py", "def f(a):\n    '''%s'''\n    return a\n" % long_inv),
               "-o", os.path.join(TMP, "g_long.py")])
check("C02 5000-char invariant line handled", rc == 0)

rc, out = cli(["gen", w("tenargs.py", "def f(" + ", ".join("a%d" % i for i in range(10)) + "):\n"
                        "    'Invariant: the result is positive.'\n    return a0\n"),
               "-o", os.path.join(TMP, "g_ten.py")])
src_ten = open(os.path.join(TMP, "g_ten.py"), encoding="utf-8").read()
check("C03 10-arg function gets 10 generators", rc == 0 and all(
    "'a%d'" % i in src_ten for i in range(10)))

rc, out = cli(["check", EMPTY, "--trials", "50"])
check("C04 empty module check passes trivially", rc == 0 and "0 executions passed" in out)

check("C05 non-UTF8 file exits 2 with message",
      cli(["list", BINARY])[0] == 2 and "not valid UTF-8" in cli(["list", BINARY])[1])

rc, out = cli(["check", w("kwonly.py", "def f(a, *, scale=1):\n    'Invariant: never raises.'\n    return a*scale\n"),
               "--trials", "30"])
check("C06 kwonly-arg function: calls skip gracefully, exit 0", rc == 0)

rc, out = cli(["check", GOODMOD, "--trials", "500", "--seed", "11"])
check("C07 500 trials on good module still green", rc == 0)

ok_seeds = all(cli(["check", GOODMOD, "--trials", "60", "--seed", str(s)])[0] == 0
               for s in (0, 1, 999))
check("C08 multiple seeds all green", ok_seeds)

rc, out = cli(["list", w("nested.py", "def outer(a):\n    'Invariant: the result is positive.'\n"
                        "    def inner(b):\n        return b\n    return inner(a)\n")])
check("C09 nested function not extracted separately", "inner" not in out and "outer" in out)

RX_SRC = (
    "def f(a):\n"
    "    'Invariant: result must match ^[a-z+*]{3}$ and (never|always) be odd.'\n"
    "    return a\n"
)
rc, out = cli(["gen", w("regexinv.py", RX_SRC), "-o", os.path.join(TMP, "g_rx.py")])
check("C10 regex-special chars in invariant text do not break emission",
      rc == 0 and compile(open(os.path.join(TMP, "g_rx.py"), encoding="utf-8").read(),
                          "g_rx", "exec") is not None)

# --------------------------------------------------- [R2] round-2 added coverage
LEN_ARG = w("lenarg.py", '''\
def pad(items, n):
    """Invariant: result length == the input length of items plus n."""
    return list(items) + [0] * max(n, 0)
''')
rc, out = cli(["list", LEN_ARG])
check("R01 LEN_ARG_REL translated with args reference",
      "len(args['items'])" in out or "len(result) ==" in out)

ARGN = w("argn.py", '''\
def chunk(n):
    """Invariant: len(result) == n."""
    return [0] * max(n, 0)
''')
rc, out = cli(["list", ARGN])
check("R02 LEN_REL with arg operand uses args['n']", "args['n']" in out)

rc, out = cli(["list", w("phr.py", 'def f(a):\n    """Invariant: returns a positive value."""\n    return a*a+1\n')])
check("R03 'returns positive' phrasing translated", "[POSITIVE]" in out)

rc, out = cli(["list", w("phr2.py", 'def f(a):\n    """Invariant: must not raise."""\n    return a\n')])
check("R04 'must not raise' phrasing translated", "[NO_RAISE]" in out)

rc, out = cli(["check", BADMOD, "--trials", "200", "--seed", "42"])
m = re.search(r"args   : (\{.*\})\n\s*shrunk : (\{.*\})", out)
check("R05 shrink reduces counterexample magnitude",
      m is not None and abs(list(ast.literal_eval(m.group(2)).values())[0])
      <= abs(list(ast.literal_eval(m.group(1)).values())[0]))

gen_zero = os.path.join(TMP, "guards_zero.py")
cli(["gen", GOODMOD, "-o", gen_zero])
rc, out = (lambda p: (p.returncode, p.stdout + p.stderr))(
    subprocess.run([sys.executable, gen_zero, "--trials", "0"], capture_output=True, text=True))
check("R06 --trials 0 accepted, exits 0", rc == 0 and "0 executions passed" in out)

IMPORT_FAIL = w("import_fail.py", "raise RuntimeError('module init exploded')\n")
rc, out = cli(["check", IMPORT_FAIL, "--trials", "5"])
check("R07 module-level import failure -> exit 2", rc == 2 and "cannot import" in out)

rc, out = cli(["list", w("phr3.py", 'def f(a):\n    """Invariant: the result should be positive."""\n    return a*a+1\n')])
check("R08 'should be positive' phrasing translated", "[POSITIVE]" in out)

rc, out = cli(["list", w("doctest.py", 'def f(a):\n    """Add one.\n\n    Invariant: the result is positive.\n    >>> f(1)\n    2\n    """\n    return a + 1\n')])
check("R09 doctest line terminates invariant capture",
      "[POSITIVE]" in out and ">>>" not in out)

rc, out = cli(["list", w("subj.py", 'def f(a):\n    """Invariant: input stays positive across calls."""\n    return a\n')])
check("R10 non-result 'positive' sentence not mistranslated", "[UNVERIFIED]" in out)

rc, out = cli(["check", GOODMOD, "--trials", "2000", "--seed", "5"])
check("R11 2000-trial run still green", rc == 0)

if __name__ == "__main__":
    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    for n, ok in RESULTS:
        if not ok:
            print("FAIL:", n)
    print("SUMMARY: PASS %d/%d" % (passed, total))
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(0 if passed == total else 1)
