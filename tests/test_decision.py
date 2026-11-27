import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import ValidationError

from common.config_loader import ConfigError, ConfigLoader, DEFAULT_DIRECTORY
from decision import ComparisonRequest, LossCalculator
from decision.actions import ACTIONS, canonical_action

ROOT = Path(__file__).resolve().parents[1]


def request(**changes):
    return {"cage_id": "3", "farm_id": "1", "sea_area_id": "1", "species": "조피볼락",
            "level": "danger", "other_area_release_count": 0,
            "stock": [{"size_group": "mid", "count": 1000, "avg_weight_kg": "0.3"}], **changes}


def options(result):
    return {r["id"]: r for r in result["options"]}


class LossCalculatorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(DEFAULT_DIRECTORY, self.root / "config")
        self.loader = ConfigLoader(self.root / "config")
        self.calculator = LossCalculator(self.loader)

    def change(self, name, mutate):
        path = self.root / "config" / (name + ".yaml")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        mutate(data)
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    def test_hand_calculated_nine_options(self):
        result = self.calculator.compare(request())
        actual = {k: v["estimated_loss_krw"] for k, v in options(result).items()}
        self.assertEqual(actual, {"keep": 1200000, "reduce_feed": 1140000, "stop_feed": 1080000,
                                  "oxygen": 990000, "density_control": 1280000, "net_depth": 1020000,
                                  "wintering": 1800000, "harvest": 1000000, "release": 3200000})
        self.assertEqual(result["lowest_loss_option"], "oxygen")
        self.assertEqual(set(actual), set(ACTIONS))

    def test_group_sum_and_cost_charged_only_once(self):
        data = request(stock=[{"size_group": "fry", "count": 100, "avg_weight_kg": "0.05", "price_per_kg": "8000"},
                              {"size_group": "mid", "count": 200, "avg_weight_kg": "0.3", "price_per_kg": "10000"}])
        oxygen = options(self.calculator.compare(data))["oxygen"]
        # (100*8000*.05 + 200*10000*.3) * .4*.7 + 150000 = 329200.
        self.assertEqual(oxygen["estimated_loss_krw"], 329200)
        calc = oxygen["calculation"]
        self.assertEqual(Decimal(calc["stock_loss_krw"]), Decimal("179200"))
        self.assertEqual(len(calc["groups"]), 2)
        self.assertEqual(Decimal(calc["action_cost_krw"]), 150000)

    def test_stock_weight_and_price_override_config_and_zero_price_is_valid(self):
        result = options(self.calculator.compare(request(stock=[{"size_group": "mid", "count": 10,
                                                                "avg_weight_kg": "1.2", "price_per_kg": "0"}])))["keep"]
        self.assertEqual(result["estimated_loss_krw"], 0)
        group = result["calculation"]["groups"][0]
        self.assertEqual(group["weight_source"], "stock_input")
        self.assertEqual(group["price_source"], "input")
        self.assertEqual(group["avg_weight_kg"], "1.2")

    def test_missing_weight_and_price_are_explicit_config_assumptions(self):
        group = options(self.calculator.compare(request(stock=[{"size_group": "fry", "count": 100}])))["keep"]["calculation"]["groups"][0]
        self.assertEqual((group["weight_source"], group["price_source"]), ("config_assumption", "config_assumption"))
        self.assertEqual(Decimal(group["stock_value_krw"]), 40000)

    def test_decimal_round_half_up_once_and_reproducible_formula(self):
        r = self.calculator.compare(request(stock=[{"size_group": "mid", "count": 1, "price_per_kg": "1.25", "avg_weight_kg": "1"}]))
        keep = options(r)["keep"]
        self.assertEqual(keep["estimated_loss_krw"], 1)  # 0.5 KRW, not binary float/banker's rounding.
        for option in r["options"]:
            c = option["calculation"]
            observed = sum(Decimal(g["count"]) * Decimal(g["price_per_kg"]) * Decimal(g["avg_weight_kg"]) * Decimal(g["loss_rate"]) for g in c["groups"])
            self.assertEqual(observed, Decimal(c["stock_loss_krw"]))
            self.assertEqual(observed + Decimal(c["action_cost_krw"]) - Decimal(c["support_krw"]), Decimal(c["exact_loss_krw"]))
        json.dumps(r, allow_nan=False)

    def test_release_exact_limit_with_other_cages_and_exceeded_alternatives(self):
        for other, allowed in ((99000, True), (99001, False)):
            r = self.calculator.compare(request(other_area_release_count=other))
            self.assertEqual(r["release_check"]["available"], allowed)
            release = options(r)["release"]
            self.assertEqual(release["available"], allowed)
            if not allowed:
                self.assertIsNone(release["estimated_loss_krw"])
                self.assertNotEqual(r["lowest_loss_option"], "release")
                self.assertTrue(release["calculation"]["hypothetical"])
                self.assertEqual(r["release_check"]["alternatives"], [{"type": "sequential", "interval_days": 7.0}, {"type": "spread", "min_distance_km": 1.0}])

    def test_release_unknown_area_and_wrong_species_are_explained(self):
        r = self.calculator.compare(request(other_area_release_count=None, species="다른 품종"))
        codes = {v["code"] for v in r["release_check"]["reasons"]}
        self.assertEqual(codes, {"area_count_unknown", "species_not_allowed"})
        self.assertFalse(options(r)["release"]["available"])

    def test_mixed_market_stock_blocks_whole_release_but_zero_group_does_not(self):
        for count, allowed in ((0, True), (1, False)):
            data = request(stock=[{"size_group": "mid", "count": 10}, {"size_group": "market", "count": count}])
            result = self.calculator.compare(data)
            self.assertEqual(options(result)["release"]["available"], allowed)
            self.assertTrue(options(result)["harvest"]["available"])

    def test_subsidy_uses_remaining_farm_cap_and_only_confirmed_allowed_release(self):
        self.change("release_rules", lambda d: d["subsidy"].update(cap_per_farm_krw=10000, per_fish_krw=10))
        r = self.calculator.compare(request(release_support_confirmed=True, farm_subsidy_remaining_krw="6000"))
        release = options(r)["release"]
        self.assertEqual(Decimal(release["calculation"]["subsidy_krw"]), 6000)
        self.assertEqual(release["estimated_loss_krw"], 3194000)
        for option in r["options"]:
            if option["id"] != "release":
                self.assertEqual(Decimal(option["calculation"]["subsidy_krw"]), 0)
        r = self.calculator.compare(request(release_support_confirmed=True, farm_subsidy_remaining_krw="6000", other_area_release_count=100000))
        self.assertEqual(Decimal(options(r)["release"]["calculation"]["subsidy_krw"]), 0)
        r = self.calculator.compare(request(farm_subsidy_remaining_krw="6000"))
        self.assertEqual(Decimal(options(r)["release"]["calculation"]["subsidy_krw"]), 0)

    def test_insurance_requires_confirmation_insured_stock_and_eligible_action(self):
        self.change("costs", lambda d: d["insurance"].update(payout_rate=0.5))
        stock = [{"size_group": "fry", "count": 100, "insured": True}, {"size_group": "mid", "count": 100, "insured": False}]
        r = self.calculator.compare(request(stock=stock, insurance_confirmed=True))
        # Insured fry: 100*8000*.05*.4*.5 = 8000, uninsured mid excluded.
        self.assertEqual(Decimal(options(r)["keep"]["calculation"]["insurance_krw"]), 8000)
        self.assertEqual(Decimal(options(r)["release"]["calculation"]["insurance_krw"]), 0)
        self.assertEqual(Decimal(options(r)["harvest"]["calculation"]["insurance_krw"]), 0)
        r = self.calculator.compare(request(stock=stock))
        self.assertEqual(Decimal(options(r)["keep"]["calculation"]["insurance_krw"]), 0)

    def test_negative_net_loss_is_not_silently_clamped(self):
        self.change("release_rules", lambda d: d["subsidy"].update(cap_per_farm_krw=5000000, per_fish_krw=5000))
        r = self.calculator.compare(request(release_support_confirmed=True, farm_subsidy_remaining_krw="5000000"))
        self.assertEqual(options(r)["release"]["estimated_loss_krw"], -1800000)

    def test_operational_blocks_aliases_and_no_available_option(self):
        self.assertEqual(canonical_action("emergency_release"), "release")
        self.assertEqual(canonical_action("stop_feeding"), "stop_feed")
        r = self.calculator.compare(request(unavailable_actions={key: "운영상 불가" for key in ACTIONS}))
        self.assertIsNone(r["lowest_loss_option"])
        self.assertTrue(all(not o["available"] for o in r["options"]))

    def test_disease_and_parasite_route_to_first_aid_without_loss_choice(self):
        for rid in ("R5", "R8"):
            r = self.calculator.compare(request(primary_rule_id=rid))
            self.assertEqual(r["mode"], "first_aid_required")
            self.assertEqual(r["options"], [])
            self.assertIsNone(r["lowest_loss_option"])

    def test_invalid_stock_inputs_are_rejected(self):
        changes = [{"stock": []}, {"stock": [{"size_group": "mid", "count": 0}]},
                   {"stock": [{"size_group": "mid", "count": 1}] * 2},
                   {"stock": [{"size_group": "mid", "count": True}]},
                   {"stock": [{"size_group": "mid", "count": -1}]},
                   {"stock": [{"size_group": "mid", "count": 1, "avg_weight_kg": "NaN"}]},
                   {"stock": [{"size_group": "mid", "count": 1, "price_per_kg": -1}]},
                   {"stock": [{"size_group": "mid", "count": 1, "avg_weight_kg": True}]},
                   {"other_area_release_count": True}, {"other_area_release_count": -1},
                   {"release_support_confirmed": True}, {"insurance_confirmed": "true"},
                   {"unavailable_actions": {"unknown": "no"}}, {"level": "invalid"}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.calculator.compare(request(**change))

    def test_mutated_model_is_revalidated(self):
        model = ComparisonRequest.model_validate(request())
        model.stock[0].count = -1
        with self.assertRaises(ValidationError):
            self.calculator.compare(model)

    def test_config_reload_reprices_next_call_and_preserves_previous_result(self):
        old = self.calculator.compare(request())
        self.change("costs", lambda d: d["size_groups"]["mid"].update(price_per_kg=20000))
        new = self.calculator.compare(request())
        self.assertEqual(options(old)["keep"]["estimated_loss_krw"], 1200000)
        self.assertEqual(options(new)["keep"]["estimated_loss_krw"], 2400000)
        self.assertNotEqual(old["config_revision"], new["config_revision"])

    def test_invalid_and_unsupported_config_fail_closed(self):
        self.change("costs", lambda d: d["insurance"].update(eligible_options=["unknown"]))
        with self.assertRaises(ConfigError):
            self.calculator.compare(request())
        shutil.copyfile(DEFAULT_DIRECTORY / "costs.yaml", self.root / "config/costs.yaml")
        def remove_density(d):
            d["option_loss"].pop("density_control")
            d["action_cost_krw"].pop("density_control")
            d["insurance"]["eligible_options"].remove("density_control")
        self.change("costs", remove_density)
        with self.assertRaises(ConfigError):
            self.calculator.compare(request())

    def test_cli_emits_json_and_rejects_invalid_input(self):
        source = self.root / "input.json"
        target = self.root / "output.json"
        source.write_text(json.dumps(request(), ensure_ascii=False), encoding="utf-8")
        command = [sys.executable, "-m", "decision", "--input", str(source), "--output", str(target)]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(output["lowest_loss_option"], "oxygen")
        self.assertEqual(len(output["config_revision"]), 64)
        source.write_text('{"unexpected":true}', encoding="utf-8")
        result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
