"""Bulk rule extractor — Phase 9 §U1.

Inspects structured tables / vector clusters / mentor packs and
proposes the matching solver_family + spec keys WITHOUT free-form
code generation. Returns match_confidence per candidate so the
U1→U3 router can decide.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import (
    SOLVER_FAMILY_KINDS,
    U1_HIGH_CONFIDENCE_THRESHOLD,
    U1_LOW_CONFIDENCE_THRESHOLD,
)


@dataclass(frozen=True)
class FamilyMatch:
    family_kind: str
    match_confidence: float
    extracted_spec: dict
    rationale: str

    def to_dict(self) -> dict:
        return {
            "family_kind": self.family_kind,
            "match_confidence": self.match_confidence,
            "extracted_spec": dict(self.extracted_spec),
            "rationale": self.rationale,
        }


def match_route(match: FamilyMatch) -> str:
    """Decide U1 vs U3 vs try_u1_then_u3 per Prompt_1_Master §U1
    ROUTING RULES."""
    if match.match_confidence > U1_HIGH_CONFIDENCE_THRESHOLD:
        return "U1_compile"
    if match.match_confidence < U1_LOW_CONFIDENCE_THRESHOLD:
        return "U3_residual"
    return "U1_with_U3_fallback"


# ── Table-based extractors ───────────────────────────────────────-

def extract_from_table(table: dict) -> list[FamilyMatch]:
    """Inspect a table-shaped dict and propose matching families.

    table is e.g.
        {"columns": ["celsius", "fahrenheit"],
         "rows": [[0, 32], [100, 212]], ...}
    """
    matches: list[FamilyMatch] = []
    cols = list(table.get("columns") or [])
    rows = list(table.get("rows") or [])

    # 2-column numeric table with proportional rows → unit conversion
    if len(cols) == 2 and len(rows) >= 2 and all(
        len(r) == 2 and isinstance(r[0], (int, float))
        and isinstance(r[1], (int, float)) for r in rows
    ):
        # Try y = x * factor (+ offset)
        if all(r[0] != 0 for r in rows):
            ratios = [r[1] / r[0] for r in rows if r[0] != 0]
            if ratios and max(ratios) - min(ratios) < 1e-6:
                matches.append(FamilyMatch(
                    family_kind="scalar_unit_conversion",
                    match_confidence=0.95,
                    extracted_spec={
                        "from_unit": cols[0],
                        "to_unit": cols[1],
                        "factor": ratios[0],
                    },
                    rationale=(
                        f"2-col numeric table with constant ratio "
                        f"{ratios[0]:.6f}"
                    ),
                ))
        # Try y = x * factor + offset (linear regression on 2 cols)
        if not any(m.family_kind == "scalar_unit_conversion"
                    for m in matches):
            xs = [r[0] for r in rows]
            ys = [r[1] for r in rows]
            n = len(xs)
            mean_x = sum(xs) / n
            mean_y = sum(ys) / n
            num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
            den = sum((x - mean_x) ** 2 for x in xs)
            if den > 0:
                slope = num / den
                intercept = mean_y - slope * mean_x
                # Check residuals
                preds = [slope * x + intercept for x in xs]
                residuals = [abs(p - y) for p, y in zip(preds, ys)]
                if max(residuals) < 1e-3:
                    matches.append(FamilyMatch(
                        family_kind="linear_arithmetic",
                        match_confidence=0.85,
                        extracted_spec={
                            "coefficients": [slope],
                            "intercept": intercept,
                            "input_columns": cols[:1],
                        },
                        rationale=(
                            f"linear fit y = {slope:.4f}*x + {intercept:.4f}"
                        ),
                    ))

    # Table with intervals → interval bucket classifier
    if "intervals" in table:
        intervals = table.get("intervals") or []
        if intervals and all(
            isinstance(i, dict) and "min" in i and "max" in i
            and "label" in i for i in intervals
        ):
            matches.append(FamilyMatch(
                family_kind="interval_bucket_classifier",
                match_confidence=0.95,
                extracted_spec={"intervals": intervals},
                rationale=f"explicit intervals[] with {len(intervals)} buckets",
            ))

    # Generic key-value mapping → lookup_table
    if "mapping" in table:
        mapping = table.get("mapping") or {}
        if isinstance(mapping, dict) and mapping:
            matches.append(FamilyMatch(
                family_kind="lookup_table",
                match_confidence=0.90,
                extracted_spec={"table": mapping},
                rationale=f"explicit mapping with {len(mapping)} entries",
            ))

    # Threshold rule shape: {threshold, operator, true_label, false_label}
    if all(k in table for k in
            ("threshold", "operator", "true_label", "false_label")):
        matches.append(FamilyMatch(
            family_kind="threshold_rule",
            match_confidence=0.95,
            extracted_spec={
                "threshold": table["threshold"],
                "operator": table["operator"],
                "true_label": table["true_label"],
                "false_label": table["false_label"],
            },
            rationale="explicit threshold rule shape",
        ))

    if not matches:
        # Fall through with a low-confidence signal → U3 residual
        matches.append(FamilyMatch(
            family_kind="lookup_table",
            match_confidence=0.20,
            extracted_spec={"table": {}},
            rationale="no recognizable structure; fallback to U3",
        ))
    return matches
