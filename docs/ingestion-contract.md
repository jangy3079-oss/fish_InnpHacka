# 지표 수집·저장 계약 — #10

## 브랜치와 선행 변경

최신 main에서 `feat/10-ingest-metrics`를 만들었다. 실행 가능한 기능을 검증하기 위해
아직 미병합인 #2(PR #47), #4(PR #58), #3(PR #67)를 **이 작업 브랜치에만** 통합했다.
main에는 병합하지 않았다. 선행 PR을 먼저 검토·병합하면 이 PR에서 신규 #10 변경을 비교하기 쉽다.
공통 MetricRecord와 DB 스키마를 다시 구현하지 않고 그대로 사용한다.

## 요청과 성공 응답

`POST /ingest/metrics`, `Authorization: Bearer <전용 edge 계정 access token>`.
본문은 MetricRecord 객체 **1~1000개 배열**이다. envelope는 없다.
공통 모델로 필수 6개 필드·10종 지표·UTC·비율·숫자를 검사한다.

```json
[
  {"cage_id":"3","ts":"2026-08-12T05:00:00Z","source":"env","metric":"temp_surface","value":26.4,"confidence":1.0}
]
```

저장 완료 뒤 HTTP 201:

```json
{"persisted":true,"received_count":1,"inserted_count":1}
```

`X-Data-Mode: database`는 이 경로가 DB 경로라는 뜻이고 저장 성공은 HTTP 201과 persisted로 확인한다.
그 외 #4 API는 목업이며 저장 결과를 `/status`에서 조회할 수 있는 기능은 #29 범위다.

## 인증과 RLS

- Supabase `/auth/v1/user`로 사용자 토큰을 검증하고, 보호된 app_metadata를 확인한다.
- role은 `edge`, ingest_cage_ids는 수집 가능한 **DB 정수 ID 배열**이어야 한다.
- 실제 insert에도 같은 사용자 Bearer 토큰을 전달한다. service_role로 RLS를 우회하지 않는다.
- 추가 migration은 이 edge 범위에 대해서만 metric INSERT를 허용한다.
  일반 어민·공무원의 지표 생성이나 edge의 SELECT/UPDATE/DELETE를 허용하지 않는다.
- 권한 메타데이터는 Auth 관리자가 설정하는 초안이다. 이 코드가 계정·권한을 자동 생성하지 않는다.

```json
{"role":"edge","ingest_cage_ids":[3]}
```

Auth 서버의 최신 메타데이터와 DB JWT claim이 모두 허용해야 한다. 권한 변경 후 토큰을 갱신한다.
DB에 직접 접근할 때 오래된 JWT의 권한이 남는 문제는 #68의 권한 회수 정책과 함께 결정한다.

## 문자열 가두리 ID와 DB 매핑

공통 모델의 문자열 ID를 유지한다. `"3"` 같은 양의 정수 문자열은 DB ID 3으로 변환한다.
`cage-003` 같은 생산자 ID는 서버 환경변수 `INGEST_CAGE_ID_MAP`에 명시적으로 매핑한다.
자동으로 `cage-003`에서 숫자를 추출하지 않는다. 매핑과 권한 검사를 통과해야 저장된다.
공통 예시 JSON의 cage-001을 시드 가두리 3에 넣으려면 `{"cage-001":3}`을 지정한다.
최종 ID 통일은 #62·#68과 함께 합의한다.

## 원자성·재전송·오류

인증·가두리 범위·중복 검사를 마친 뒤 한 번의 PostgREST bulk INSERT로 묶음을 저장한다.
레코드별 반복 insert나 upsert를 하지 않는다.

| HTTP | 의미 |
| --- | --- |
| 401 | 누락/잘못된/만료된 access token |
| 403 | edge 역할·가두리 범위 또는 DB RLS 거부 |
| 409 | 기존 (cage_id, ts, source, metric) 키 충돌. 해당 묶음 전체 거부 |
| 422 | 모델·배열·DB 제약조건 오류, 미등록 ID 또는 묶음 내 중복 |
| 503 | Supabase 설정·인증 서버·저장 요청 장애 |

