# 지표 멱등 수집 계약 (#198, #212)

## 요청과 영수증

`POST /ingest/metrics`, `Authorization: Bearer <전용 edge access token>`.
본문은 기존 MetricRecord 1~1000개 배열이며 생산자 형식은 바꾸지 않는다.
`ts`는 관측 5분 구간 시작 UTC이며 분석 결과 게시와 별개다.

```json
[{"cage_id":"3","ts":"2026-08-12T05:00:00Z","source":"env","metric":"temp_surface","value":26.4,"confidence":1.0}]
```

신규/중복 모두 HTTP201, `X-Data-Mode: database`다.

```json
{"persisted":true,"received_count":1,"inserted_count":0,"duplicate_count":1}
```

`received_count = inserted_count + duplicate_count`. 건수는 bool/문자열/소수가 아닌 정수다.
inserted는 실제 신규 행, duplicate는 이미 같은 내용으로 저장된 행이다.
전체 중복이면 inserted=0이어도 성공이다. DB 영수증이 요청 건수/합계와 맞지 않으면 API는503으로 거부한다.

## 멱등·원자성

identity는 `(DB cage_id, UTC ts, source, metric)`이다. API는 ID 매핑·UTC 정규화 뒤 batch 내부 중복을422로 거부한다.
RPC도 타입 변환 후 내부 중복을 거부한다. 내부 중복은 같은 값이어도 흡수하지 않는다.

- 기존 identity와 **value 및 confidence가 모두 같으면** 중복 성공이다.
- value 또는 confidence가 다르면409 `metric_conflict`이며 신규 행까지 **batch 전체 rollback**한다.
- value는 PostgreSQL jsonb 수/배열 동등성, confidence는 numeric 동등성으로 비교한다.
  숫자6과6.0, 같은 시각의 UTC/오프셋 표현은 같은 내용이다.
- PostgREST `POST /rest/v1/rpc/ingest_metrics`, 본문 `{"p_records":[...]}` 한 번으로 처리한다.
  RPC는 HTTP200 JSON 객체를 반환하고 API는 검증 후 HTTP201로 반환한다.
- RPC 안에서 키 순서대로 INSERT ON CONFLICT DO NOTHING 후 내용을 비교한다. UPDATE/덮어쓰기는 하지 않는다.
  READ COMMITTED에서 unique-key 삽입은 동시 writer를 기다리고 다음 SELECT의 새 snapshot으로 비교한다.
  더 강한 격리 수준의 serialization 실패는 성공으로 위장하지 않는다.503 후 원본 batch를 재시도한다.
- 여러 batch로 나눈 파일 전체는 하나의 transaction이 아니며 앞서 성공한 batch는 남는다.

## 인증과 오류

Supabase `/auth/v1/user`의 최신 보호 app_metadata에서 `role=edge`, DB 정수 배열 `ingest_cage_ids`를 확인한다.
user_metadata는 권한 근거가 아니다. 같은 사용자 Bearer를 RPC에 전달하고 private security-definer 함수가
JWT identity·edge 역할·모든 가두리 allow-list를 다시 검사한다. 반환은 건수뿐이다.
허용하지 않은 가두리의 기존 내용/존재를 비교·반환하지 않는다.

public wrapper는 security invoker, private 함수는 빈 search_path다. anon은 실행 불가이며
일반 어민/공무원은 edge 검사를 통과하지 못한다. edge에 metric SELECT/UPDATE/DELETE 권한을 추가하지 않는다.
기존 raw INSERT 권한은 보존하지만 새 멱등 계약은 RPC/API 경로에 적용된다. service_role로 우회하지 않는다.
실제 계정 승인·JWT 갱신/회수는 #207 운영 검증이다.

생산자 문자열은 `INGEST_CAGE_ID_MAP`으로 명시적으로 매핑한다. 양의 정수 문자열은 DB ID로 허용한다.
`cage-003`에서 숫자를 자동 추출하지 않는다.

