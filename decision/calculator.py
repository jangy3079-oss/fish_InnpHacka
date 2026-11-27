"""Decimal loss accounting with auditable configured eligibility; no external writes."""

from datetime import timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext

from common.config_loader import ConfigError, ConfigLoader
from .actions import ACTIONS
from .schemas import ComparisonRequest

FORMULA = "L(a) = sum_g(N_g * P_g * W_g * m_g(a)) + C(a) - S(a)"


def dec(value):
    return Decimal(str(value))


def exact(value):
    return format(value, "f")


def release_check(request, rules):
    count = sum(s.count for s in request.stock)
    other = request.other_area_release_count
    total = None if other is None else count + other
    failures = []
    if request.species not in rules["species"]["allowed"]:
        failures.append({"code": "species_not_allowed", "message": rules["species"]["reason_if_not"]})
    for group in request.stock:
        cfg = rules["size_groups"][group.size_group]
        if group.count and not cfg["release_allowed"]:
            failures.append({"code": "size_not_allowed", "size_group": group.size_group,
                             "message": cfg.get("reason_if_not") or "해당 크기는 방류 대상이 아닙니다."})
    if total is None:
        failures.append({"code": "area_count_unknown", "message": "같은 해역의 다른 동시 방류 수량을 확인해야 합니다."})
    elif total > rules["simultaneous_limit"]["max_count"]:
        failures.append({"code": "simultaneous_limit_exceeded", "message": rules["simultaneous_limit"]["reason_if_not"]})
    # Config snapshots are immutable; return JSON-native independent copies.
    alternatives = []
    if total is not None and total > rules["simultaneous_limit"]["max_count"]:
        alternatives = [dict(v) for v in rules["simultaneous_limit"]["if_exceeded"]]
    return {"available": not failures, "scope": "configured_species_size_and_simultaneous_count_only",
            "species": request.species, "sea_area_id": request.sea_area_id,
            "requested_count": count, "other_area_release_count": other,
            "simultaneous_count": total, "max_count": rules["simultaneous_limit"]["max_count"],
            "reasons": failures, "alternatives": alternatives}


