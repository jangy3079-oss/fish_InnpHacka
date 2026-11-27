"""Generate up to three grounded sentences; any unsuitable LLM result falls back."""

import json
import re
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from common.config_loader import ConfigError, ConfigLoader
from decision.actions import ACTIONS, canonical_action
from decision.first_aid import UnsafeFirstAid, ensure_safe
from .models import Contract, NotificationContext, NotificationResult
from .sources import ROOT, load_policy, retrieve

CAUSE_NAMES = {"R1": "고수온", "R2": "피난처 소실", "R3": "저산소", "R4": "적조",
               "R5": "질병", "R6": "작업 스트레스", "R7": "섭식 저하", "R8": "기생충성 질병"}


class ModelReply(Contract):
    message: str
    source_id: str | None


def number(value):
    # Compact and deterministic; no locale/rounding controlled by the LLM.
    if value == 0:
        return "0"
    text = format(value, ".4g")
    return text.rstrip("0").rstrip(".") if "." in text and "e" not in text.lower() else text


def sentences(message):
    # Decimal points are not sentence endings; output must otherwise end in punctuation.
    return [s for s in re.split(r"(?<!\d)[.!?。！？]+(?:\s+|$)", message) if s.strip()]


def facts(context, recommendation_summary=None):
    if context.baseline_status == "warming_up":
        return [f"가두리 {context.cage_id}번은 아직 평소 상태를 익히는 중입니다",
                "그래서 지금은 스트레스 정도와 의심 원인을 알려 드릴 수 없습니다"]
    first = f"가두리 {context.cage_id}번은 '{context.level}' 단계입니다"
    if context.rule_id:
        first = f"가두리 {context.cage_id}번은 '{context.level}' 단계이며 의심 원인은 {CAUSE_NAMES[context.rule_id]}입니다"
    evidence = [f"스트레스 지수 {number(context.stress_score)}점"]
    values = context.evidence
    if values.temp_surface is not None:
        evidence.append(f"표층 수온 {number(values.temp_surface)}℃")
    if context.rule_id == "R7" and values.feeding_change is not None:
        evidence.append(change_fact("먹이 반응", values.feeding_change))   # R7의 핵심 근거 (#232)
    elif values.activity_change is not None:
        evidence.append(change_fact("활동량", values.activity_change))
    elif values.lower_layers_ratio_x is not None:
        evidence.append(f"아래쪽(중·저층)에 모인 물고기 평소의 {number(values.lower_layers_ratio_x)}배")
    elif values.feeding_change is not None:
        evidence.append(change_fact("먹이 반응", values.feeding_change))
    second = "근거는 " + ", ".join(evidence) + "입니다"
    if recommendation_summary:
        second += "; " + recommendation_summary
    return [first, second]


def change_fact(label, value):
    # #232: '기준선 대비' 대신 '평소보다'. 숫자는 계산값 그대로.
    if value == 0:
        return f"{label} 평소와 같음"
    percent = abs(Decimal(str(value))) * 100
    direction = "감소" if value < 0 else "증가"
    return f"{label} 평소보다 {number(percent)}% {direction}"