| HTTP | detail.code 예시 | 의미 |
| --- | --- | --- |
| 401 | invalid_token | 누락/잘못된/만료 token |
| 403 | ingest_forbidden / cage_forbidden / storage_forbidden | edge·가두리 범위 거부 |
| 409 | metric_conflict | 같은 identity의 다른 내용, batch 전체 거부 |
| 422 | duplicate_in_batch / unknown_cage_id / storage_validation_failed | 모델·내부 중복·DB 검증 |
| 503 | storage_unavailable / upstream_unavailable | 설정/DB/RPC/영수증/연결 실패, 저장 결과 불명확 가능 |

모델 검증도 현재 API의 detail.code 형식을 사용한다. upstream body·토큰은 반사하지 않는다.
새 서버에서는 저장 후 응답 유실 시 **같은 원본 batch/파일**을 재전송해 확인할 수 있다.
값/ts/confidence를 바꿔 충돌을 피하지 않는다. 자동 재시도는 하지 않는다.

## #212 업로더 전환·배포

1. 새 uploader를 먼저/함께 배포한다. duplicate_count가 없는 구 영수증은 inserted=received일 때만 허용한다.
   새 영수증은 received=inserted+duplicate를 엄격히 검사한다. 구 서버의409를 성공으로 해석하지 않는다.
2. migration `20261128000100_idempotent_metric_ingest.sql` 적용 뒤 새 API로 전환한다.
   구 API·새 uploader는 호환된다. 새 API에 RPC가 없으면503이며 raw INSERT로 fallback하지 않는다.
3. 구 uploader는 전체 중복 성공을 실패로 볼 수 있으므로 구 uploader를 남긴 채 서버만 전환하지 않는다.
4. 원본 파일 재전송 재개는 새 서버 계약 확인 후 사용한다. 구 서버의 미확정 결과는 저장 확인이 필요하다.

`upload_counts()`는 received/inserted/duplicate 합계를 dict로 반환하며 CLI도 세 건수를 출력한다.
기존 `upload()`는 호환용으로 **확인된 건수(received)** int를 반환한다. 실제 신규 건수로 표시하지 않는다.
#212 업로더 확장은 이 영수증/진행 건수 의미를 유지한다. 학습·snapshot·원인 산출물 코드는 #212 담당 범위다.

기본 batch500, 최대1000. env·sonar·camera를 모두 보낼 때는 pipeline의 **metrics.jsonl**을 사용한다.
simulator records.jsonl만으로 sonar까지 수집됐다고 처리하지 않는다.

```sh
python -m scripts.upload_metrics data/out/heatwave_typhoon/metrics.jsonl --dry-run
# API: SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY / INGEST_CAGE_ID_MAP
# uploader: INGEST_ACCESS_TOKEN (명령줄 token 금지)
python -m scripts.upload_metrics data/out/heatwave_typhoon/metrics.jsonl --batch-size 500
```

## 검증

```sh
python -m unittest tests.test_ingestion -q
python -m unittest discover -s tests -q
npm test --prefix supabase/tests
docker run --name fish198-pg -e POSTGRES_HOST_AUTH_METHOD=trust -d postgres:17-alpine
python supabase/tests/ingest_concurrency.py --container fish198-pg
docker rm -f fish198-pg
```

API 테스트는 실제 FastAPI/공통 모델/업로더와 대체 Supabase HTTP를 사용한다.
PGlite 및 실제 PostgreSQL17에서 migration·RLS·영수증·전체 rollback을 검증한다.
동시성 도구는 **새 일회용 DB 전용**이며 두 세션의 실제 Lock 대기 후 commit/rollback한다.
동일 내용, 다른 내용(신규 행 rollback), 역순 batch, 첫 writer rollback을 검증한다.
hosted Supabase Auth/PostgREST·원격 migration/실계정/실장비 적용은 별도 #207 운영 검증이다.