class LossCalculator:
    def __init__(self, loader=None):
        self.loader = loader or ConfigLoader()

    def compare(self, request):
        # Revalidate model instances too: in-place edits must not bypass validation.
        raw = request.model_dump() if isinstance(request, ComparisonRequest) else request
        request = ComparisonRequest.model_validate(raw)
        snapshot = self.loader.snapshot()
        configs = snapshot.to_dict()
        costs, release = configs["costs"], configs["release_rules"]
        expected = {v[0] for v in ACTIONS.values()}
        if set(costs["option_loss"]) != expected:
            raise ConfigError("costs.yaml must define exactly the nine supported decision actions")
        with localcontext() as arithmetic:
            # Covers finite YAML floats, bigint counts and bounded decimal user inputs.
            arithmetic.prec = 4096
            return self._compare(request, snapshot, costs, release)

    def _compare(self, request, snapshot, costs, release):
        groups = []
        for s in sorted(request.stock, key=lambda v: ("fry", "mid", "market").index(v.size_group)):
            cfg = costs["size_groups"][s.size_group]
            price = s.price_per_kg if s.price_per_kg is not None else dec(cfg["price_per_kg"])
            weight = s.avg_weight_kg if s.avg_weight_kg is not None else dec(cfg["avg_weight_kg"])
            groups.append({"size_group": s.size_group, "count": s.count, "price_per_kg": price,
                           "avg_weight_kg": weight, "insured": s.insured,
                           "price_source": "input" if s.price_per_kg is not None else "config_assumption",
                           "weight_source": "stock_input" if s.avg_weight_kg is not None else "config_assumption",
                           "stock_value_krw": dec(s.count) * price * weight})
        eligibility = release_check(request, release)
        result = {"cage_id": request.cage_id, "farm_id": request.farm_id, "sea_area_id": request.sea_area_id,
                  "currency": costs["currency"], "formula": FORMULA, "config_revision": snapshot.revision,
                  "config_loaded_at": snapshot.loaded_at.astimezone(timezone.utc).isoformat(),
                  "request": request.model_dump(mode="json"), "release_check": eligibility,
                  "rounding": "Decimal, round final option total to integer KRW with ROUND_HALF_UP",
                  "availability_scope": "configured_release_checks_and_caller_unavailable_actions",
                  "mode": "loss_comparison", "options": [], "lowest_loss_option": None,
                  "primary_rule_id": request.primary_rule_id}
        if request.primary_rule_id in ("R5", "R8"):
            result.update(mode="first_aid_required", reason="질병 계열은 손익 비교 대신 별도 1차 조치·진단 요청 흐름을 사용합니다 (#28).")
            return result
        mortality = dec(costs["expected_mortality_by_level"][request.level])
        ranked = []
        for public_id, (config_id, label) in ACTIONS.items():
            definition = costs["option_loss"][config_id]
            if definition.get("loss_rate") is not None:
                rate = dec(definition["loss_rate"])
            elif definition.get("mortality_factor") is not None:
                rate = mortality * dec(definition["mortality_factor"])
            else:
                rate = mortality
            terms, stock_loss, insured_loss = [], Decimal(0), Decimal(0)
            for g in groups:
                loss = g["stock_value_krw"] * rate
                stock_loss += loss
                if g["insured"]:
                    insured_loss += loss
                terms.append({**{k: exact(v) if isinstance(v, Decimal) else v for k, v in g.items()},
                              "loss_rate": exact(rate), "loss_krw": exact(loss)})
            cost = dec(costs["action_cost_krw"][config_id])
            reasons = list(eligibility["reasons"]) if public_id == "release" else []
            if public_id in request.unavailable_actions:
                reasons.append({"code": "operationally_unavailable", "message": request.unavailable_actions[public_id]})
            available = not reasons
            insurance = Decimal(0)
            if request.insurance_confirmed and config_id in costs["insurance"]["eligible_options"]:
                insurance = insured_loss * dec(costs["insurance"]["payout_rate"])
            subsidy = Decimal(0)
            if public_id == "release" and available and request.release_support_confirmed:
                subsidy = min(dec(release["subsidy"]["cap_per_farm_krw"]),
                              request.farm_subsidy_remaining_krw,
                              dec(eligibility["requested_count"]) * dec(release["subsidy"]["per_fish_krw"]))
            support = subsidy + insurance
            total = stock_loss + cost - support
            # Unavailable choices expose a clearly hypothetical calculation, never rank.
            option = {"id": public_id, "config_action_id": config_id, "label": label, "available": available,
                      "reason": "; ".join(r["message"] for r in reasons) or None, "reasons": reasons,
                      "estimated_loss_krw": int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP)) if available else None,
                      "calculation": {"formula": FORMULA, "hypothetical": not available, "groups": terms,
                                      "base_mortality_rate": exact(mortality), "level": request.level,
                                      "loss_definition": definition, "stock_loss_krw": exact(stock_loss),
                                      "action_cost_krw": exact(cost), "insurance_krw": exact(insurance),
                                      "subsidy_krw": exact(subsidy), "support_krw": exact(support),
                                      "exact_loss_krw": exact(total),
                                      "insurance_confirmed": request.insurance_confirmed,
                                      "insurance_payout_rate": str(costs["insurance"]["payout_rate"]),
                                      "subsidy_confirmed": request.release_support_confirmed,
                                      "subsidy_per_fish_krw": str(release["subsidy"]["per_fish_krw"]),
                                      "subsidy_cap_per_farm_krw": str(release["subsidy"]["cap_per_farm_krw"]),
                                      "farm_subsidy_remaining_krw": None if request.farm_subsidy_remaining_krw is None else exact(request.farm_subsidy_remaining_krw)}}
            result["options"].append(option)
            if available:
                ranked.append((total, public_id))
        if ranked:
            result["lowest_loss_option"] = min(ranked, key=lambda v: v[0])[1]
        return result
