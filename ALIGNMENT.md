# Alignment Audit (Rules 85-100 / 16 items) — invariant-guard-gen v0.1.1

| # | Check | Status | Evidence |
|---|-------|--------|----------|
| 1 | Safety/Ethics section | N/A | Not a security-research repo; tool only reads user-designated files and emits local scripts; no network, no eval of untrusted input beyond user's own target |
| 2 | Privacy/dezero | PASS | No secrets/tokens/real names; only contact line uses hcac4735@agent.qq.com; grep `(sk-\|ghp_\|api[_-]?key\s*=\s*['"][A-Za-z0-9])` → 0 hits |
| 3 | Copyright boundary | PASS | Hypothesis/Z3/crosshair comparisons are structural restatements of public knowledge, no >50-char copied text; MIT LICENSE present, "zhengming contributors" |
| 4 | Fact-check annotation | PASS | ~25-35 s suite runtime and <1 s per 200-trial run marked [Measured] with repro commands (`python self_test.py`, `python guard_gen.py check ...`) |
| 5 | Confidence grading | PASS | Opener [High ≥80%]; assumption 1 [Medium]; ≥2 explicit labels visible in opener and [Core Assumptions] |
| 6 | PLAN B built-in | PASS | "Minimal Fallback (PLAN B)": grep-based invariant inventory + assert count triage, zero generation needed |
| 7 | Timeout preset | PASS | Quickstart states "~3 min" with per-step ≈1 s / ≈2-5 s / ≈30 s markers |
| 8 | Rule-conflict arbitration | PASS | "Design Trade-offs" opens with priority: honesty about verification > zero deps > translation coverage > speed |
| 9 | Version changelog | PASS | CHANGELOG.md v0.1.0 + v0.1.1; v0.1.1 lists 5 concrete self-review fixes and test delta |
| 10 | Exemption instruction | N/A | Session-level rule; static repo content not applicable |
| 11 | External-knowledge cutoff | PASS | No vendor time-sensitive claims; equivalent-absence claim dated "as of 2026-10-11" |
| 12 | Assumption explicitness | PASS | "[Core Assumptions]" section with 5 items |
| 13 | Logic-check trio | PASS | self_test.py tiers: [A] functional (21), [B] exception paths (12), [C] edges (10: empty/unicode/5000-char/10-arg/binary/kwonly/nested/regex-special); [R2] round-2 block (11). Final: `python self_test.py` → `SUMMARY: PASS 54/54` |
| 14 | Socratic question | PASS | FAQ "When NOT to use this tool?" with 4 anti-use-cases (proof-needing, custom generators, boilerplate docstrings, impure functions) |
| 15 | Pareto improvement | PASS | v0.1.1: tests 43→54 (+26% ≥20% gate) + 5 correctness fixes incl. a crash bug and a false-negative phrasing class; documented in CHANGELOG, suite re-run green |
| 16 | Ultimate fallback (≤60-word opener) | PASS | First paragraph = 56 words, contains what/who-needs-it judgment sentence |

Second-order effects (rule 16 bonus): PASS — "Limits & Second-order Effects" lists green-dashboard complacency, invariant-weakening gaming, snapshot regeneration risk, flake-driven trust collapse.

Scan evidence: `grep -rEn "(sk-|ghp_|api[_-]?key\s*=\s*['\"][A-Za-z0-9])" .` → 0 hits; `grep -rn "hcac4735@agent.qq.com" README.md` → 1 hit.
