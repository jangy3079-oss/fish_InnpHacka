# 진단 접수 조회·중복 억제 (#203)

## GET /cages/{id}/diagnosis-request

현재 소유자 본인이 생성한 최신 접수 하나를 반환한다. `?request_key=<UUID>`로 특정 접수를 조회할 수 있다.
없는 접수는404 `diagnosis_request_not_found`, 접근 불가 가두리는404 `cage_not_found`, 소유자 아닌 RPC 접근은403이다.

```json
{"persisted":true,"id":10,"cage_id":3,"request_key":"11111111-1111-4111-8111-111111111111",
 "primary_cause_id":1,"status":"pending","dispatch_status":"pending",
 "created_at":"2026-11-28T00:00:00Z","observed_at":"2026-11-27T23:55:00Z"}
```

HTTP200, database/private,no-store/Vary:Authorization. created_at desc/id desc로 최신을 정한다.
packet·그래프·creator_id·자유 문구는 반환하지 않는다. 현재 원인 변경/만료·YAML 오류와 무관하게
저장된 요약을 읽으며 생성·전달·DB 갱신·quota 소비를 하지 않는다. 소유권 이전 시 다른 생성자의 접수를 노출하지 않는다.
공개 stable security-invoker RPC가 소유자 검사 private 함수를 호출한다. 사용자 Bearer를 유지하고 service_role로 우회하지 않는다.

## 생성·재시도·간격

기존 POST는 request_key UUID만 받으며201/pending/dispatch_status=pending이다. 외부 전달은 비활성이다.
동일 사용자/UUID는 원래 snapshot을 반환한다. 현재 원인 변경/만료·간격은 재시도를 막지 않는다.
새 UUID라도 같은 생성자·가두리·primary_cause_id가 이미 접수됐으면409 `data_conflict`다.
과거 중복 행을 삭제/통합하지 않고 새 생성만 억제한다.

다른 원인의 새 접수도 같은 소유자/가두리의 최근 접수와 간격을 검사한다.
API는 `first_aid.diagnosis_policy.min_request_interval_minutes`(60분 MVP 가정)를 RPC에 전달한다.
RPC가 허용하는 정책 인자 범위는1~1440분이다. 따라서60분은 현재 API 설정 기준이며,
직접 RPC까지 보호된 전역 DB 설정으로 고정된 값이라고 주장하지 않는다.
직접 RPC에도 동일 정책 강제가 필요하면 신뢰된 정책 저장/권한 경로를 후속으로 확정한다.
정확히 간격이 지난 다른 원인은 허용하지만 같은 원인의 새 UUID는 계속 거부한다.
가두리/어가 잠금 뒤 키·이벤트·최근 접수를 검사하고 기존 context version·R5/R8 검증을 거쳐 저장한다.
동시 같은 UUID는 같은 행, 다른 UUID의 중복/간격 내 접수는 하나만 저장한다.
409 뒤에는 조회/현재 관측 재확인과 사용자 확인을 거치며 자동으로 새 UUID·다른 원인을 전송하지 않는다.

## 화면·보관·문구

FirstAidCard 진입에는 GET만 호출한다. 조회 실패에는 신규 접수를 막고 인증/권한 확인을 안내한다.
현재 추천과 같은 observed_at 접수는 접수 상태, 다른 시각은 최근 이력으로 표시한다.
사용자 클릭만 POST하며 기존 sessionStorage UUID 재시도를 유지한다. 시연에서는 GET/POST를 하지 않는다.
화면 변경 시 늦은 응답을 무시한다. 실제 Supabase 세션/Bearer 연결은 #200/#207이며 시연 로그인이 이를 대체하지 않는다.

onset/censored·source/confidence·잘림·official 필터는 유지한다.
PR #156의 두 문구 수정안은 임시 설정에서 현재 스키마·출처·금칙어 검증으로 검토한다.
이 작업이 PR #156 병합을 뜻하지 않는다. source snapshot/config_revision은 유지하지만 당시 rendered 문구/archive는 저장하지 않는다.
완전한 당시 문구 재현·보관/삭제·수신기관·dispatcher는 #207 및 운영 후속이다.

## 배포·검증

YAML 필수 키·API·migration20261128000200을 함께 배포한다. 생성 RPC에 p_min_interval_minutes가 추가되고
옛 생성 서명의 authenticated 실행은 회수되므로 배포 중 구 API POST를 중지한다. RPC cache/실제 소유자·권한 회수는 원격에서 확인한다.
#34·#35·#45 구현은 변경하지 않는다.

```sh
python -m unittest tests.test_diagnosis -q
python -m unittest discover -s tests -q
npm test --prefix supabase/tests
npm run build --prefix frontend
docker run --name fish203-pg -e POSTGRES_HOST_AUTH_METHOD=trust -d postgres:17-alpine
python supabase/tests/diagnosis_concurrency.py --container fish203-pg
docker rm -f fish203-pg
```

동시성 도구는 새 일회용 DB 전용이다. 실제 Lock 대기 뒤 첫 세션을 commit하여 같은 UUID 재시도·새 UUID 중복·다른 이벤트 간격을 검사한다.
SQL은 조회 부작용·권한·정확한 간격 경계·stale context·불변 snapshot을 검증한다.
프론트는 production build 확인이며 브라우저 E2E/실제 로그인·hosted Auth/PostgREST·원격 배포를 완료했다고 보지 않는다.
