#!/usr/bin/env python3
"""invariant-guard-gen: turn docstring invariants and asserts into runnable property checks.

Static tool (stdlib only, no solver): parses a Python source file, extracts
``Invariant:`` lines from docstrings and existing ``assert`` statements from
function bodies, then *generates* a property-based checker script — seeded
random input generators, executable invariant assertions (for the subset of
invariant phrasings it can translate), and a shrinking report on failure.

It does not prove anything. It makes your existing informal invariants
cheap enough to fuzz on every commit.

Python >= 3.8.
"""

import argparse
import ast
import keyword
import os
import re
import sys

VERSION = "0.1.1"

INVARIANT_TAG = re.compile(r"^\s*invariant\s*:\s*(.*)$", re.IGNORECASE)

# ---------------------------------------------------------------- translatable
# Each pattern maps an invariant sentence to an executable check expression.
# ``{arg}`` placeholders are substituted with the actual parameter name.
PATTERNS = [
    ("POSITIVE",
     re.compile(r"^(the\s+)?result\b.*\b(is|must be|should be|returns?)\b.*\bpositive\b"
                r"|\breturns?\b\s+(a\s+)?positive\b", re.I),
     "result > 0"),
    ("NON_NEGATIVE",
     re.compile(r"^(the\s+)?result\b.*\b(is|must be|should be|returns?)\b"
                r".*\b(non-?negative|zero or (greater|more))\b"
                r"|\breturns?\b\s+(a\s+)?non-?negative\b", re.I),
     "result >= 0"),
    ("NEGATIVE",
     re.compile(r"^(the\s+)?result\b.*\b(is|must be|should be|returns?)\b.*\bnegative\b"
                r"|\breturns?\b\s+(a\s+)?negative\b", re.I),
     "result < 0"),
    ("SORTED",
     re.compile(r"\bresult\b.*\b(is|must be|should be)\b.*\b(non-?decreasing|sorted( ascending)?)\b", re.I),
     "all(result[i] <= result[i + 1] for i in range(len(result) - 1))"),
    ("NO_RAISE",
     re.compile(r"\b(never|does not|doesn'?t|must not)\s+(raise|throw)s?\b", re.I),
     None),  # handled by the harness (any exception is a counterexample)
    ("PRESERVES_ALL",
     re.compile(r"\bresult\b.*\b(contains|includes|preserves?|keeps?)\b\s+(all|every)?\s*(the\s+)?(elements|items|entries)", re.I),
     "set(_first_seq(args)) <= set(result)"),
    ("LEN_REL",
     re.compile(r"\blen\(result\)\s*(==|<=|>=|<|>)\s*(\w+)"),
     None),  # built dynamically from the captured relation
    ("LEN_ARG_REL",
     re.compile(r"\bresult\s+length\s*(==|<=|>=|<|>)\s*(?:the\s+)?(?:input\s+)?(?:length\s+of\s+)?(\w+)"),
     None),
]


def _rel_expr(rel, operand, argnames):
    if operand in argnames:
        return "len(result) %s args[%r]" % (rel, operand)
    if re.fullmatch(r"\d+", operand):
        return "len(result) %s %s" % (rel, operand)
    return None


def translate(text, argnames):
    """Return (kind, expr) when the sentence maps to a check; else (None, text)."""
    for rel, operand in re.findall(r"len\(result\)\s*(==|<=|>=|<|>)\s*(\w+)", text):
        expr = _rel_expr(rel, operand, argnames)
        if expr:
            return "LEN_REL", expr
    m = re.search(r"\bresult\s+length\s*(==|<=|>=|<|>)\s*(?:the\s+)?(?:input\s+)?(?:length\s+of\s+)?(\w+)", text, re.I)
    if m and m.group(2) in argnames:
        return "LEN_ARG_REL", "len(result) %s len(args[%r])" % (m.group(1), m.group(2))
    for kind, rx, expr in PATTERNS:
        if kind in ("LEN_REL", "LEN_ARG_REL"):
            continue
        if rx.search(text):
            return kind, expr
    return None, text  # untranslatable -> reported as UNVERIFIED, never asserted


# ------------------------------------------------------------------- extraction
class FunctionSpec:
    def __init__(self, name, args, invariants, asserts, unsupported):
        self.name = name
        self.args = args                    # list of positional-or-keyword names
        self.invariants = invariants        # list of (kind, payload) — payload is expr or raw text
        self.asserts = asserts              # list of assert source strings
        self.unsupported = unsupported      # list of reason strings


