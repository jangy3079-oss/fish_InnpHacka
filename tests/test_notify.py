"""#34: grounded messages, deterministic facts, provider failures and dedup boundaries."""

import importlib.util
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import yaml

from common.config_loader import ConfigLoader
from decision.first_aid import ensure_safe
from notify import NotificationContext, NotificationGenerator
from notify.generator import sentences
from notify.llm import ChatCompletionClient
from notify.policy import AcceptedNotification, should_notify

ROOT = Path(__file__).resolve().parents[1]
TS = datetime(2026, 11, 27, 12, tzinfo=timezone.utc)
USER = UUID("00000000-0000-4000-8000-000000000001")


def context(**changes):
    values = {"cage_id": 3, "ts": TS, "baseline_status": "ready", "level": "경고",
              "stress_score": 64.0, "rule_id": "R1",
              "evidence": {"temp_surface": 26.4, "activity_change": -.28}}
    return NotificationContext.model_validate({**values, **changes})


class NotificationTests(unittest.TestCase):
    def comparison(self, **changes):
        from decision.calculator import LossCalculator
        request = {"cage_id": "3", "farm_id": "1", "sea_area_id": "1", "species": "조피볼락",
                   "level": "warning", "stock": [{"size_group": "mid", "count": 1000}],
                   "other_area_release_count": 0, "primary_rule_id": "R1", **changes}
        return LossCalculator().compare(request)

    def test_real_calculator_result_is_rendered_without_new_api_schema(self):
        comparison = self.comparison()
        result = NotificationGenerator().generate(context(), recommendation=comparison)
        self.assertIn("최소 손실 선택지", result.message)
        self.assertIn("예상 손실", result.message)
        self.assertEqual(len(sentences(result.message)), 3)
        self.assertNotIn("확정 손실", result.message)

    def test_wrong_cage_revision_level_or_unavailable_recommendation_rejected(self):
        import copy
        original = self.comparison()
        for changes in ({"cage_id": "4"}, {"config_revision": "different"},
                        {"primary_rule_id": "R4"}, {"request": {"level": "danger"}}):
            with self.assertRaises(ValueError):
                NotificationGenerator().generate(context(), recommendation={**original, **changes})
        unavailable = copy.deepcopy(original)
        selected = next(o for o in unavailable["options"] if o["id"] == unavailable["lowest_loss_option"])
        selected["available"] = False
        with self.assertRaises(ValueError):
            NotificationGenerator().generate(context(), recommendation=unavailable)

    def test_disease_recommendation_never_ranks_losses_or_claims_diagnosis_delivery(self):
        result = NotificationGenerator().generate(context(rule_id="R5"), recommendation=self.comparison(primary_rule_id="R5"))
        self.assertNotIn("최소 손실", result.message)
        self.assertNotIn("접수했", result.message)
        self.assertEqual(result.dispatch_status, "not_dispatched")

    def test_template_has_three_sentences_and_original_numbers(self):
        result = NotificationGenerator().generate(context())
        self.assertEqual(len(sentences(result.message)), 3)
        for phrase in ("64점", "26.4℃", "28% 감소", "2018년"):
            self.assertIn(phrase, result.message)
        self.assertEqual(result.mode, "template")
        self.assertEqual(result.dispatch_status, "not_dispatched")
        self.assertEqual(result.citations[0].id, "high_temp_monitoring")
        self.assertEqual(len(result.citations[0].source_sha256), 64)
        ensure_safe(result.message, ConfigLoader().snapshot())

    def test_disease_only_uses_official_actions_and_never_claims_diagnosis_sent(self):
        for rule in ("R5", "R8"):
            seen = []
            def choose(candidates):
                seen.extend(candidates)
                return json.dumps(candidates[-1], ensure_ascii=False)
            result = NotificationGenerator(llm=SimpleNamespace(complete=choose)).generate(context(rule_id=rule))
            self.assertEqual({candidate["source_id"] for candidate in seen},
                             {"feed_reduction", "density_control", "dead_fish_removal"})
            self.assertEqual(result.mode, "llm")
            self.assertNotIn("접수했", result.message)
            self.assertNotIn("전달했", result.message)
            self.assertIn("의심", result.message)

    def test_source_text_really_contains_every_selected_quote(self):
        for rule in ("R1", "R2", "R4", "R5", "R7", "R8"):
            result = NotificationGenerator().generate(context(rule_id=rule))
            for citation in result.citations:
                from notify.sources import flattened
                document = (ROOT / citation.source).read_text()
                self.assertIn(flattened(citation.quote), flattened(document))

    def test_missing_reviewed_rule_guidance_omits_advice(self):
        for rule in (None, "R3", "R6"):
            result = NotificationGenerator().generate(context(rule_id=rule))
            self.assertEqual(result.citations, ())
            self.assertEqual(len(sentences(result.message)), 2)
            self.assertEqual(result.fallback_reason, "no_reviewed_guidance")

    def test_warming_up_never_invents_score_or_cause(self):
        result = NotificationGenerator().generate(context(baseline_status="warming_up", stress_score=None,
                                                         level=None, rule_id=None))
        self.assertIn("학습 중", result.message)
        self.assertNotIn("0점", result.message)
        self.assertEqual(len(sentences(result.message)), 2)

    def test_missing_evidence_is_omitted_and_score_zero_is_observed(self):
        result = NotificationGenerator().generate(context(stress_score=0., level="정상", rule_id=None, evidence={}))
        self.assertIn("0점", result.message)
        self.assertNotIn("수온", result.message)
        self.assertNotIn("활동량", result.message)

    def test_model_may_select_only_an_exact_grounded_candidate(self):
        llm = SimpleNamespace(complete=lambda candidates: json.dumps(candidates[0], ensure_ascii=False))
        result = NotificationGenerator(llm=llm).generate(context())
        self.assertEqual(result.mode, "llm")
        self.assertIsNone(result.fallback_reason)

    def test_activity_increase_decrease_and_observed_zero_are_distinct(self):
        for value, phrase in ((-.4, "40% 감소"), (.2, "20% 증가"), (0., "변화 없음")):
            result = NotificationGenerator().generate(context(evidence={"activity_change": value}))
            self.assertIn(phrase, result.message)

    def test_invented_numbers_actions_source_and_dispatch_are_rejected(self):
        for mutate in (lambda c: {**c, "message": c["message"].replace("64점", "99점")},
                       lambda c: {**c, "message": c["message"] + " 모든 고기를 방류하세요."},
                       lambda c: {**c, "source_id": "made_up"},
                       lambda c: {**c, "message": "진단 요청을 이미 전달했습니다."}):
            llm = SimpleNamespace(complete=lambda candidates: json.dumps(mutate(candidates[0]), ensure_ascii=False))
            result = NotificationGenerator(llm=llm).generate(context())
            self.assertEqual(result.mode, "template")
            self.assertEqual(result.fallback_reason, "ungrounded_response")

    def test_blocked_words_doses_and_unicode_variants_fall_back(self):
        for message in ("항 생 제를 쓰세요.", "옥시테트라사이클린", "５ｍｇ", "5\u200bmg", "2㎎", "1 g/kg"):
            llm = SimpleNamespace(complete=lambda _: json.dumps({"message": message, "source_id": None}))
            result = NotificationGenerator(llm=llm).generate(context())
            self.assertEqual(result.mode, "template")
            self.assertEqual(result.fallback_reason, "blocked_output")
            ensure_safe(result.message, ConfigLoader().snapshot())

    def test_malformed_extra_or_duplicate_response_fields_fall_back(self):
        responses = ["not JSON", "[]", '{"message":"x","message":"y","source_id":null}',
                     '{"message":"x","source_id":null,"extra":"secret"}']
        for response in responses:
            result = NotificationGenerator(llm=SimpleNamespace(complete=lambda _: response)).generate(context())
            self.assertEqual(result.fallback_reason, "invalid_response")

    def test_provider_exception_does_not_expose_credentials(self):
        def fail(_):
            raise RuntimeError("secret-token-in-provider-response")
        result = NotificationGenerator(llm=SimpleNamespace(complete=fail)).generate(context())
        self.assertEqual(result.fallback_reason, "llm_error")
        self.assertNotIn("secret", result.model_dump_json())

    def test_context_rejects_nonfinite_false_numeric_and_incoherent_state(self):
        for changes in ({"stress_score": float("nan")}, {"stress_score": True}, {"cage_id": True},
                        {"ts": TS.replace(tzinfo=None)}, {"evidence": {"activity_change": float("inf")}},
                        {"baseline_status": "warming_up"}):
            with self.assertRaises(ValueError):
                context(**changes)

    def test_forged_model_instances_are_revalidated(self):
        forged = context().model_copy(update={"stress_score": 101.})
        with self.assertRaises(ValueError):
            NotificationGenerator().generate(forged)

    def test_source_removal_or_source_escape_suppresses_advice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notify/docs").mkdir(parents=True)
            policy = yaml.safe_load((ROOT / "config/notify.yaml").read_text())
            target = root / "policy.yaml"
            target.write_text(yaml.safe_dump(policy, allow_unicode=True))
            result = NotificationGenerator(source_root=root, policy_path=target).generate(context())
            self.assertEqual(result.fallback_reason, "guidance_unavailable")
            self.assertEqual(len(sentences(result.message)), 2)
            policy["passages"][0]["source"] = "../outside.md"
            target.write_text(yaml.safe_dump(policy, allow_unicode=True))
            result = NotificationGenerator(source_root=root, policy_path=target).generate(context())
            self.assertEqual(result.citations, ())

    def test_two_sentence_policy_does_not_attach_unused_citations(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = yaml.safe_load((ROOT / "config/notify.yaml").read_text())
            policy["max_sentences"] = 2
            path = Path(directory) / "policy.yaml"
            path.write_text(yaml.safe_dump(policy, allow_unicode=True))
            result = NotificationGenerator(policy_path=path).generate(context())
            self.assertEqual(len(sentences(result.message)), 2)
            self.assertEqual(result.citations, ())


class DedupTests(unittest.TestCase):
    def row(self, **changes):
        return AcceptedNotification.model_validate({"recipient_id": USER, "cage_id": 3,
            "level": "경고", "accepted_at": TS, **changes})

    def test_exact_six_hour_boundary(self):
        row = self.row()
        self.assertFalse(should_notify(context(), USER, now=TS + timedelta(hours=6, microseconds=-1), history=[row]).allowed)
        self.assertTrue(should_notify(context(), USER, now=TS + timedelta(hours=6), history=[row]).allowed)

    def test_recipient_cage_and_level_are_independent(self):
        rows = [self.row(recipient_id=UUID(int=2)), self.row(cage_id=4), self.row(level="주의")]
        self.assertTrue(should_notify(context(), USER, now=TS, history=rows).allowed)
        self.assertTrue(should_notify(context(level="위험"), USER, now=TS, history=[self.row()]).allowed)

    def test_unordered_history_uses_last_committed_same_level(self):
        rows = [self.row(accepted_at=TS - timedelta(hours=1)), self.row(accepted_at=TS - timedelta(hours=8))]
        result = should_notify(context(), USER, now=TS, history=rows)
        self.assertFalse(result.allowed)
        self.assertEqual(result.next_allowed_at, TS + timedelta(hours=5))

    def test_future_history_and_naive_clock_are_rejected(self):
        for now in (TS - timedelta(seconds=1), TS.replace(tzinfo=None)):
            with self.assertRaises(ValueError):
                should_notify(context(), USER, now=now, history=[self.row()])

    def test_warming_up_does_not_schedule_an_alert(self):
        result = should_notify(context(baseline_status="warming_up", stress_score=None, level=None, rule_id=None), USER, now=TS)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "warming_up")

    def test_dedup_interval_is_read_from_yaml(self):
        with tempfile.TemporaryDirectory() as directory:
            shutil.copytree(ROOT / "config", directory, dirs_exist_ok=True)
            path = Path(directory) / "thresholds.yaml"
            path.write_text(path.read_text().replace("alert_dedup_hours: 6", "alert_dedup_hours: 2"))
            self.assertTrue(should_notify(context(), USER, now=TS + timedelta(hours=2),
                                          history=[self.row()], loader=ConfigLoader(directory)).allowed)


