# 질병 계열 공통 1차 조치·진단 접수 — #28

## 범위와 출처

R5/R8이 1순위인 손익 요청은 경제 비교 대신 검토된 공통 조치와 diagnosis_request_required=true를 반환한다.
계산 단계에서는 접수하지 않으며 dispatch_status=not_created다. 아래 POST가 인증·DB 관측을 확인해 저장한다.
외부 전달/알림·시료 접수·진단 결과 입력은 미구현이다. 실제 추천 HTTP에서 자동 호출하는 연결은 #104 후속이다.

first_aid.yaml의 source_status=official 항목만 선택하고 text가 notify/docs 내부의 지정된 요약 문서에 있는지 확인한다.
Markdown 강조/공백은 정규화한다. 이는 저장소 출처 요약과 문구 일치 확인이며 현행 고시나 개별 상황의 효과를 검증한 결과가 아니다.
새 문구·수치·약품 안내를 생성하지 않는다. 출처 경로 이탈/누락·문구 불일치는 오류다.
현재 사료·밀도·폐사체3개를 반환한다. 도구 분리는 pending, 질병 상황의 산소 보강은 partial이므로 문구를 보류한다.
사료 항목은 출처의 섭취가 활발하지 않을 때 공급 중단 문구다. 이름(감량)과 조건부 중단의 표현 및 보류 항목은 합의 대상이다.

응답의 문자열·키에 blocked_terms/dose_patterns를 적용한다. NFKC·대소문자·공백·zero-width 변형을 확인한다.
차단 시 안전한503으로 종료하며 차단 사전/원문 전체/오류 입력을 응답하지 않는다.
UUID/설정 해시 전용 필드의 유효한 형식은 우연히 5cc가 들어 있어도 투약량 텍스트로 취급하지 않는다.
이 사전은 완전한 약품 인식기가 아니다. 제한된 출처 문구·허용 필드와 함께 사용하는 방어다. LLM/알림 템플릿은 #34 후속이다.
커스텀 설정 묶음은 새 diagnosis_policy를 추가해야 한다. source_status가 없으면 pending으로 간주하며,
문구 검증·출처 상태가 준비되지 않으면 임의로 안전하다고 간주하지 않는다.

## POST /cages/{id}/diagnosis-request

```http
Authorization: Bearer <Supabase user access token>
Content-Type: application/json

{"request_key":"11111111-1111-4111-8111-111111111111"}
```

- UUID는 한 논리 요청에 동일하게 유지한다. 타임아웃/응답 유실 때 새 키를 만들지 않는다.
- 기존 note 목업은 지원하지 않는다. 자유 문구·시작 시점·원인·그래프를 클라이언트가 주입하지 않는다.
- 등록된 실제 가두리 소유자만 생성/재조회한다. 공무원·다른 어가·익명은 불가다.
- 최신 cause_estimate 시각의 후보를 확률/설정 순서로 정렬하고 1순위가 R5/R8이어야 한다.
  확률 합은1이어야 하며 중복 규칙·오래된/미래 원인은 거부한다.
- 같은 시각의 유효한 비정상 stress_score와 실제 행동 지표가 필요하다. 없으면 가짜 자료 대신409다.
- 기본값은 원인 시각 이전72시간, metric/stress 각각 최신1000개, 원인 유효기간60분이다.
  first_aid.diagnosis_policy의 가정값이며 배포 전 합의한다.

성공은201/persisted=true/status=pending/dispatch_status=pending이다. id·created_at·request_key와 packet을 반환한다.
packet은 의심 원인/확률·선택 원인 ID, 그래프 점/source/confidence, 관측 시작 시점, 한도 초과 플래그,
공통 조치/출처·설정 revision을 포함한다. private,no-store 및 Vary: Authorization을 적용한다.
X-Data-Mode=database만으로 성공을 판단하지 않는다. 접수 메시지는 실제 외부 전달을 주장하지 않는다.

onset_at은 과거로 연속된 비정상 지수 관측의 시작이지 실제 발병 시각이 아니다. 정상/누락/주기 공백에서 멈춘다.
앞선 정상 관측을 확인하지 못하면 onset_censored=true다. onset_policy에 문턱·주기·revision을 기록한다.
그래프는 원시 관측이며 0 채움·보간·융합을 하지 않는다. 센서별 source를 구분해 그린다.

## 저장·권한·재시도

public 테이블10개는 유지하고 private.diagnosis_request에 DB 원인·metric·stress 스냅샷을 저장한다.
공개 RPC는 security invoker wrapper이고 private 함수가 소유권을 확인하며 저장 시 가두리/어가 행을 잠근다.
직접 테이블 읽기/쓰기/삭제 권한은 열지 않는다. API는 사용자 Bearer를 전달하며 service_role을 쓰지 않는다.

1. diagnosis_context RPC로 DB 스냅샷과 context_version을 읽는다.
2. API가 관측·출처·문구 안전성을 확인한다. 실패하면 저장하지 않는다.
3. create_diagnosis_request RPC가 DB를 다시 읽어 version을 비교하고 저장한다. 변경됐으면409다.
4. 저장된 스냅샷에서 응답한다. 사용자/request_key가 같으면 기존 행을 반환하며 덮어쓰지 않는다.

같은 키를 다른 가두리에 재사용하면409다. 새 키는 같은 원인이라도 별도 요청이므로 이벤트별 자동 중복 키/빈도 제한은 합의 대상이다.
기존 키는 현재 원인이 달라지거나 오래돼도 현재 소유자 권한이 있으면 원래 관측을 재사용한다.
creation_config_revision은 최초 설정이고 문구/시작 시점은 현재 검증된 설정으로 렌더링해 각 revision을 표시한다.
당시 문구/계산까지 재현하는 설정 아카이브는 후속이다.

service_role에는 dispatcher용 읽기만 추가했다. status는 pending만 허용하며 수신자·전달 API·재시도·수신 확인·
보관/삭제는 별도 migration/권한 설계가 필요하다. 실제 외부 전송을 하지 않는다.

## 오류와 검증

401은 토큰 없음/무효, 403은 소유자 아님, 404는 가두리 없음/조회 불가다.
409는 최신 질병 원인/관측 없음·오래됨·버전/키 충돌, 422는 형식 오류, 503은 설정/출처/필터/업스트림 오류다.
이 경로의 검증 오류는 입력을 반사하지 않으며 쓰기를 자동 재시도하지 않는다.

```sh
python -m pip install -r requirements.txt -r requirements-api-dev.txt
python -m unittest tests.test_diagnosis tests.test_api tests.test_decision -v
npm ci --prefix supabase/tests
npm test --prefix supabase/tests
```

HTTP는 MockTransport, DB/RLS/RPC는 PGlite의 실제 SQL로 검증한다. 원격 Supabase migration·실계정·
실제 Auth/PostgREST 전체 스택·외부 전달·현장 효과는 검증하지 않았다.
