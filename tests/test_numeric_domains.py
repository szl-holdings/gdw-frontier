# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.diagnostics import DiagnosticSnapshot
from app.models import RiskControlRequest, RiskControlResponse
from app.quant_core import release_score_breakdown
from app.risk_control import conformal_risk_threshold


class NumericDomainTests(unittest.TestCase):
    def score(self, **overrides):
        values = {
            "p95_latency_ms": 200.0,
            "error_rate": 0.0,
            "missing_receipt_rate": 0.0,
            "weight_validation_passed": True,
            "cardinality_risk": 0.0,
        }
        values.update(overrides)
        return release_score_breakdown(**values)

    def test_nonfinite_telemetry_never_becomes_a_release_score(self):
        for field in ("p95_latency_ms", "error_rate", "missing_receipt_rate", "cardinality_risk"):
            for value in (math.nan, math.inf, -math.inf):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.score(**{field: value})

    def test_telemetry_domain_violations_are_not_silently_clipped(self):
        for field in ("p95_latency_ms", "error_rate", "missing_receipt_rate", "cardinality_risk"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.score(**{field: -0.01})
        for field in ("error_rate", "missing_receipt_rate", "cardinality_risk"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.score(**{field: 1.01})

    def test_weight_domain_and_arithmetic_overflow_fail_closed(self):
        for value in (math.nan, math.inf, -math.inf, -1.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.score(weights={"latency": value})
        with self.assertRaises(ValueError):
            self.score(p95_latency_ms=4_000.0, weights={"latency": 1e308})

    def test_weight_validation_must_be_a_boolean(self):
        for value in ("false", 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.score(weight_validation_passed=value)

    def test_valid_scores_preserve_existing_formula_and_monotonicity(self):
        self.assertEqual(self.score()["score"], 98.0)
        self.assertEqual(self.score(weight_validation_passed=False)["score"], 48.0)
        baseline = self.score(p95_latency_ms=0.0)["score"]
        for value in (100.0, 200.0, 2_000.0, 4_000.0, 8_000.0):
            current = self.score(p95_latency_ms=value)["score"]
            self.assertLessEqual(current, baseline)
            baseline = current

    def test_conformal_rank_beyond_sample_refuses_finite_cutoff(self):
        result = conformal_risk_threshold([0.2] * 20, alpha=0.01)
        self.assertIsNone(result["threshold"])
        self.assertEqual(result["evidence_status"], "UNAVAILABLE")
        self.assertEqual(result["target_coverage"], 0.99)
        self.assertTrue(any("rank" in reason for reason in result["assumptions"]))
        RiskControlResponse.model_validate(result).model_dump_json()

    def test_conformal_cutoff_uses_valid_finite_sample_order_statistic(self):
        result = conformal_risk_threshold(
            [0.25, 0.1, 0.2, 0.05, 0.15], alpha=0.2, minimum_samples=5
        )
        self.assertEqual(result["threshold"], 0.25)
        self.assertEqual(result["evidence_status"], "MEASURED")
        boundary = conformal_risk_threshold([0.2] * 20, alpha=1.0 / 21.0)
        self.assertEqual(boundary["threshold"], 0.2)
        self.assertEqual(boundary["evidence_status"], "MEASURED")

    def test_conformal_score_domain_is_finite_and_bounded(self):
        for value in (math.nan, math.inf, -math.inf, -0.01, 1.01):
            with self.subTest(value=value), self.assertRaises(ValueError):
                conformal_risk_threshold([value] * 20, alpha=0.05)

    def test_conformal_parameters_and_empty_calibration_fail_closed(self):
        for value in (math.nan, math.inf, -math.inf, 0.0, 1.0):
            with self.subTest(alpha=value), self.assertRaises(ValueError):
                conformal_risk_threshold([0.2] * 20, alpha=value)
        for value in (0, -1, 1.5, True):
            with self.subTest(minimum_samples=value), self.assertRaises(ValueError):
                conformal_risk_threshold([0.2] * 20, alpha=0.1, minimum_samples=value)
        empty = conformal_risk_threshold([], alpha=0.1)
        self.assertIsNone(empty["threshold"])
        self.assertEqual(empty["evidence_status"], "UNAVAILABLE")

    def test_request_rejects_invalid_calibration_before_endpoint_execution(self):
        for value in (math.nan, math.inf, -math.inf, -0.01, 1.01):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                RiskControlRequest(nonconformity_scores=[value] * 20)

    def test_diagnostic_model_rejects_infinite_latency(self):
        valid = {
            "snapshot_id": "finite-domain-regression",
            "p50_latency_ms": 100.0,
            "p95_latency_ms": 200.0,
            "p99_latency_ms": 300.0,
            "error_rate": 0.0,
            "inflight_requests": 0,
            "missing_receipt_rate": 0.0,
            "weight_validation_passed": True,
        }
        for field in ("p50_latency_ms", "p95_latency_ms", "p99_latency_ms"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                DiagnosticSnapshot.model_validate({**valid, field: math.inf})

    def test_http_boundary_refuses_bad_measurements_and_unattainable_rank(self):
        from fastapi.testclient import TestClient

        from app import api

        original_ledger = api.ledger.path
        original_exemplars = api.exemplar_store.path
        try:
            with tempfile.TemporaryDirectory() as directory:
                api.ledger.path = Path(directory) / "receipts.jsonl"
                api.exemplar_store.path = Path(directory) / "exemplars.jsonl"
                with TestClient(api.app) as client:
                    for value in ("nan", "inf", -0.01, 1.01):
                        with self.subTest(score=value):
                            result = client.post(
                                "/v1/gdw/quant/risk-control",
                                json={"nonconformity_scores": [value] * 20},
                            )
                            self.assertEqual(result.status_code, 422)
                    result = client.post(
                        "/v1/gdw/quant/risk-control",
                        json={"nonconformity_scores": [0.2] * 20, "alpha": 0.01},
                    )
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.json()["evidence_status"], "UNAVAILABLE")
                    self.assertIsNone(result.json()["threshold"])
                    result = client.get(
                        "/v1/gdw/score/release",
                        params={"p95_latency_ms": "inf", "error_rate": 0, "missing_receipt_rate": 0},
                    )
                    self.assertEqual(result.status_code, 422)
        finally:
            api.ledger.path = original_ledger
            api.exemplar_store.path = original_exemplars


if __name__ == "__main__":
    unittest.main()