def recommendation_fact(context, recommendation, revision):
    """Consume the existing LossCalculator result, not a new API recommendation schema."""
    if not isinstance(recommendation, dict) or recommendation.get("config_revision") != revision:
        raise ValueError("recommendation must use the current config revision")
    levels = {"정상": "normal", "주의": "caution", "경고": "warning", "위험": "danger"}
    request = recommendation.get("request")
    if not isinstance(request, dict) or recommendation.get("currency") != "KRW":
        raise ValueError("recommendation must contain calculator request and KRW currency")
    if (context.baseline_status != "ready" or recommendation.get("cage_id") != str(context.cage_id)
            or recommendation.get("primary_rule_id") != context.rule_id
            or request.get("level") != levels[context.level]):
        raise ValueError("recommendation cage, cause and level must match the notification")
    if recommendation.get("mode") == "first_aid_required" and context.rule_id in ("R5", "R8"):
        return None  # No economic ranking or diagnosis-dispatch claim for disease-related results.
    if recommendation.get("mode") != "loss_comparison" or context.rule_id in ("R5", "R8"):
        raise ValueError("unsupported recommendation mode")
    options = recommendation.get("options")
    if not isinstance(options, list) or any(not isinstance(o, dict) for o in options):
        raise ValueError("recommendation options must be an array")
    if context.rule_id == "R7" or recommendation.get("recommendation_basis") == "R7_feeding_management":
        # #232: R7은 손해 최소가 아니라 급이 관리 권고다 (pipeline/analysis.py와 같은 규칙). 감량률 수치는 쓰지 않는다.
        managed = canonical_action(ConfigLoader().snapshot()["cause_rules"]["rules"]["R7"]["first_action"][0])
        if any(o.get("id") == managed and o.get("available") is True for o in options):
            # 문서 담당 제안(#152): 23℃대는 '중단'이 아니라 '줄이세요'
            return "평소 관리로 사료 양을 줄이세요" if managed == "reduce_feed" else f"평소 관리로 {ACTIONS[managed][1]}을 권합니다"
        return "지금 바로 권할 수 있는 사료 관리 방법이 계산되지 않았습니다"
    option_id = recommendation.get("lowest_loss_option")
    if option_id is None:
        if any(option.get("available") is True for option in options):
            raise ValueError("available options require a selected lowest-loss option")
        return "지금 바로 할 수 있는 대응 방법이 계산되지 않았습니다"
    selected = [o for o in options if o.get("id") == option_id]
    if not isinstance(option_id, str) or option_id not in ACTIONS or len(selected) != 1 or selected[0].get("available") is not True:
        raise ValueError("selected recommendation must be a known, available option")
    amount = selected[0].get("estimated_loss_krw")
    if type(amount) is not int:
        raise ValueError("selected option requires the calculator's integer KRW loss")
    return f"손해가 가장 적은 방법은 {ACTIONS[option_id][1]}이며 예상 손해는 약 {amount:,}원입니다(추정)"


def duplicate_key_guard(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate LLM response key")
        value[key] = item
    return value


class NotificationGenerator:
    def __init__(self, *, loader=None, policy_path=ROOT / "config/notify.yaml", source_root=ROOT, llm=None):
        self.loader = loader or ConfigLoader()
        self.policy_path, self.source_root, self.llm = Path(policy_path), Path(source_root), llm

    def generate(self, context, *, recommendation=None):
        raw = context.model_dump() if isinstance(context, NotificationContext) else context
        context = NotificationContext.model_validate(raw)
        snapshot = self.loader.snapshot()
        summary = None if recommendation is None else recommendation_fact(context, recommendation, snapshot.revision)
        policy, policy_hash = load_policy(self.policy_path)
        # Common config errors propagate. A missing reviewed passage suppresses advice, not facts.
        guidance_error = False
        try:
            citations = retrieve(context.rule_id, policy, snapshot, self.source_root, context.ts)
        except (ConfigError, UnsafeFirstAid, OSError, UnicodeError):
            citations, guidance_error = (), True
        base = ". ".join(facts(context, summary)) + "."
        candidates = []
        if policy.max_sentences >= 3:
            for citation in citations:
                third = f"{citation.scope}: {citation.quote.rstrip('.!?。！？')}."
                message = f"{base} {third}"
                try:
                    ensure_safe(message, snapshot)
                except UnsafeFirstAid:
                    continue
                if len(sentences(message)) <= policy.max_sentences and len(message) <= policy.max_characters:
                    candidates.append({"message": message, "source_id": citation.id})
        if not candidates:
            candidates = [{"message": base, "source_id": None}]
        template = candidates[0]
        ensure_safe(template["message"], snapshot)
        if len(template["message"]) > policy.max_characters:
            raise ConfigError("notification length policy cannot fit the validated facts")
        chosen, mode = template, "template"
        reason = "guidance_unavailable" if guidance_error else "no_reviewed_guidance" if not citations else "llm_disabled"
        if self.llm is not None:
            try:
                response = self.llm.complete([dict(c) for c in candidates])
            except Exception:
                # Provider errors may carry tokens, prompts or URLs; never expose their messages.
                reason = "llm_error"
            else:
                try:
                    parsed = ModelReply.model_validate(json.loads(response, object_pairs_hook=duplicate_key_guard))
                    ensure_safe(parsed.message, snapshot)
                    proposed = parsed.model_dump()
                    if proposed not in candidates:
                        reason = "ungrounded_response"
                    else:
                        chosen, mode, reason = proposed, "llm", None
                except UnsafeFirstAid:
                    reason = "blocked_output"
                except (ValueError, TypeError, ValidationError):
                    reason = "invalid_response"
        selected = tuple(c for c in citations if c.id == chosen["source_id"])
        return NotificationResult(cage_id=context.cage_id, ts=context.ts, message=chosen["message"],
            mode=mode, fallback_reason=reason, citations=selected, config_revision=snapshot.revision,
            generation_config_sha256=policy_hash)