@unittest.skipUnless(importlib.util.find_spec("httpx"), "API HTTP dependency")
class ChatAdapterTests(unittest.TestCase):
    def client(self, handle):
        import httpx
        client = httpx.Client(transport=httpx.MockTransport(handle))
        self.addCleanup(client.close)
        return ChatCompletionClient("https://provider.test/v1/chat/completions", "configured-model", "test-secret",
                                    timeout=2, max_tokens=384, client=client)

    def test_real_http_response_parsing_uses_grounded_candidates(self):
        import httpx
        def handle(request):
            self.assertEqual(request.headers["Authorization"], "Bearer test-secret")
            payload = json.loads(request.content)
            self.assertFalse(payload["store"])
            candidates = json.loads(payload["messages"][1]["content"])["candidates"]
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(candidates[0], ensure_ascii=False)}}]})
        result = NotificationGenerator(llm=self.client(handle)).generate(context())
        self.assertEqual(result.mode, "llm")

    def test_timeout_http_failure_and_truncated_response_fall_back(self):
        import httpx
        def timeout(request):
            raise httpx.ReadTimeout("secret", request=request)
        for handle in (timeout, lambda _: httpx.Response(429, json={"error": "secret"}),
                       lambda _: httpx.Response(200, json={"choices": [{"finish_reason": "length"}]})):
            result = NotificationGenerator(llm=self.client(handle)).generate(context())
            self.assertEqual(result.fallback_reason, "llm_error")
            self.assertNotIn("secret", result.model_dump_json())


if __name__ == "__main__":
    unittest.main()
