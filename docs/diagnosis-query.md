# 진단 접수 조회 (#203)

`GET /cages/{id}/diagnosis-request`는 현재 소유자 본인이 만든 최신 접수 요약을 반환한다.
`?request_key=<UUID>`로 특정 접수를 찾을 수 있다. 없는 접수는404 `diagnosis_request_not_found`,
접근 불가 가두리는404 `cage_not_found`, 소유자 아닌 RPC 접근은403이다.

```json
{"persisted":true,"id":10,"cage_id":3,"request_key":"11111111-1111-4111-8111-111111111111",
 "primary_cause_id":1,"status":"pending","dispatch_status":"pending",
 "created_at":"2026-11-28T00:00:00Z","observed_at":"2026-11-27T23:55:00Z"}
```

조회는 생성·수정·외부 전송 없이 저장된 결과와 시각만 읽는다. packet·그래프·생성자 원문은 반환하지 않는다.
현재 원인/설정이 바뀌어도 과거 접수 요약을 읽을 수 있으며, 문구를 재생성하지 않는다.
사용자 Bearer·소유권 검사, private/no-store/Vary:Authorization을 유지한다.

## 접수 규칙

- 같은 UUID 재시도는 원래 접수를 반환한다. 현재 원인 소멸도 기존 접수 재조회/재시도를 막지 않는다.
- **같은 소유자·가두리·관측 회차에는 한 번만 접수한다.** UUID를 바꾸거나 같은 회차의 R5/R8을 바꿔도409다.
- 다른 관측 회차는 최신 원인·유효기간·revision 검증을 통과하면 접수할 수 있다. 임의의60분 대기 제한은 없다.
- 게시 분석이 있는 가두리는 최신 회차/ready/R5·R8/유효기간/revision을 확인한다. 원인이 사라진 뒤 과거 cause 행으로 새 접수를 만들지 않는다.
- 게시 분석 없는 #28 원시 입력 경로는 기존 검증을 유지한다. 잠금 뒤 중복을 검사하므로 동시 접수도 한 건만 저장한다.
- POST의 기존 UUID 본문과 생성 RPC 인자는 바꾸지 않는다. 새 YAML 키도 추가하지 않는다.

화면 진입에는 GET만 호출한다. 조회 실패에는 신규 접수를 막고, 현재 회차 접수와 과거 이력을 구분한다.
사용자 클릭에서만 POST하며 응답 유실 때 같은 UUID를 유지한다. 시연에서는 실제 접수 API를 호출하지 않는다.
`pending`은 접수·전달 대기이며 외부 전달은 수행하지 않는다.

기존 API/YAML과 호환되는 migration20261128000200을 적용하면 된다. 실환경 인증 연결은 기존 #200/#207 범위다.
#34·#35·#45 구현은 변경하지 않는다.

## 변경 범위 검증

```sh
python -m unittest tests.test_diagnosis tests.test_api -q
npm test --prefix supabase/tests
node --test frontend/tests/diagnosis-client.test.mjs
```

별도 Docker 검증은 `diagnosis_concurrency.py`의 동일 관측 동시 접수1건만 실행한다.
전체 모델 재학습·기존 수집 경합·이미 병합된 문구 수정안의 재적용 테스트는 이 작업에서 반복하지 않는다.
