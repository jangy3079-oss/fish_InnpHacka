import unittest

from scripts.check_vc08 import DOSE, scan


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


if __name__ == "__main__":
    unittest.main()
