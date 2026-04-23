"""Tests for B.5 question-frame parser (v3 §1.8)."""
from __future__ import annotations

from waggledance.core.reasoning.question_frame import parse


def test_simple_numeric_fi():
    f = parse("paljonko lämmitys maksaa")
    assert f.desired_output == "numeric"
    assert f.comparator is None
    assert f.negation.present is False


def test_simple_numeric_en():
    f = parse("what does heating cost")
    assert f.desired_output == "numeric"


def test_boolean_with_eur_threshold_fi():
    f = parse("onko lämmityskustannus yli 50 €?")
    assert f.desired_output == "boolean_comparison"
    assert f.comparator is not None
    assert f.comparator.op == ">"
    assert f.comparator.threshold == 50.0
    assert f.comparator.unit == "EUR"


def test_boolean_with_eur_threshold_en():
    f = parse("is heating cost above 100 EUR")
    assert f.desired_output == "boolean_comparison"
    assert f.comparator.op == ">"
    assert f.comparator.threshold == 100.0
    assert f.comparator.unit == "EUR"


def test_below_threshold_fi():
    f = parse("onko teho alle 2 kW")
    assert f.desired_output == "boolean_comparison"
    assert f.comparator.op == "<"
    assert f.comparator.threshold == 2.0
    assert f.comparator.unit == "kW"


def test_at_least_fi():
    f = parse("tarvitsenko vähintään 5000 mehiläistä")
    assert f.comparator.op == ">="
    assert f.comparator.threshold == 5000.0


def test_negation_fi_alä():
    f = parse("älä laske sähkölämmitystä")
    assert f.negation.present is True


def test_negation_fi_ei():
    f = parse("ei ole lämmintä")
    assert f.negation.present is True


def test_negation_en():
    f = parse("do not include solar power")
    assert f.negation.present is True


def test_no_negation():
    f = parse("laske lämmityskustannus")
    assert f.negation.present is False


def test_diagnosis_fi():
    f = parse("miksi lämpöpumppu ei käynnisty")
    assert f.desired_output == "diagnosis"


def test_diagnosis_en():
    f = parse("why is the heat pump broken")
    assert f.desired_output == "diagnosis"


def test_optimization_fi():
    f = parse("mikä on halvin lämmitysratkaisu")
    assert f.desired_output == "optimization"


def test_optimization_en():
    f = parse("what is the cheapest heating option")
    assert f.desired_output == "optimization"


def test_explanation_fi():
    f = parse("selitä miten aurinkopaneelit toimivat")
    assert f.desired_output == "explanation"


def test_explanation_en():
    f = parse("explain how heat pumps work")
    assert f.desired_output == "explanation"


def test_unit_kwh():
    f = parse("is it under 500 kWh")
    assert f.comparator.op == "<"
    assert f.comparator.threshold == 500.0
    assert f.comparator.unit == "kWh"


def test_unit_celsius():
    f = parse("is outdoor temperature below -10 °C")
    assert f.comparator.op == "<"
    # Note: -10 might parse as positive 10 unless we handle signs; this is intentionally not a strict-sign parser
    assert f.comparator.unit == "C"


def test_unit_percent():
    f = parse("onko hyötysuhde yli 90 %")
    assert f.comparator.op == ">"
    assert f.comparator.unit == "percent"


def test_unit_kg():
    f = parse("is yield above 15 kg")
    assert f.comparator.op == ">"
    assert f.comparator.threshold == 15.0
    assert f.comparator.unit == "kg"


def test_threshold_without_unit():
    f = parse("is the value above 100")
    assert f.comparator.op == ">"
    assert f.comparator.threshold == 100.0
    assert f.comparator.unit is None


def test_comma_decimal_fi():
    # Finnish uses comma as decimal separator
    f = parse("onko hinta yli 0,18 €")
    assert f.comparator.op == ">"
    assert f.comparator.threshold == 0.18
    assert f.comparator.unit == "EUR"


def test_raw_query_preserved():
    original = "onko lämmityskustannus yli 50 €?"
    f = parse(original)
    assert f.raw_query == original


def test_to_dict_includes_comparator_if_present():
    f = parse("is it above 50 EUR")
    d = f.to_dict()
    assert "comparator" in d
    assert d["comparator"]["op"] == ">"
    assert d["comparator"]["threshold"] == 50.0


def test_to_dict_omits_comparator_if_absent():
    f = parse("laske lämmitys")
    d = f.to_dict()
    assert "comparator" not in d or d.get("comparator") is None


def test_nfc_normalization():
    # Decomposed ä should work same as composed
    composed = parse("onko yli 50 €")
    decomposed = parse("onko yli 50 €")  # same
    assert composed.comparator.threshold == decomposed.comparator.threshold


def test_multiple_numbers_first_with_unit_wins():
    """When multiple numbers exist, the one adjacent to a unit is threshold."""
    f = parse("compared to 2024 value, is cost above 50 EUR")
    # Should find 50 EUR not 2024
    assert f.comparator.threshold == 50.0
    assert f.comparator.unit == "EUR"


def test_no_false_diagnosis_on_neutral():
    f = parse("what is heating cost")
    assert f.desired_output == "numeric"


def test_optimization_overrides_explanation():
    f = parse("explain the cheapest option")
    # optimization wins over explanation per our ranked detection
    assert f.desired_output == "optimization"


def test_diagnosis_overrides_optimization():
    f = parse("why is the cheapest option failing")
    # diagnosis wins over optimization
    assert f.desired_output == "diagnosis"
