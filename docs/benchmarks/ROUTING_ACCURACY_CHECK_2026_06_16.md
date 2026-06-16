# Routing Accuracy + Expensive-Path Rate — Results (2026-06-16)

**Status:** producer-lane measured results (fable-5, non-gate identity).
**Date:** 2026-06-16.
**Author:** fable-5.
**Task:** `fable-5/routing-accuracy-check-20260616`.
**Harness:** `tools/run_routing_accuracy_check.py`.

Engineering record, fully offline (no model or cloud calls). Mismatches below are
**capsule-tuning candidates, not asserted defects** — accuracy reflects how well
the chosen profile's capsule aligns with the corpus `expected_route` labels, two
independently-authored artifacts. No claim of superiority over any external
system is made or implied. Supports the north-star *deterministic-first routing*
pillar by measuring how often routing resolves on a cheap deterministic layer
vs the costly LLM path.

## Reproduce

```
python tools/run_routing_accuracy_check.py --profile apiary \
    --out-dir docs/runs/routing_accuracy_check_2026_06_16
```

(The raw JSON envelope is regenerable via `--out-dir`; it is intentionally not
committed — `docs/runs/` is off the autonomous-merge allowlist. The numbers
below are the durable record.)

## Results — profile `apiary`, 30-query corpus

| metric | value |
|---|---:|
| overall accuracy (predicted layer == `expected_route`) | **0.467** (14/30) |
| expensive-path rate (routed to `llm_reasoning`) | **0.133** (4/30) |

| expected route | correct / total | accuracy |
|---|---:|---:|
| `model_based` | 9 / 15 | 0.60 |
| `statistical` | 2 / 4 | 0.50 |
| `llm_reasoning` | 2 / 5 | 0.40 |
| `retrieval` | 1 / 6 | **0.17** |

16 of 30 queries route to a layer other than their label.

## Findings

* **Cost path is healthy; only 13 % of the corpus reaches the expensive
  `llm_reasoning` layer.** Most queries resolve on a cheaper deterministic layer
  (model/rule/statistical/retrieval), which is the desired behaviour for the
  deterministic-first north-star.
* **Part of that low cost is an accuracy trade-off.** The ambiguous `fallback`
  queries (labelled `llm_reasoning`) are routed to `retrieval` instead — cheaper,
  but not the labelled path. So the low expensive-path rate is partly *because*
  the router prefers a cheap layer for ambiguous input.
* **`retrieval`-labelled queries are the weakest alignment (17 %).** They are
  most often routed to `model_based`/`statistical`/`rule_constraints`. These are
  the clearest **capsule-tuning candidates**: if these queries genuinely need the
  retrieval layer, the apiary capsule's `key_decisions` keywords for those topics
  are under-specified.
* The 16 mismatches are enumerated in the harness output (run the command above)
  and are concrete, query-level tuning leads — not asserted errors.

## Net recommendation

Deterministic-first routing is cost-healthy (low LLM rate) but has a measurable
alignment gap on `retrieval`-labelled queries for the `apiary` profile. A
follow-up capsule-tuning slice could add/adjust `retrieval` key-decision keywords
and re-run this harness to confirm the accuracy delta — measurement-first, no
speculative capsule edits until the harness shows the gain.

## References

* `tools/run_routing_accuracy_check.py` — this harness.
* `core/smart_router_v2.py`, `core/domain_capsule.py` — routing path under test.
* `configs/benchmarks.yaml` — 30-query corpus with `expected_route` labels.
* `configs/capsules/apiary.yaml` — profile under test.
* `docs/benchmarks/ROUTING_HOTPATH_MICROBENCH_RESULTS_2026_06_16.md` — companion latency results.
