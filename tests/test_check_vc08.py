import unittest

from common.config_loader import ConfigLoader
from scripts.check_vc08 import DOSE, notification_cases, scan


class CheckVc08(unittest.TestCase):
    terms = {"category": ["항생제", "구충제"], "ingredient": ["프라지콴텔"]}

    def test_clean_sentences_pass(self):
        self.assertEqual(scan(["가두리 안의 병든 고기나 폐사어를 빨리 제거해야 한다.", "진단 요청을 접수했습니다."], self.terms), [])

    def test_dictionary_words_and_spacing_variants_are_found(self):
        hits = scan(["항 생 제를 쓰세요", "프라지콴텔 사용"], self.terms)
        self.assertEqual(len(hits), 2)

    def test_dose_patterns_without_dictionary_words_are_found(self):
        for text in ("어체중 1kg당 200mg", "2 g 투입", "20 PPM", "１ｍｌ"):
            with self.subTest(text=text):
                self.assertTrue(scan([text], self.terms))

    def test_numbers_without_units_are_not_flagged(self):
        self.assertEqual(scan(["3문장 이내, 2시간 뒤 확인"], self.terms), [])

    def test_notification_matrix_exercises_reviewed_candidates_and_failure_fallbacks(self):
        snapshot = ConfigLoader().snapshot()
        rows = notification_cases(snapshot)
        for rule in ("R5", "R8"):
            for level in ("주의", "경고", "위험"):
                cases = [r for r in rows if r["rule_id"] == rule and r["level"] == level]
                selected = [r for r in cases if r["mode"] == "llm"]
                self.assertEqual({c["citations"][0]["id"] for c in selected},
                                 {"feed_reduction", "density_control", "dead_fish_removal"})
                reasons = {r["route"]: r["fallback_reason"] for r in cases}
                self.assertEqual(reasons["provider_error"], "llm_error")
                self.assertEqual(reasons["invalid_reply"], "invalid_response")
        self.assertTrue(all(r["dispatch_status"] == "not_dispatched" for r in rows))
        self.assertTrue(all(r["config_revision"] == snapshot.revision for r in rows))
        self.assertEqual(scan([r["message"] for r in rows], self.terms), [])


if __name__ == "__main__":
    unittest.main()
