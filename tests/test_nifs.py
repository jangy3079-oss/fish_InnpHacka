import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from nifs.risa import _items, load_csv, parse_obs_time, to_keyframes, to_records

FIX = Path(__file__).parent / "fixtures" / "nifs_risa_sample.json"


class NifsRisaTest(unittest.TestCase):
    def setUp(self):
        self.rows = _items(json.loads(FIX.read_text(encoding="utf-8")))

    def test_items_found(self):
        self.assertEqual(len(self.rows), 5)
        self.assertEqual(_items({"response": {"body": {"items": {"item": self.rows}}}}), self.rows)

    def test_records(self):
        recs = to_records(self.rows, {"s0017": ["cage-003", "cage-004"], "s0021": ["cage-009"]})
        self.assertEqual(len(recs), 4)              # 표층·중층 × 가두리 2, 저층·점검·매핑 없음은 버림
        by = {(r.cage_id, r.metric.value): r for r in recs}
        self.assertEqual(by[("cage-003", "temp_surface")].value, 26.4)
        self.assertEqual(by[("cage-004", "temp_mid")].value, 22.1)
        self.assertEqual(by[("cage-003", "temp_mid")].ts, datetime(2026, 8, 12, 5, 0, tzinfo=timezone.utc))
        self.assertTrue(all(r.source.value == "env" for r in recs))

    def test_time_formats(self):
        exp = datetime(2026, 8, 12, 5, 30, tzinfo=timezone.utc)
        for d, t in [("2026-08-12", "14:30"), ("20260812", "1430"), ("2026-08-12", "2026-08-12 14:30:00")]:
            self.assertEqual(parse_obs_time(d, t), exp)

    def test_csv_to_keyframes(self):
        lines = ["관측소코드,관측일자,관측시각,관측층,수온"]
        for h in range(12):
            lines.append(f"s0017,2023-08-05,{h:02d}:00,1,{25 + h * 0.1:.1f}")
            lines.append(f"s0017,2023-08-05,{h:02d}:00,2,21.5")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "past.csv"
            p.write_text("\n".join(lines), encoding="cp949")
            kf = to_keyframes(load_csv(p), "s0017", datetime.fromisoformat("2023-08-05T00:00:00+09:00"), step_h=3)
        self.assertEqual([k[0] for k in kf["temp_surface"]], [0, 3, 6, 9])
        self.assertAlmostEqual(kf["temp_surface"][0][1], 25.1)
        self.assertEqual(kf["temp_mid"][0][1], 21.5)


if __name__ == "__main__":
    unittest.main()
