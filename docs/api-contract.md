# API 계약 초안 — #4

> #197·#200·#201의 /me·재고 GET·검증 경계/원인 분포·관측 metadata는 [접근·재고·관측 계약](access-stock-observations.md)을 따른다.

> #36 관리자 설정 GET/PUT은 보호된 관리자 역할·revision·재전송 키를 사용하는 실제 파일 저장 경로다. [관리자 계약](admin-config-api.md)을 따른다.

> #44 추천의 deadline/deadlines는 게시 시점의 실행 기한·다음 판단 정보다. [기한 계약](recommendation-deadlines.md)을 따른다.

> #35 해역 두 GET은 공무원 인증·허용 해역의 실제 DB 집계다. nullable/부분 집계·방류 추천 기준은
> [해역 집계 API 계약](area-aggregates.md)을 따른다. 아래 기존 목업 응답은 #4 당시 기록이다.

> #30 시나리오 재생은 실제 합성 센서 분석으로 교체했다. `t`는 시간 단위, `mock=false/simulated=true/persisted=false`다.
> [시나리오 재생 API 계약](scenario-replay-api.md)을 따른다. 알림·R4/R6(#34/#45)은 별도 담당 범위다.

> #104 상태·추천 조회·선택 저장은 인증된 실제 게시 데이터 경로다. 선택에는 request_key UUID가 필요하다.
> [분석 저장·어민 API 계약](analysis-api.md)을 따른다. 아래 #4 목업 설명은 당시 기록이다.

> #28 진단 POST는 소유자 인증·DB 저장 경로이며201/pending이다. note 대신 request_key UUID를 보낸다.
> [진단 API 계약](diagnosis-api.md)을 따른다.
> #203의 GET 접수 요약과 관측 회차별 중복 억제는 [접수 조회 계약](diagnosis-query.md)을 따른다.

> `/ingest/metrics`는 실제 edge 인증·DB 저장 경로이며 [멱등 수집 계약](idempotent-ingestion.md)을 따른다.
> #198에서 동일 값은 중복 성공, 다른 값은 전체409이며 received=inserted+duplicate 응답을 제공한다.
> stock PUT·timeseries GET·echogram GET은 인증된 실제 데이터 경로이며 [어민 데이터 API 계약](farmer-data-api.md)을 따른다.
> 아래 목업 설명은 #4 시점의 기록이다. 나머지는 기존 목업이다.

근거: 제공된 SDD 8장. `/cages/3/status`의 필드·값은 문서 예시 그대로다.
다른 가두리의 status는 같은 fixture에서 cage_id만 바꾼다. 가두리 존재 여부는 조회하지 않는다.
그 외 본문 구조는 프론트와 검토할 제안이며 실제 비즈니스 기능은 후속 이슈에서 구현한다.

## 공통 동작

- 성공 JSON은 별도 `data` envelope 없이 직접 반환한다. 이미지 API는 SVG를 반환한다.
- 모든 응답에 `X-Data-Mode: mock` 헤더를 포함한다. Swagger에도 목업임을 명시한다.
- POST/PUT은 HTTP 200의 `MockReceipt`를 반환한다. `mock=true`, `persisted=false`,
  action, resource_id, received_count만 반환하며 저장·수정·진단 요청 발송은 수행하지 않는다.
- 요청 형식 오류는 FastAPI 기본 HTTP 422 `{"detail": [...]}`.
  빈 지표 객체·역전 시간 구간은 `{"detail": "설명"}`. 통일된 오류 envelope는 합의 대상이다.
- API ID는 SDD 예시에 맞춰 양의 정수다. `from`, `to`는 시간대가 있는 ISO 8601,
  둘 다 있으면 from ≤ to. 시계열의 범위는 양 끝 포함이다.
- 인증은 아직 없다. 합성 fixture만 로컬에서 사용하며 Supabase 인증·RLS는 #3 등 후속 작업이다.

## 경로와 현재 응답

| 메서드 | 경로 | 요청/응답 |
| --- | --- | --- |
| GET | `/health` | `{status: "ok", mode: "mock"}` |
| POST | `/ingest/metrics` | 비어 있지 않은 객체 배열 → MockReceipt. 현재 envelope만 검증 |
| GET | `/cages/{id}/status` | SDD 그대로의 CageStatus |
| GET | `/cages/{id}/timeseries?from&to&metric` | `{cage_id, metric, points: [{ts, value}]}`. metric 필수, 범위 밖은 빈 points |
| GET | `/cages/{id}/echogram?from&to` | `image/svg+xml` 정적 합성 이미지. 시간 범위는 검증만 하며 그림은 변하지 않음 |
| GET | `/cages/{id}/recommendation` | `{id, cage_id, ts, recommended_option, options}`. 손익 미계산은 null |
| POST | `/recommendations/{id}/choice` | `{option_id: "reduce_feed"}` → MockReceipt |
| PUT | `/cages/{id}/stock` | `{stock: [{size_group: "mid", count: 100, avg_weight_kg: 0.2}], insured: false}` → MockReceipt |
| GET | `/areas/stress-map` | `{ts, areas: [{area_id, name, level, stress_score, cage_count}]}`. 개별 어가 손익 제외 |
| GET | `/areas/{id}/release-demand` | `{area_id, ts, estimated_fish_count, cage_count}` |
| POST | `/cages/{id}/diagnosis-request` | `{note: ""}` → MockReceipt |
| POST | `/cages/{id}/work-log` | 실제 DB. `{kind, performed_at, request_key}` → WorkLogReceipt (201). [적조 특보·작업 기록 안내](red-tide-work-log.md) |
| POST | `/demo/scenario/{name}?t=0` | `{mock: true, scenario, t, status}`. 실제 재생하지 않음 |
| PUT | `/admin/config/{file}` | `{values: {warning: 60}}` → MockReceipt. YAML 파일 수정 없음 |

시나리오명: heatwave_typhoon / turbidity_event / disease_suspect / parasite_suspect.
설정명(확장자 제외): thresholds / cause_rules / release_rules / costs / first_aid.
세부 필드의 타입·필수 여부는 `/openapi.json`의 schema를 기준으로 한다.

## 프론트·데이터 담당과 합의 필요

1. **ID 충돌:** #2 초안은 cage_id 문자열, SDD status 예시는 정수다.
   #3 DB 키 결정에 맞춰 전체 API와 MetricRecord를 통일해야 한다.
2. **#2 연계:** 최신 main에는 #2가 없어 공통 모델을 중복 구현하지 않았다.
   ingest는 객체 배열까지만 검증한다. #2 병합 후 #10에서 `list[MetricRecord]`로 교체하고
   전체 성공/부분 실패·중복 전송 정책을 정한다. 현재 200은 실제 수집 성공을 뜻하지 않는다.
3. **비율 순서·단위:** 시계열 vertical_dist 예시는 #2 초안과 같은 [저층, 중층, 표층].
   구간 경계와 최종 순서·활동량 등 단위는 데이터 담당과 확정한다.
4. **빈 상태:** 현재 nullable score/level과 warming_up enum을 정의했으나 관련 fixture는 없다.
   측정 없음·권한 없음·존재하지 않는 ID의 응답, 센서 누락·워밍업 화면을 합의한다.
5. **추천·입력:** 선택지 ID, 예상 손익의 단위(현재 KRW)/null,
   재고·보험 필드, 진단 요청 본문과 실제 생성 시 201/202 여부를 합의한다.
6. **시연 시간:** t는 음수 아닌 숫자만 검증한다. 분/초/시나리오 기준 시각 중 단위를 정해야 한다.
   현재 모든 시나리오·t에서 동일한 status를 반환한다. #30에서 실제 결과로 교체한다.
7. **이미지·지도:** 에코그램을 바이너리로 받을지 URL JSON으로 받을지,
   지도 응답을 현재 요약 배열에서 GeoJSON으로 바꿀지 확정한다(#29·#35).
8. **오류·인증·CORS:** 공통 에러 구조, Supabase Bearer 토큰의 역할 구분,
   프론트 실제 origin을 정한다. 현재 인증 검사나 권한 확인 기능은 제공하지 않는다.

SDD cause 확률 예시 합은 0.9지만 원문 일치를 위해 그대로 반환한다.
실제 확률 정규화는 #25에서 구현한다. 이 PR은 #2·#3 등 미병합 변경을 포함하지 않는다.