묶음 안 중복은 UTC 정규화와 DB ID 매핑 뒤 확인한다. 같은 키의 다른 값도 덮어쓰지 않는다.
네트워크가 저장 후 끊기면 503이어도 저장 여부가 불명확할 수 있다. 이 경우 저장 상태를 확인하고
재시도해야 한다. 원본 upstream 오류·토큰을 응답에 내보내지 않는다.
FastAPI 모델 검증 422는 기본 detail 배열, 업무 오류는 detail의 code/message다. 공통 오류 형식은 합의 대상이다.

## 실행·업로드

```sh
python -m pip install -r requirements-api-dev.txt
```

서버에 SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY(또는 그 자리에 legacy anon key),
선택적으로 INGEST_CAGE_ID_MAP을 환경변수로 설정한다. secret/service_role 키를 사용하지 않는다.
`.env.example`은 형식 참고용이며 서버가 .env를 자동으로 읽지는 않는다.
두 Supabase migration을 적용한 로컬/검증 프로젝트와, 관리자가 승인한 전용 edge 계정이 필요하다.

PowerShell 설정 예:

```powershell
$env:SUPABASE_URL = 'https://YOUR_PROJECT.supabase.co'
$env:SUPABASE_PUBLISHABLE_KEY = 'YOUR_PUBLISHABLE_KEY'
$env:INGEST_CAGE_ID_MAP = '{"cage-003":3,"cage-001":3}'
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

업로더는 시뮬레이터의 records.jsonl과 공통 예시 JSON 배열을 그대로 읽는다.
파일 전체를 먼저 검증한 뒤 기본 500개씩 업로드한다. 토큰은 명령줄 옵션 대신 환경변수로 전달한다.

```sh
python -m scripts.upload_metrics scenarios/metric_records.example.json --dry-run
# #6 시뮬레이터가 통합된 작업 환경에서:
python -m simulator.run heatwave_typhoon --out data/out
python -m scripts.upload_metrics data/out/heatwave_typhoon/records.jsonl --dry-run
# INGEST_ACCESS_TOKEN 설정 후 실제 업로드:
python -m scripts.upload_metrics data/out/heatwave_typhoon/records.jsonl --batch-size 500
```

파일 전체는 여러 요청으로 나뉘므로 파일 단위 원자성을 보장하지 않는다. 실패 시 중단하며
그 전까지 성공한 묶음은 남는다. 자동 재시도하지 않고 확인된 저장 건수를 표시한다.

## 테스트와 제한

```sh
python -m unittest discover -s tests -v
npm ci --prefix supabase/tests
npm test --prefix supabase/tests
```

Python 테스트는 실제 FastAPI와 공통 모델·업로더를 호출하며 Supabase HTTP 응답은 대체한다.
PGlite 테스트는 PostgreSQL에서 migration·RLS·키 충돌·혼합 권한/잘못된 값의 전체 rollback을 실행한다.
실제 hosted Supabase의 Auth·PostgREST 전체 경로는 접속 정보가 없어 실행하지 않았다.
원격 migration 적용·실제 계정 권한 부여는 수행하지 않았다.

추가 호환성 확인: #6 시뮬레이터 heatwave_typhoon의 원본 records.jsonl 20,736개를
업로더→FastAPI→대체 Supabase HTTP 경로로 42개 묶음 전달했다. 동일한 전달 JSON을
PostgreSQL의 authenticated edge 역할에서 저장하고 최종 20,736개를 확인했다.
Python API/공통 모델/업로더 테스트 28개와 PostgreSQL 검증 73개가 통과했다.

## 합의 필요

1. **데이터·모델 + 백엔드·DB:** ID 매핑·측정 ts·키 중복 시 거부/무시/upsert, 정정 이력 정책.
2. **백엔드·DB + 기획/권한 운영:** edge 계정 발급·가두리 범위·토큰 갱신·회수 책임자.
3. **데이터·모델 + 백엔드:** 500/1000개 묶음 크기, 파일 부분 성공 후 재개·중복 처리,
   네트워크 결과 불명확 시 저장 확인 방법. 임의의 중복 무시를 기본값으로 정하지 않는다.
4. **프론트 + 백엔드:** 201 응답과 401/403/409/422/503 오류 형식. 모니터링 화면의 저장 성공 표시.
5. **데이터·모델 + 백엔드·DB:** #77의 stress_score 행 계약과 별개로 이 경로는 metric만 저장함을 확인.

근거: SDD 4.1·7·8, #10, #62, #68.
