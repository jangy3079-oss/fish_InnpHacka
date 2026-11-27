# Supabase 스키마 — #3

SDD 7장 테이블 10개, RLS, 로컬 시연용 seed를 제공한다.
#104는 `20261127000500_analysis_api.sql`과 전용 분석 사용자 metadata가 필요하다. [운영/API 계약](../docs/analysis-api.md)을 확인한다.
설계·권한·합의 사항은 [DB 계약](../docs/database-contract.md)을 확인한다.
이 PR은 SQL 파일만 제공하며 원격 Supabase에 자동 적용하지 않는다.

#28 진단 접수는 `20261127000400_diagnosis_request.sql`을 포함한 전체 migration이 필요하다.
public 테이블10개는 유지하며 private 요청 저장소와 소유자 RPC를 추가한다. [진단 계약](../docs/diagnosis-api.md)을 확인한다.

## 로컬 Supabase에서 사용

Supabase CLI와 Docker가 설치된 로컬 환경에서 저장소 루트 기준:

```sh
# 최초 설정 시에만 실행 (config.toml이 이미 있다면 생략)
supabase init
supabase start
```

로컬 개발 DB를 초기화해 다시 구성할 경우에만 `supabase db reset --local`을 사용한다.
이 명령은 해당 로컬 프로젝트 데이터를 초기화하고 migrations → seed 순서로 적용한다.
원격 DB에 적용할 때는 팀이 프로젝트·인증·정책을 검토한 뒤 별도로 진행한다.
`tests/bootstrap.sql`은 테스트용 Auth 대체물이며 migration이나 실제 Supabase에 적용하지 않는다.

## Docker 없이 PostgreSQL 계약 테스트

Node.js 20 이상에서 실행:

```sh
npm ci --prefix supabase/tests
npm test --prefix supabase/tests
```

PGlite(PostgreSQL WASM) 메모리 DB에 migration과 seed를 실제 실행한다.
인증 역할·auth.users·auth.uid()/auth.jwt()는 bootstrap에서 최소 구성으로 대체한다.
RLS는 소유자/다른 어가/공무원/익명/service_role로 `SET ROLE`하여 검증한다.
실제 Supabase Auth의 JWT 서명 검증, PostgREST 요청, CLI 전체 스택은 이 테스트 범위가 아니다.
테스트 DB는 실행 종료 시 닫히며 원격 DB·사용자 데이터와 연결하지 않는다.

## 파일

- `migrations/20261127000100_initial_schema.sql`: 스키마·인덱스·RLS·GRANT
- `seed.sql`: 합성 해역 1개·어가 1개·센티넬 가두리 1개
- `tests/run-tests.mjs`: migration·seed 재실행·SQL 권한 테스트

`seed.sql`은 인증 수단 없는 로컬 전용 Auth 소유자 UUID를 만든다.
실제 사용자 로그인 계정은 생성하지 않는다. 시연 앱을 로그인으로 연결하려면
실제 Auth 사용자와 farm.owner_user_id를 관리자가 연결해야 한다.
# 추천 기한 (#44)

`20261127000800_recommendation_deadlines.sql`은 기존 recommendation.deadline과 게시 snapshot을 함께 검증·저장합니다.
기존 회차는 수정하지 않으며 정책·표시 계약은 [추천 기한 문서](../docs/recommendation-deadlines.md)를 따릅니다.
