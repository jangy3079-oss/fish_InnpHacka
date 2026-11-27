# 대응(action) ID 대조표

이슈 #90의 "action ID 연결 방식"을 위한 제안이다. 지금 같은 대응이 **세 곳에서 서로 다른 이름**으로 쓰이고 있다.
이름을 바꾸면 프론트·테스트에 영향이 있어, 이 문서는 현황 대조와 제안만 담고 **YAML 키는 바꾸지 않았다.**

## 현황

| 대응 | API·프론트 (`docs/api-contract.md`, `frontend/src/mocks`) | `config/costs.yaml` (option_loss·action_cost_krw) | `config/cause_rules.yaml` (first_action·compare_options) | `config/first_aid.yaml` |
| --- | --- | --- | --- | --- |
| 유지 | `keep` | `keep` | — | — |
| 사료 중단 | `stop_feed` | `feed_stop` | `stop_feeding` (R1, R4) | — |
| 사료 감량 | `reduce_feed` (choice 예시) | — | `feed_reduction` (R7), `feeding_adjust` (R6 급이 조절) | `feed_reduction` |
| 산소 강화 | `oxygen` | `oxygen_boost` | `oxygen_boost` (R1), `oxygen_supply` (R3) | `oxygen_boost` |
| 밀도 조절 | — | — | `density_control` (R3) | `density_control` |
| 그물 수심 조정 | `net_depth` | `net_depth_adjust` | `net_depth_adjust` | — |
| 월하장 이동 | `wintering` | `overwinter_move` | `overwinter_move` | — |
| 조기 출하 | `harvest` | `early_harvest` | `early_harvest` | — |
| 긴급 방류 | `release` | `release` | `emergency_release` | — |
| 진단 요청 | — | — | `diagnosis_request`, `parasite_exam_request` | — |
| 폐사체 수거 | — | — | — | `dead_fish_removal` |
| 도구 분리 | — | — | — | `tool_separation` (근거 부족) |

`config/costs.yaml`의 키는 `tests/test_config_loader.py`가 `release`, `keep`을 직접 쓴다.

## 불일치가 생기는 곳 (현재 config 안)

- 사료 중단: `costs.feed_stop` ↔ `cause_rules.stop_feeding`
- 긴급 방류: `costs.release` ↔ `cause_rules.compare_options.emergency_release`
- 산소: `cause_rules`의 R1은 `oxygen_boost`, R3은 `oxygen_supply`로 서로 다름

## 제안

1. **사용자에게 보이는 ID는 API·프론트 쪽(`keep`, `stop_feed`, `oxygen`, `net_depth`, `wintering`, `harvest`, `release`, `reduce_feed`)을 기준**으로 한다. 이미 #4 계약과 목업에 쓰이고 있어 바꾸는 비용이 가장 크다.
2. config 안에서는 한 대응에 한 이름만 쓴다. `costs`와 `cause_rules`의 이름을 맞추는 일은 #25·#27이 소비 코드를 쓰기 전에 하는 것이 싸다.
3. 이름을 맞출 때까지는 `decision` 모듈에 **ID 변환 표 한 곳**만 두고, 변환 표를 이 문서와 같게 유지한다.
4. 합의되면 이 문서의 표를 정식 이름 하나로 줄이고 `cause_rules.yaml`, `costs.yaml`을 같은 PR에서 바꾼다.

## 확정 전에 정할 것

- `feed_stop`(중단)과 `reduce_feed`(감량)을 다른 대응으로 볼지. 지금은 둘 다 필요하다 (R1·R4는 중단, R7은 감량).
- `diagnosis_request`는 대응 선택지인지, 별도 흐름(질병 계열)인지. SDD 6.5는 별도 흐름이다.
