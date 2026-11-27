import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from common import MetricName, MetricRecord, MetricSource


class MetricRecordTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "scenarios/metric_records.example.json"
        self.examples = json.loads(path.read_text(encoding="utf-8"))
        self.scalar = self.examples[1]
        self.distribution = self.examples[0]

    def test_shared_fixture_covers_contract_and_round_trips(self):
        adapter = TypeAdapter(list[MetricRecord])
        records = adapter.validate_python(self.examples)
        self.assertEqual({r.metric for r in records}, set(MetricName))
        self.assertEqual({r.source for r in records}, set(MetricSource))
        self.assertEqual(adapter.validate_json(adapter.dump_json(records)), records)

    def test_timestamp_normalized_to_utc(self):
        record = MetricRecord.model_validate({**self.scalar, "ts": "2026-11-27T18:00:00+09:00"})
        self.assertEqual(record.ts, datetime(2026, 11, 27, 9, tzinfo=timezone.utc))
        self.assertTrue(record.model_dump(mode="json")["ts"].endswith("Z"))

    def test_invalid_scalar_records_are_rejected(self):
        changes = [
            {"ts": "2026-11-27T09:00:00"}, {"ts": 1795770000},
            {"ts": "1795770000"},
            {"source": "simulator"}, {"metric": "unknown"},
            {"cage_id": ""}, {"cage_id": " "}, {"cage_id": 123},
            {"value": True}, {"value": "12.5"}, {"value": None},
            {"value": float("nan")}, {"value": float("inf")},
            {"value": [1.0]}, {"confidence": -0.01}, {"confidence": 1.01},
            {"confidence": True}, {"confidence": "0.9"}, {"confidence": float("nan")},
            {"unexpected": "field"},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                MetricRecord.model_validate({**self.scalar, **change})

    def test_all_six_fields_are_required(self):
        for key in self.scalar:
            with self.subTest(field=key), self.assertRaises(ValidationError):
                MetricRecord.model_validate({k: v for k, v in self.scalar.items() if k != key})

    def test_distribution_shape_and_ratios(self):
        for value in [0.5, [], [0.1, 0.2], [-0.1, 1.1], [True, 0], [float("nan")], ["0.5", 0.5]]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                MetricRecord.model_validate({**self.distribution, "value": value})
        # SDD also allows 1m bins; do not restrict the contract to three bins.
        MetricRecord.model_validate({**self.distribution, "value": [0.25] * 4})
        MetricRecord.model_validate({**self.distribution, "value": [0.333333] * 3})

    def test_quality_and_still_ratio_bounds(self):
        for metric in ["quality", "still_ratio"]:
            for value in [-0.1, 1.1]:
                with self.subTest(metric=metric, value=value), self.assertRaises(ValidationError):
                    MetricRecord.model_validate({**self.scalar, "metric": metric, "value": value})

    def test_zero_is_valid_observation_and_confidence(self):
        record = MetricRecord.model_validate({**self.scalar, "value": 0, "confidence": 0})
        self.assertEqual(record.value, 0)

    def test_missing_camera_does_not_require_placeholder(self):
        adapter = TypeAdapter(list[MetricRecord])
        records = adapter.validate_python([r for r in self.examples if r["source"] != "camera"])
        self.assertNotIn(MetricSource.CAMERA, {r.source for r in records})


if __name__ == "__main__":
    unittest.main()
