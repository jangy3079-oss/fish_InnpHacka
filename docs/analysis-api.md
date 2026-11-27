# 분석 저장·어민 API 2차 — #104

## 실제 데이터 흐름

`edge MetricRecord 수집(#10) → DB metric → 분석 작업자 → M5·원인·손익/공통 조치 → 원자적 DB 게시 → 상태/추천 조회 → 소유자 선택 저장`

분석 작업자는 기존 학습 모델·fusion·CauseEngine·LossCalculator/검토된 first_aid를 사용한다.
분석 결과를 만들지 않고 읽기 시점에 목업/시나리오 정답으로 채우는 경로는 없다.
기존 `python -m pipeline`은 로컬 시나리오 파일 생성용으로 유지한다.
실제 DB 경로는 아래 별도 명령이다. 기존 `stress --supabase`의 단독 stress 저장만으로는 새 API에 게시되지 않는다.

## 배포와 분석 명령

전체 migration(특히 `20261127000500_analysis_api.sql`)을 검토·적용해야 한다. 이 PR은 원격 DB나 계정을 변경하지 않는다.
운영자가 **전용 분석 사용자**에 보호된 app_metadata를 설정한다:

```json
{"role":"analysis","analysis_cage_ids":[3]}
```

user_metadata의 같은 값, 일반 어민·공무원·edge 토큰은 분석 게시에 사용할 수 없다.
설정은 SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, SUPABASE_ANALYSIS_TOKEN(전용 사용자의 access token)이다.
service_role 키를 사용하지 않는다. 토큰 만료/갱신·범위 승인/회수·작업 주기는 운영 담당자가 관리한다.

```sh
python -m pip install -r requirements.txt -r requirements-api-dev.txt
python -m pipeline.analyze_db --cage-id 3 --producer-id cage-003 --species 조피볼락 --model models/cage-003.pkl
# 특보 없음이 실제로 확인된 입력에만 아래 옵션을 지정
python -m pipeline.analyze_db --cage-id 3 --producer-id cage-003 --species 조피볼락 --model models/cage-003.pkl --advisory-state clear
```

