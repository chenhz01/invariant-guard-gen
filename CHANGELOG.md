# Changelog — invariant-guard-gen

## v0.1.1 — 2026-10-11 (round-2 self-review revision)

### Fixed
- Generated harness crashed with `NameError: rng` at import: generator expressions were emitted as live code evaluated at module level instead of strings evaluated per-trial under the seeded rng (self-review finding A19).
- `except ast.SyntaxError` does not exist — `ast.parse` raises the builtin `SyntaxError`; the broken except clause aborted handler resolution and cascaded failures into the UTF-8/ValueError paths (B02/C05).
- `never raises` invariant regex missed the plural "never raises" (A06).
- `UNVERIFIED` list in generated files was declared but never populated; unverified-invariant count was always 0 (A20).
- `wrote <file>` progress line moved to stderr so repeated seeded runs produce byte-identical stdout (B11).

### Changed
- Test suite extended 43 → 54 cases (+26%, coverage dimension of the ≥20% round-2 gate): arg-referencing length relations, additional phrasings ("returns a positive value", "must not raise", "should be positive"), shrink-magnitude assertion, `--trials 0`, module-level import failure, doctest-adjacent invariant capture, non-result "positive" sentences not mistranslated, 2000-trial soak.

### Improved
- POSITIVE/NON_NEGATIVE/NEGATIVE patterns now also match subject-less phrasings (`returns a positive value`) — closes a false-negative class found by R03.

## v0.1.0 — 2026-10-11 (first working draft)

### Added
- `guard_gen.py`: AST-based extraction of `Invariant:` docstring lines (tag + continuation joining) and function-body asserts; ~10-pattern translation registry (POSITIVE/NON_NEGATIVE/NEGATIVE/SORTED/NO_RAISE/PRESERVES_ALL/LEN_REL/LEN_ARG_REL) with honest UNVERIFIED fallback; generator inference (annotation → name heuristic); emitted standalone checker with seeded generators, replayed asserts, out-of-domain skip accounting, and greedy per-argument shrinking.
- CLI: `list` (inventory), `gen` (emit checker, compile-checked), `check` (generate + run).
- `self_test.py`: 43 cases across functional / exception / edge tiers.
- README (article-level), MIT LICENSE, this changelog.
