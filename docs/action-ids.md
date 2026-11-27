# 대응(action) ID 기준 이름

이슈 #90에서 합의한 대응 ID다. 사용자에게 보이는 ID(API·프론트, #4 계약)를 기준으로 `config/*.yaml`의 이름을 맞췄다.
합의: 데이터·모델 동의 (#90). 백엔드·프론트는 이 PR에서 확인.

## 기준 이름

| 대응 | 기준 ID | 이전 config 이름 (바꾼 것) |
| --- | --- | --- |
| 유지 | `keep` | — |
| 사료 중단 | `stop_feed` | costs `feed_stop`, cause_rules `stop_feeding` |
| 사료 감량 | `reduce_feed` | cause_rules `feed_reduction`·`feeding_adjust`, first_aid `feed_reduction` |
| 산소 강화 | `oxygen` | costs·cause_rules·first_aid `oxygen_boost`, cause_rules `oxygen_supply` |
| 밀도 조절 | `density_control` | — |
| 그물 수심 조정 | `net_depth` | `net_depth_adjust` |
| 월하장 이동 | `wintering` | `overwinter_move` |
| 조기 출하 | `harvest` | `early_harvest` |
| 긴급 방류 | `release` | cause_rules `emergency_release` |
| 폐사체 수거 | `dead_fish_removal` | — (first_aid 1차 조치, 선택지 아님) |
| 작업 도구 소독 | `tool_disinfection` | first_aid `tool_separation` (항목명도 "작업 도구 분리" → "작업 도구 소독") |

## 결정된 것
- `stop_feed`(중단)와 `reduce_feed`(감량)는 별개 대응이다. R1·R4는 중단, R7은 감량. 수과원 2018 고수온 요령표에서 경계(27~28℃)부터 "사료 공급 중단"이 근거다.
- R7 알림은 "20% 감량" 수치를 쓰지 않는다. 20%(`feed_reduction_pct`)는 UC-04 평가용 가정값이다. 알림은 공식 표현("사료 투입량을 줄이세요")을 쓴다.
- `diagnosis_request`는 선택지가 아니라 별도 흐름이다 (SDD 6.5). 이름은 그대로.
- `tool_disinfection`은 공식 자료의 기구 소독 근거가 있어 `source_status: partial`로 올렸다.

## 코드에서 확인한 것
- 이전 이름(`stop_feeding` 등)을 쓰는 Python 코드는 없었다. API 목업의 `reduce_feed`만 쓰고 있다.
- `tests/test_config_loader.py`가 직접 쓰는 `release`, `keep`은 바뀌지 않았다.
- #27(손익 계산기)이 소비 코드를 쓰기 전에 이름을 맞췄다.
