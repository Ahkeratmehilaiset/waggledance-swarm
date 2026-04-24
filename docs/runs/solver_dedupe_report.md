# Solver dedupe report

- **Generated:** 2026-04-24T05:51:14+00:00
- **Axiom dir:** `configs/axioms`
- **Files scanned:** 22
- **Parseable axioms:** 22
- **Strict duplicate groups:** 0
- **Legacy-collision groups (strict-distinct):** 0

## 1. Strict duplicates

These share *every* semantic dimension — formulas, inputs, outputs,
units, conditions, tags, invariants. A match here is almost certainly
a genuine duplicate and should be collapsed.

*(none — every axiom is uniquely hashed)*

## 2. Core-semantic collisions (strict-distinct)

These share the legacy `canonical_hash` shape (formulas + variables +
conditions) but diverge on outputs, tags, or invariants. Common
cause: copy-paste adaptation with modified output unit or tag.

*(none)*