모델은 기존 M5 학습 경로에서 만든 신뢰하는 로컬 파일이며 producer-id가 모델의 cage_id와 같아야 한다.
DB 정수 ID와 생산자 ID를 명시적으로 연결한다. 품종은 DB가 보장하지 않으므로 작업자가 명시한다.
모델 학습 기준선·설정 호환과 재학습/배포는 운영 합의 대상이다. 실행 프로세스의 M5 주기/윈도우와 설정이 다르면 게시하지 않는다.
기본 최근96시간, --hours는1~168시간, --to는 주기 경계의 시간대 포함 ISO 시각이다. 기본 cutoff는 현재 UTC의 완료된 주기다.
입력은 전역 최대50000개이며 잘리면 분석을 중단한다. 과거 시나리오를 운영 DB에 올리지 않는다(데모 격리는 #30 후속).

## 회차와 저장 계약

- analysis_input RPC: 지정 가두리의 실제 metric·stock·어가/해역 ID·입력/재고 버전을 읽는다.
- 한 작업의 검증된 snapshot을 fusion/원인/손익에 전달한다. 처리 중 설정 revision이 바뀌면 게시 전에 중단한다.
- 빠진 주기는 빈 관측으로 표시한다. 현재 행동 관측이 없으면 이전 점수를 새 점수로 재사용하지 않는다.
  baseline warming_up과 baseline은 준비됐으나 현재 관측이 부족한 insufficient_data를 구분한다.
- publish_analysis RPC: 입력을 다시 읽고 version을 확인한다. 가두리/어가 및 재고를 잠근 뒤
  stress_score·cause_estimate·recommendation·private.analysis_run을 한 트랜잭션으로 게시한다.
  실패하면 일부만 남지 않는다. ready인데 점수가 없는 회차는 공개 stress_score의 기존 제약을 우회하지 않고 private snapshot으로 표현한다.
- 게시 키는 입력/결과에서 결정적으로 만든 UUID다. 동일 요청은 기존 게시 확인을 반환한다.
  한 가두리/시각의 다른 결과나 기존 단독 저장과 충돌하면409이며 덮어쓰지 않는다. 변경된 모델/설정은 다음 새 주기에 게시한다.
- private 테이블은 일반 사용자에게 직접 열지 않는다. 공개 RPC는 invoker wrapper, 내부 definer는 읽기/게시/선택 권한을 각각 검사한다.

## GET /cages/{id}/status

등록 사용자 Bearer가 필요하다. 소유자·지정 해역 공무원은 기존 RLS 범위에서 조회한다.
가장 최근 **게시된 회차**의 상태와 같은 회차 원인을 반환한다. 자료가 없으면404다.

기존 필드에 analysis_id/config_revision/valid_until/model_version/baseline_windows/data_status,
metric_reliability·last_observation_at·stale/config_changed·level_held_due_to_missing을 추가한다.
evidence는 누락을 허용하는 숫자 맵이며 필수 중층/수온 값을 가짜0으로 채우지 않는다.
지수 관측이 없을 때 기존 경보 단계가 유지될 수 있으며 지수=null/insufficient_data/유지 플래그로 함께 표시한다.

metric_reliability는 지표별 good/limited/low/unavailable·관측 confidence·사용 센서를 제공한다.
융합 가중치와 관측 confidence로 계산하며, 누락은 confidence=null이다. 이는 **관측 신뢰도**이며 원인 확률/진단 확실성이 아니다.
기준은 thresholds.analysis의 가정값이다. 자료 유효기간은 마지막 실제 관측 시각을 기준으로 한다.
현재 설정과 다르거나 유효기간이 지났으면 stale이며 기존 값을 숨겨 새 정상처럼 표시하지 않는다.

## GET /cages/{id}/recommendation

소유자 또는 별도 financial_farm_ids 권한이 있는 지정 해역 공무원만 읽는다.
최신 게시 회차에 추천이 없으면404이며 이전 회차 추천을 대신 돌려주지 않는다.
no_current_cause/stock_required/stock_input_invalid 등 확인 가능한 이유를 오류 코드로 구분한다.

- 일반 원인:9개 손익 선택지를 보존하고 현재 원인의 대응 대상만 available로 둔다.
  추천은 대상 중 가능한 최소 손실이다. 시세/비용·효과 계수는 검증 전 가정값이며 실제 장비/허가 확인이 아니다.
- R5/R8: 재고가 없어도 검토된 공통 조치로 분기한다. 비용/경제적 우승 선택지는 없으며 recommended_option=null이다.
  diagnosis_request_required=true이며 실제 접수는 소유자 #28 POST다. 분석 작업자가 소유자를 사칭해 접수/외부 발송하지 않는다.
- 해역 동시 계획·지원/보험 적용 확인은 아직 연동하지 않아 미확인으로 둔다. 방류를 임의로 허용하거나 지원금을 차감하지 않는다.
- options에는 그룹별 산식/가정/출처/불가 이유가 보존된다. 정수 원과 정확한 중간 금액 문자열 계약은 #27을 따른다.
- stock_changed/config_changed/stale/chosen_option/selectable을 함께 반환한다. 과거 계산은 바꾸지 않지만 선택 가능 여부를 명확히 표시한다.

상태·추천은 개별 HTTP 호출이므로 그 사이 새 게시가 가능하다. 프론트는 analysis_id를 비교해 같은 회차로 맞춰 표시하고,
selectable=false를 비활성화해야 한다. 서버는 오래된 회차 선택을 다시 거부한다.

## POST /recommendations/{id}/choice

```json
{"option_id":"stop_feed","request_key":"11111111-1111-4111-8111-111111111111"}
```

소유자만 저장한다(공무원은 읽기만). 기존 action alias는 canonical ID로 변환한다.
현재 최신 회차·유효기간·설정 revision·재고 version과 available을 확인한 뒤
private.recommendation_choice와 recommendation.chosen_option을 원자적으로 기록한다.
같은 키/추천/선택 재시도는 원래 접수를 반환하고, 키 충돌/다른 선택 덮어쓰기는409다.
이미 저장한 선택을 수정하는 기능은 이번 범위가 아니다. 처음 선택에는 UUID를 만들고 재시도에는 같은 키를 유지한다.
성공은200/persisted=true/chosen_at/execution_status=not_executed다. 기록은 실제 조치·방류·진단 전달 실행이 아니다.

401은 인증 실패, 403은 권한 없음, 404는 게시/추천 없음, 409는 오래된/변경된 회차·재고·설정/중복 충돌,
422는 잘못되거나 불가능한 선택, 503은 설정/업스트림/검증 실패다. 쓰기는 자동 재시도하지 않는다.
모든 실제 데이터 응답은 private,no-store와 Vary: Authorization이다. 약품/투약량 방어는 상태·추천·선택 응답에도 적용한다.

## 진단 접수와 기존 흐름

새 정상/자료 부족 회차는 과거 R5를 현재 원인처럼 새로 접수하는 것을 막는다. 기존 #28 요청 키의 재시도는 원시 관측을 유지한다.
외부 진단 전달·자동 호출·알림·수신 확인, 직접 서비스 계정 저장과의 운영 전환, 과거 설정 재현은 별도 합의가 필요하다.
public10개 테이블은 유지하며 추천에 snapshot 컬럼, private 분석/선택 테이블을 추가했다.
현재 /demo/scenario·지자체 집계·관리자 설정은 기존 목업이다. 사용자 역할·이슈별 범위를 섞지 않는다.

## 검증과 한계

```sh
python -m unittest tests.test_analysis_api tests.test_api -v
python -m unittest discover -s tests -q
npm ci --prefix supabase/tests
npm test --prefix supabase/tests
```

실제 학습 모델/합성 MetricRecord→분석→게시 payload, FastAPI/MockTransport, PGlite 실제 SQL/RLS로 검증한다.
원격 Supabase 적용·실계정 권한 변경·실제 Auth/PostgREST 전체 스택·운영 스케줄/현장 효과는 검증하지 않았다.