def _docstring_invariants(docstring):
    out, current = [], None
    for line in (docstring or "").splitlines():
        m = INVARIANT_TAG.match(line)
        if m:
            if current:
                out.append(" ".join(current))
            current = [m.group(1).strip()]
        elif current is not None:
            stripped = line.strip()
            if not stripped or re.match(r"^\s*(returns?|raises?|args?|params?|>>>):?\s*$", line, re.I):
                out.append(" ".join(current))
                current = None
            elif stripped.startswith(("Invariant", "Note", "Example")):
                out.append(" ".join(current))
                current = None
            else:
                current.append(stripped)
    if current:
        out.append(" ".join(current))
    return [t for t in (s.strip() for s in out) if t]


def _assert_sources(fn_node, source_lines):
    out = []
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Assert):
            seg = ast.get_source_segment("\n".join(source_lines), node)
            out.append(seg if seg else "assert <unrecoverable>")
    return out


def extract(path):
    """Parse a Python file -> (list[FunctionSpec], list[str] module-level notes)."""
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source, filename=path)
    lines = source.splitlines()
    specs, notes = [], []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.AsyncFunctionDef):
                notes.append("skipped async function %r (coroutines need an event loop)" % node.name)
                continue
            args = [a.arg for a in node.args.args if not keyword.iskeyword(a.arg)]
            unsupported = []
            if node.args.vararg:
                unsupported.append("*%s not generated" % node.args.vararg.arg)
            if node.args.kwarg:
                unsupported.append("**%s not generated" % node.args.kwarg.arg)
            doc = ast.get_docstring(node)
            invs_raw = _docstring_invariants(doc)
            invariants = []
            for text in invs_raw:
                kind, expr = translate(text, args)
                invariants.append((kind, expr))
            specs.append(FunctionSpec(node.name, args, invariants,
                                      _assert_sources(node, lines), unsupported))
        elif isinstance(node, ast.Assert):
            notes.append("module-level assert ignored (not inside a function): %s"
                         % (ast.get_source_segment(source, node) or "?"))
        elif isinstance(node, ast.ClassDef):
            notes.append("class %r: methods are not extracted (module-level functions only)" % node.name)
    return specs, notes


# ------------------------------------------------------------------- generators
ANN_MAP = {
    "int": "rng.randint(-100, 100)",
    "float": "rng.uniform(-1000.0, 1000.0)",
    "str": "rng.choice(['', 'a', 'abc', 'hello world', 'Ünïcode🎉'])",
    "bool": "rng.random() < 0.5",
    "list": "rng.choice([[], [1], [3, 1, 2], [i %% 7 for i in range(rng.randint(0, 8))]])",
    "List": "rng.choice([[], [1], [3, 1, 2], [i %% 7 for i in range(rng.randint(0, 8))]])",
}

NAME_HINTS = [
    (re.compile(r"(count|n$|num|idx|i$|j$|size|len|limit|depth|level|amount|qty)"), "int"),
    (re.compile(r"(ratio|rate|frac|price|score|threshold|pct|weight)"), "float"),
    (re.compile(r"(items|arr|lst|list|values|xs|elements|seq)"), "list"),
    (re.compile(r"(str|text|name|word|s$|msg|label)"), "str"),
    (re.compile(r"^(is|has|use|enable|flag)"), "bool"),
]
KIND_EXPR = {
    "int": "rng.randint(-100, 100)",
    "float": "rng.uniform(-1000.0, 1000.0)",
    "str": "rng.choice(['', 'a', 'abc', 'hello world', 'Ünïcode🎉'])",
    "bool": "rng.random() < 0.5",
    "list": "[rng.randint(0, 9) for _ in range(rng.randint(0, 8))]",
}


def infer_kind(arg_name, annotation_text):
    ann = (annotation_text or "").strip()
    base = ann.split("[")[0].strip("'\" ")
    if base in ANN_MAP:
        return base if base in KIND_EXPR else "list"
    for rx, kind in NAME_HINTS:
        if rx.search(arg_name.lower()):
            return kind
    return "int"


def generator_expr(kind, rng_name="rng"):
    return KIND_EXPR[kind]


SHRINKERS = {
    "int": "def _shrink_int(v):\n"
           "    cands = [0, 1]\n"
           "    if v < 0:\n"
           "        cands += [v + 1, -(-v // 2)]\n"
           "    else:\n"
           "        cands += [v - 1, v // 2]\n"
           "    return [c for c in cands if c != v]",
    "float": "def _shrink_float(v):\n"
             "    return [c for c in (0.0, 1.0, v / 2) if c != v]",
    "str": "def _shrink_str(v):\n"
           "    return [c for c in ('', 'a', v[:len(v) // 2]) if c != v]",
    "bool": "def _shrink_bool(v):\n    return []",
    "list": "def _shrink_list(v):\n"
            "    return [c for c in ([], v[:len(v) // 2], v[:-1]) if c != v]",
}


