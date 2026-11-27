# 적조 특보·작업 기록 입력 (#45)

원인 규칙 R4(적조)와 R6(작업 스트레스)는 센서 밖의 정보가 있어야 판정할 수 있습니다.
- R4: 가두리 해역에 **적조 특보**가 나와 있는가
- R6: 최근에 **망갈이·선별 작업**을 했는가

판정 조건은 [원인 엔진 안내](cause-engine.md)의 #174 절에 있습니다. 이 문서는 두 정보를 DB에 넣고 분석에 연결하는 방법입니다.

## 흐름

```text
어민 앱 ── POST /cages/{id}/work-log ──▶ private.work_log ─┐
운영자 ── python -m pipeline.red_tide ──▶ private.red_tide_advisory ─┤
                                                              ▼
              analysis_input (work_logs, red_tide_advisories 포함)
                                                              ▼
   pipeline/analysis.py → pipeline.contexts.observed_contexts → 원인 엔진 CauseContext
```

두 테이블은 `private` 스키마에 있고 RPC로만 다룹니다. 분석 입력에 들어가므로 `input_version`이 기록 변경까지 잡고,
분석 중 기록이 바뀌면 `publish_analysis`가 409(40001)로 거부합니다.

## 작업 기록 API

`POST /cages/{id}/work-log` (가두리 소유자, `Authorization: Bearer <사용자 토큰>`)

```json
{"kind": "net_change", "performed_at": "2026-08-10T10:00:00+09:00", "request_key": "8f0d2c1e-..."}
```

| 필드 | 값 |
| --- | --- |
| `kind` | `net_change`(망갈이) · `sorting`(선별). `config/cause_rules.yaml` R6 `env.work_log`와 같다 |
| `performed_at` | 실제 작업한 시각. 시간대 필수. 7일 이전·5분 넘는 미래는 422 |
| `request_key` | 한 번 누를 때 만든 UUID. 응답을 못 받았으면 **같은 키**로 다시 보낸다 |

201 응답:

```json
{"persisted": true, "work_log_id": 12, "cage_id": 3, "kind": "net_change",
 "performed_at": "2026-08-10T01:00:00+00:00", "created_at": "2026-08-10T01:03:12+00:00"}
```

| 상태 | `detail.code` | 의미 |
| --- | --- | --- |
| 401 | `missing_token` · `invalid_token` | 로그인 필요 |
| 403 | `data_forbidden` | 소유자가 아님 (분석 작업자 계정도 기록할 수 없음) |
| 404 | `cage_not_found` | 가두리 없음·볼 수 없음 |
| 409 | `data_conflict` | 같은 `request_key`로 다른 내용을 보냄 |
| 422 | `invalid_work_log_request` · `invalid_data` | 요청 형식 오류, 종류·시각이 DB 검증에 걸림 |
| 503 | `invalid_upstream_response` 등 | 저장 확인 실패. 같은 키로 다시 보내면 안전 |

같은 작업을 다른 키로 두 번 보내면 행이 두 개 생깁니다. 원인 엔진은 그 시각까지의 **가장 최근 기록**만 쓰므로 판정은 같습니다.
작업 기록의 수정·삭제와 기록 목록 조회는 아직 없습니다.

## 적조 특보 적재 (운영자)

수과원 적조 정보는 공개 API를 확인하지 못해서, 운영자가 적조속보를 보고 CSV로 옮겨 넣습니다.

```csv
sea_area_id,stage,starts_at,ends_at,source,source_ref
1,주의보,2026-08-10T14:00:00+09:00,,국립수산과학원 적조속보,속보 제12호
1,주의보,2026-08-10T14:00:00+09:00,2026-08-14T18:00:00+09:00,국립수산과학원 적조속보,속보 제15호(해제)
```

```sh
python -m pipeline.red_tide advisories.csv --dry-run    # 검증만
SUPABASE_URL=... SUPABASE_SERVICE_KEY=... python -m pipeline.red_tide advisories.csv
```

- `stage`: `watch`(예비주의보) · `advisory`(주의보) · `warning`(경보). 한글 단계명도 받는다.
- 해제는 같은 (해역, 단계, 발령 시각) 행을 `ends_at`을 채워 다시 넣는다. 해제 시각과 출처만 갱신된다.
- `source_ref`에는 속보 번호처럼 원문을 다시 찾을 수 있는 값을 적는다.
- 적재는 `service_role` 전용 RPC(`load_red_tide_advisories`)다. 어민·분석 작업자 계정은 넣을 수 없다.

## 분석에서 쓰는 방법

`pipeline/analysis.py`가 `analysis_input`의 두 목록으로 주기마다 `CauseContext`를 만듭니다 (`pipeline/contexts.py`의 `observed_contexts`).

| 입력 | 분석 입력에 들어가는 범위 | 주기별 값 |
| --- | --- | --- |
| `work_logs` | 분석 창 시작 72시간 전부터 창 끝까지 | 그 주기 시각까지의 가장 최근 기록 → `work_log_at`·`work_log_type` |
| `red_tide_advisories` | 가두리 해역에서 분석 창과 겹치는 특보 | R4 `env.advisory_stages`(기본 주의보·경보) 특보가 그 시각을 덮으면 `True`, 아니면 `None` |

적조 특보가 없을 때 `False`(특보 없음)가 아니라 `None`(미확인)으로 둡니다. 자료가 끊긴 것과 특보가 없는 것을 구분할 수 없기 때문입니다.
R4는 `True`일 때만 후보가 되므로 판정 결과는 같고, 근거(evidence)에 미확인으로 남습니다.

R4·R6가 1순위일 때 손익 비교는 다음 선택지로 합니다. `red_tide_guideline`(적조 대응 요령)과 `recovery_monitoring`(회복 모니터링)은
손익 선택지가 아니라 안내 조치라서 비교에서 빠집니다 (`decision/actions.py`의 `GUIDANCE_ACTIONS`).

| 1순위 | 비교 선택지 |
| --- | --- |
| R4 적조 | 유지, 사료 중단 |
| R6 작업 스트레스 | 유지, 사료 감량 (급이 조절) |

## 확인

```sh
python -m unittest tests.test_work_log_red_tide tests.test_cause_r4_r6 -v
cd supabase/tests && npm ci && node run-tests.mjs      # work_log.sql 포함
```

## 범위 밖

- 수과원 적조 정보 자동 수집 (공개 API 확인 후)
- 작업 기록 수정·삭제·목록 조회, 프론트 입력 화면
- 적조 특보를 해역 지도·알림에 표시
