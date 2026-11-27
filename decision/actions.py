"""One API/cost/rule action mapping, following docs/action-ids.md."""

# Text and identifiers only: prices, rates, costs and policy limits live in YAML.
ACTIONS = {
    "keep": ("keep", "유지"),
    "reduce_feed": ("feed_reduction", "사료 감량"),
    "stop_feed": ("feed_stop", "사료 중단"),
    "oxygen": ("oxygen_boost", "산소 보강"),
    "density_control": ("density_control", "밀도 조절"),
    "net_depth": ("net_depth_adjust", "그물 수심 조정"),
    "wintering": ("overwinter_move", "월하장 이동"),
    "harvest": ("early_harvest", "조기 출하"),
    "release": ("release", "긴급 방류"),
}

# 원인 규칙의 first_action 중 손익 비교 선택지가 아닌 것: 화면·알림 안내로만 쓴다 (#45).
# immediate_alert(R2 즉시 알림), compare_options(R2 비교 목록 표시), red_tide_guideline(R4 적조 대응 요령),
# recovery_monitoring(R6 회복 모니터링).
GUIDANCE_ACTIONS = frozenset({"immediate_alert", "compare_options", "red_tide_guideline", "recovery_monitoring"})

ALIASES = {key: key for key in ACTIONS}
ALIASES.update({cost: public for public, (cost, _) in ACTIONS.items()})
ALIASES.update(stop_feeding="stop_feed", oxygen_supply="oxygen", emergency_release="release",
               feeding_adjust="reduce_feed")


def canonical_action(value):
    try:
        return ALIASES[value]
    except (KeyError, TypeError):
        raise ValueError("Unknown decision action") from None
