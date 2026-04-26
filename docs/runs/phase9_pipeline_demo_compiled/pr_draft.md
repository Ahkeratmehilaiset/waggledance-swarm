# Draft PR — introspection_gap

**source meta-proposal:** 4116420fed0a
**target:** 63f8a14965db
**solver_name:** —
**no_runtime_mutation:** True
**no_main_branch_auto_merge:** True

## Affected files

- `docs/runs/self_model/<sha12>/`
- `tools/build_self_model_snapshot.py`

## Rollout plan

- shadow_first: True
- stages: human_review → post_campaign_runtime_candidate → review_only → review_only → review_only

## Notes

Human review required before any runtime promotion.