# ---------------------------------------------------------------- code emission
_HEADER = '''#!/usr/bin/env python3
"""Property-based checker generated by invariant-guard-gen {version}.

Source of truth : {source}
Do not hand-edit: regenerate with `python guard_gen.py gen {source} -o {outname}`.

Run:  python {outname} --trials 200 --seed 42
Exit: 0 all checks held | 1 counterexample found | 2 setup error
"""
import argparse
import importlib.util
import random
import sys

TARGET = {target!r}
SEED_DEFAULT = 42
TRIALS_DEFAULT = 200

UNVERIFIED = []   # (function, invariant text) — extracted but not translatable


def load_target():
    spec = importlib.util.spec_from_file_location("_guard_target", TARGET)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
'''

_FUNCS = '''

def _first_seq(args):
    for v in args.values():
        if isinstance(v, (list, tuple)):
            return v
    return ()
'''

_HARNESS = '''

def _run_checks(mod, fname, args):
    """Returns (status, detail): 'pass' | 'fail' | 'skip'."""
    fn = getattr(mod, fname)
    checks = CHECKS.get(fname, [])
    try:
        result = fn(**args)
    except Exception as exc:  # noqa: BLE001
        if any(kind == "NO_RAISE" for kind, _ in checks):
            return "fail", "invariant 'never raises' violated: %s: %s" % (type(exc).__name__, exc)
        return "skip", "call raised %s: %s (input out of domain, not counted)" % (
            type(exc).__name__, exc)
    for kind, payload in checks:
        if payload is None:
            continue
        try:
            ok = eval(payload, {"result": result, "args": args, "_first_seq": _first_seq})
        except Exception as exc:  # noqa: BLE001
            return "fail", "check %s crashed: %s: %s" % (kind, type(exc).__name__, exc)
        if not ok:
            return "fail", "invariant %s violated: %s" % (kind, payload)
    for assert_src in ASSERTS.get(fname, []):
        ns = {"result": result}
        ns.update(args)
        try:
            exec(compile(assert_src, "<replayed-assert>", "exec"), ns)
        except AssertionError as exc:
            return "fail", "replayed assert failed: %s" % (assert_src,)
        except Exception:
            return "skip", "replayed assert %r references out-of-scope names; excluded"
    return "pass", ""


def _shrink_value(v):
    if isinstance(v, bool):
        return []
    if isinstance(v, int):
        return [c for c in (0, 1, v - 1 if v > 0 else v + 1, v // 2) if c != v]
    if isinstance(v, float):
        return [c for c in (0.0, 1.0, v / 2) if c != v]
    if isinstance(v, str):
        return [c for c in ("", "a", v[: len(v) // 2]) if c != v]
    if isinstance(v, list):
        return [c for c in ([], v[: len(v) // 2], v[:-1]) if c != v]
    return []


def _shrink(mod, fname, args, budget=300):
    """Greedily reduce each argument while the invariant still fails."""
    best = dict(args)
    for _ in range(budget):
        improved = False
        for key in list(best):
            for cand in _shrink_value(best[key]):
                trial = dict(best)
                trial[key] = cand
                status, detail = _run_checks(mod, fname, trial)
                if status == "fail":
                    best = trial
                    improved = True
                    break
        if not improved:
            break
    return best


def main(argv=None):
    parser = argparse.ArgumentParser(description="run generated invariant guards")
    parser.add_argument("--trials", type=int, default=TRIALS_DEFAULT)
    parser.add_argument("--seed", type=int, default=SEED_DEFAULT)
    ns = parser.parse_args(argv)

    if ns.trials < 0:
        print("guard: --trials must be >= 0", file=sys.stderr)
        return 2
    try:
        mod = load_target()
    except Exception as exc:  # noqa: BLE001
        print("guard: cannot import target module: %s: %s" % (type(exc).__name__, exc),
              file=sys.stderr)
        return 2

    rng = random.Random(ns.seed)
    skips = fails = checks_run = 0
    for fname, gens in GENERATORS.items():
        if not gens:
            continue
        for trial in range(ns.trials):
            args = {name: eval(expr, {"rng": rng}) for name, expr in gens}
            status, detail = _run_checks(mod, fname, args)
            if status == "pass":
                checks_run += 1
                continue
            if status == "skip":
                skips += 1
                continue
            fails += 1
            shrunk = _shrink(mod, fname, args)
            print("COUNTEREXAMPLE in %s (trial %d, seed %d)" % (fname, trial, ns.seed))
            print("  detail : %s" % detail)
            print("  args   : %r" % (args,))
            if shrunk != args:
                print("  shrunk : %r" % (shrunk,))
            return 1
    print("guard: %d executions passed, %d skipped (out-of-domain), %d unverified invariants"
          % (checks_run, skips, len(UNVERIFIED)))
    for fname, text in UNVERIFIED:
        print("  UNVERIFIED in %s: %s" % (fname, text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def generate(specs, source_path, out_path):
    src_display = os.path.abspath(source_path).replace("\\", "/")
    parts = [_HEADER.format(version=VERSION, source=src_display,
                            target=os.path.abspath(source_path),
                            outname=os.path.basename(out_path)), _FUNCS]

    gen_lines, check_lines, assert_lines = [], [], []
    for spec in specs:
        gens = []
        for arg in spec.args:
            ann = ""
            gens.append((arg, generator_expr(infer_kind(arg, ann))))
        if gens:
            gen_lines.append("GENERATORS[%r] = [%s]"
                             % (spec.name, ", ".join("(%r, %r)" % g for g in gens)))
        else:
            gen_lines.append("GENERATORS[%r] = []  # no generatable positional args" % spec.name)
        if spec.unsupported:
            gen_lines.append("# %s: %s" % (spec.name, "; ".join(spec.unsupported)))

        entries = []
        for kind, payload in spec.invariants:
            if kind is None:
                check_lines.append("# UNVERIFIED invariant in %s: %s" % (spec.name, payload))
                check_lines.append("UNVERIFIED.append((%r, %r))" % (spec.name, payload))
            elif kind == "NO_RAISE":
                entries.append('("NO_RAISE", None)')
            else:
                entries.append("(%r, %r)" % (kind, payload))
        check_lines.append("CHECKS[%r] = [%s]" % (spec.name, ", ".join(entries)))

        if spec.asserts:
            assert_lines.append("ASSERTS[%r] = %r" % (spec.name, spec.asserts))

    body = ["GENERATORS = {}", "CHECKS = {}", "ASSERTS = {}", ""]
    body += gen_lines + [""] + check_lines + [""] + assert_lines
    return "".join(parts) + "\n".join(body) + _HARNESS


# ------------------------------------------------------------------------- CLI
def cmd_list(path):
    specs, notes = extract(path)
    if not specs:
        print("no module-level functions found in %s" % path)
    for spec in specs:
        print("function %s(%s)" % (spec.name, ", ".join(spec.args)))
        for kind, payload in spec.invariants:
            if kind is None:
                print("  invariant [UNVERIFIED] %s" % payload)
            elif kind == "NO_RAISE":
                print("  invariant [%s] (harness-enforced)" % kind)
            else:
                print("  invariant [%s] %s" % (kind, payload))
        for src in spec.asserts:
            print("  assert %s" % src)
        for why in spec.unsupported:
            print("  note: %s" % why)
    for note in notes:
        print("note: %s" % note)
    return 0


def cmd_gen(path, out):
    specs, _ = extract(path)
    code = generate(specs, path, out)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(code)
    compile(code, out, "exec")  # emitted file must be valid Python — fail loudly here
    print("wrote %s (%d functions, %d bytes)" % (out, len(specs), len(code)), file=sys.stderr)
    return 0


def cmd_check(path, trials, seed):
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix="_guards.py")
    os.close(fd)
    try:
        rc = cmd_gen(path, tmp)
        if rc != 0:
            return rc
        import subprocess
        proc = subprocess.run([sys.executable, tmp, "--trials", str(trials),
                               "--seed", str(seed)], capture_output=True, text=True)
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        return proc.returncode
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="guard_gen",
        description="Extract docstring invariants + asserts from a Python file "
                    "and generate a property-based checker (with shrinking).")
    parser.add_argument("--version", action="version", version="invariant-guard-gen " + VERSION)
    sub = parser.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="show extracted invariants/asserts inventory")
    p_list.add_argument("target")

    p_gen = sub.add_parser("gen", help="generate a checker script")
    p_gen.add_argument("target")
    p_gen.add_argument("-o", "--out", default="guards.py")

    p_chk = sub.add_parser("check", help="generate + run in one step")
    p_chk.add_argument("target")
    p_chk.add_argument("--trials", type=int, default=200)
    p_chk.add_argument("--seed", type=int, default=42)

    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help(sys.stderr)
        return 2
    if not os.path.isfile(args.target):
        print("guard_gen: target file not found: %s" % args.target, file=sys.stderr)
        return 2
    try:
        if args.cmd == "list":
            return cmd_list(args.target)
        if args.cmd == "gen":
            return cmd_gen(args.target, args.out)
        if args.cmd == "check":
            return cmd_check(args.target, args.trials, args.seed)
    except SyntaxError as exc:
        print("guard_gen: target is not valid Python: %s" % exc, file=sys.stderr)
        return 2
    except UnicodeDecodeError as exc:
        print("guard_gen: target is not valid UTF-8 text: %s" % exc, file=sys.stderr)
        return 2
    except ValueError as exc:  # e.g. null bytes in source
        print("guard_gen: target cannot be parsed: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("guard_gen: cannot read target: %s" % exc, file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
