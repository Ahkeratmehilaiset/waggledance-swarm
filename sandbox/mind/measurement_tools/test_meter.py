"""Synthetic-only tests; no Mind modules, memory, provider or repository fixtures."""

import unittest

from meter import Card, Case, MeasurementError, run_pilot


class MeterTests(unittest.TestCase):
    def test_top3_scores_engine_order_not_all_candidates(self):
        cases = (Case("synthetic-a", ("gold",)), Case("synthetic-b", ("gold",)))
        cards = iter((Card("a", ("x", "y", "gold", "z")),
                      Card("b", ("x", "y", "z", "gold"))))
        result = run_pilot(cases, lambda query: next(cards))
        self.assertEqual((result["cases"], result["top3_hits"], result["top3_rate"]), (2, 1, 0.5))

    def test_generate_receives_query_not_gold_and_is_called_once(self):
        queries = []

        def generate(query):
            queries.append(query)
            return Card("synthetic", ("lesson",))

        result = run_pilot((Case("synthetic", ("lesson",)),), generate)
        self.assertEqual(queries, ["synthetic"])
        self.assertEqual(result["top3_hits"], 1)

    def test_utf8_bytes_exact_tokens_and_timing_boundary(self):
        ticks = iter((10, 2_000_010))
        calls = []

        def generate(query):
            calls.append("generate")
            return Card("\u00e4\U0001f642", ("lesson",))

        def tokenize(text):
            self.assertEqual(text, "\u00e4\U0001f642")
            calls.append("tokenize")
            return 7

        result = run_pilot((Case("synthetic", ("lesson",)),), generate,
                           token_count=tokenize, tokenizer_id="synthetic-exact-v1",
                           clock_ns=lambda: next(ticks))
        self.assertEqual(calls, ["generate", "tokenize"])
        self.assertEqual(result["card_bytes_total"], 6)
        self.assertEqual(result["card_tokens_total"], 7)
        self.assertEqual(result["card_tokens_mean"], 7)
        self.assertEqual(result["card_ms_mean"], 2.0)
        self.assertEqual(result["tokenizer_id"], "synthetic-exact-v1")

    def test_no_tokenizer_means_unknown_not_bytes_divided_by_four(self):
        result = run_pilot((Case("synthetic", ("lesson",)),), lambda query: Card("12345678", ()))
        self.assertIsNone(result["card_tokens_total"])
        self.assertIsNone(result["card_tokens_mean"])
        self.assertIsNone(result["tokenizer_id"])
        self.assertEqual(result["top3_rate"], 0.0)

    def test_validates_entire_sample_before_first_call(self):
        calls = []
        for cases in ((), (Case("a", ("ok",)), Case("b", ())),
                      (Case("a", (True,)),)):
            with self.subTest(cases=cases), self.assertRaises(MeasurementError):
                run_pilot(cases, lambda query: calls.append(query))
        self.assertEqual(calls, [])

    def test_duplicate_ranked_ids_are_not_silently_deduplicated(self):
        with self.assertRaisesRegex(MeasurementError, "invalid_card"):
            run_pilot((Case("a", ("gold",)),), lambda query: Card("synthetic", ("x", "x", "gold")))

    def test_empty_card_is_a_miss_not_a_dropped_case(self):
        result = run_pilot((Case("a", ("gold",)),), lambda query: Card("", ()))
        self.assertEqual((result["cases"], result["top3_hits"], result["card_bytes_total"]), (1, 0, 0))

    def test_failures_stop_once_without_raw_exception_or_retry(self):
        calls = []

        def generate(query):
            calls.append(query)
            raise ValueError("PRIVATE synthetic content, never emit")

        with self.assertRaisesRegex(MeasurementError, "^generation_failed$") as caught:
            run_pilot((Case("synthetic", ("gold",)),), generate)
        self.assertEqual(calls, ["synthetic"])
        self.assertTrue(caught.exception.__suppress_context__)

    def test_tokenizer_pair_and_counts_refuse_ambiguity(self):
        for counter, name in ((lambda text: 1, None), (None, "unused")):
            with self.subTest(name=name), self.assertRaises(MeasurementError):
                run_pilot((Case("a", ("gold",)),), lambda query: Card("", ()),
                          token_count=counter, tokenizer_id=name)
        for bad in (True, -1, 1.5):
            with self.subTest(bad=bad), self.assertRaisesRegex(MeasurementError, "invalid_token_count"):
                run_pilot((Case("a", ("gold",)),), lambda query: Card("", ()),
                          token_count=lambda text: bad, tokenizer_id="synthetic")

    def test_backwards_clock_refuses(self):
        ticks = iter((2, 1))
        with self.assertRaisesRegex(MeasurementError, "invalid_clock"):
            run_pilot((Case("a", ("gold",)),), lambda query: Card("", ()),
                      clock_ns=lambda: next(ticks))

    def test_output_is_aggregate_only(self):
        result = run_pilot((Case("secret synthetic query", ("secret-id",)),),
                           lambda query: Card("secret synthetic card", ("secret-id",)))
        self.assertNotIn("secret", repr(result))
        self.assertEqual(set(result), {
            "schema", "cases", "top3_hits", "top3_rate", "card_ms_total", "card_ms_mean",
            "card_ms_max", "card_bytes_total", "card_bytes_mean", "card_tokens_total",
            "card_tokens_mean", "tokenizer_id", "timing_scope",
        })


if __name__ == "__main__":
    unittest.main()
