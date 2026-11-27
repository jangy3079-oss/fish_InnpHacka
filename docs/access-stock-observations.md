# 접근 범위·재고 조회·관측 근거 (#197 #200 #201)

| 경로 | 응답/권한 |
| --- | --- |
| GET /me | 최신 Auth 역할, 허용 area_ids, 접근 가능한 cages의 id/name/farm_id/sea_area_id/can_write_stock/can_read_financial. 금융 원문 없음 |
| GET /cages/{id}/stock | 그룹별 count/avg_weight_kg/insured/updated_at, 공통 insured와 insured_mixed. 소유자 또는 별도 금융 열람 권한 필요 |
| GET /areas/shapes | 허용된 실제 검증 경계만 WGS84 FeatureCollection로 반환. properties.area_id/name/source/station_id, 미확인 해역은 unverified_area_ids |
| GET /areas/cause-distribution | 공무원 허용 해역의 가두리별 최신·유효·ready 경보 회차에서 표시되는 첫 원인만 집계. 개별 어가 원문 제외 |

## 권한과 ID

API가 `/auth/v1/user`의 최신 보호 app_metadata를 확인하고 DB ownership/JWT 허용 범위와 교차한다.
user_metadata로 권한을 얻지 못한다. 공무원의 금융 읽기는 소유자 재고 쓰기와 별개다.
stock GET은 최신 Auth에서 회수된 금융 권한도 거부한다. 사용자 Bearer를 RPC에 전달하고 service_role로 우회하지 않는다.
JSON 정수 ID/재고 수량은 JS 안전 정수 범위까지만 반환한다. 생산자 문자열 ID는 기존 명시적 매핑을 유지하며 숫자를 추출해 추측하지 않는다.

## 재고와 지도

빈 재고는 insured=null/mixed=false, 혼합은 insured=null/mixed=true다. 0마리 그룹도 현재 재고로 보존한다.
폼은 GET 성공 후 전체 그룹을 채우고 편집한다. 조회 실패·가두리 변경·권한 부족에는 이전 값을 지우고 저장을 막는다.
PUT 전체 교체/마지막 쓰기 우선은 유지하며 생략 그룹 삭제와 공통 보험 적용을 저장 전에 알린다.
가입 신고는 보상 지급 확인이 아니다. 동시 편집 CAS를 지원한다고 표시하지 않는다.

sea_area.geometry_verified 기본값은 false다. 실제 경계 출처가 확인된 자료만 geometry_source와 함께 등록해 true로 바꾼다.
기존 합성 seed 경계는 실제 API에서 반환하지 않는다. PostGIS나 임의 실제 경계·station 매핑을 추가하지 않는다.
원인 분포는 현재 config_revision/valid_until/ready/주의 이상/표시 원인이 있는 회차만 센다. 최신 회차가 무효면 오래된 회차로 대체하지 않는다.
기존 #35 stress-map/release-demand 구현은 변경하지 않는다.

## 실제 인증·이미지·그래프

실제 로그인은 Supabase Auth REST의 password/refresh_token 흐름을 사용한다.
[공식 Auth API](https://supabase.github.io/auth/) 및 [세션 설명](https://supabase.com/docs/guides/auth/sessions)을 따른다.
VITE_SUPABASE_URL/VITE_SUPABASE_PUBLISHABLE_KEY는 API와 같은 프로젝트의 공개 설정만 넣는다. 비밀/service 키를 넣지 않는다.
세션은 브라우저 탭 sessionStorage/메모리에 보관하며 로그아웃 시 제거한다. 실제 로그인 후에는 /me의 가두리·역할을 사용한다.
실제 토큰을 모든 보호 요청에 전달하고401은 갱신 후 동일 요청을 한 번만 재시도한다. 동시 갱신은 공유한다.
403은 재시도하지 않고 보호 화면을 비워 권한 재확인을 요구한다. 계정 변경 중 늦은 응답은 버린다.
시연 계정/파일 전환은 실제 로그인과 구분하고 파일 시연에서 실제 재고 쓰기를 하지 않는다.

에코그램은 Bearer fetch→blob/object URL로 표시하고 변경/종료 시 URL을 해제한다. 자산 없음/구간 없음은 구분한다.
기존 NPZ 명시 매핑·양끝 포함 mask는 유지하며 PNG900×300의 글자 가독성·KST 축을 개선한다.
실제 시계열은 최근72h/limit1000과 지표별 source(sonar/camera/env)를 명시한다.
source/confidence/has_more를 보존하고 원시 점/막대만 표시해 누락을 보간하거나 센서를 평균하지 않는다.

## 관측 메타데이터

이미 구현된 CauseEngine.metadata()의 cause_source/cause_pending/progress와 R7 유지·해제 근거를 분석/재생 API에 연결한다.
cause[]에는 display/cause_source가 추가되며 false 후보는 숨긴다. 점수·순위·R8 16h·R7 유지 규칙은 바꾸지 않는다.
정상/예열/표시 원인이 있는 회차에는 보류 안내를 비운다. 실제 관측 시간만 progress로 사용하고 단계 유지로 원인을 만들어 채우지 않는다.
DB 게시 trigger가 producer evidence의 display를 보존하고 ts 이전 실제 metric의 source별 마지막 ts를 last_seen에 기록한다.
해당 시각 이상인 다음 주기·미래 관측은 제외하며, 현재 입력으로 과거 snapshot을 GET에서 덮어쓰지 않는다.
기존 회차에 없는 metadata는 unknown/빈 값으로 남긴다. 실제 API의 source는 cause_engine 또는 미기록 null이며 scenario_label을 받지 않는다.

## 검증·배포

migration20261128000300과 API/worker/frontend를 함께 배포한다. 기존 공개 테이블10개·원시 MetricRecord는 유지한다.
작업 중 main을 반영하고 구현 완료 후 관련 API·관측 metadata·SQL/RLS·클라이언트/빌드를 한 번 모아서 검증한다.
전체 모델 학습/다중 시드 평가는 이 계약 연결 변경에서 반복하지 않는다.
원격 Auth 계정/키·실제 경계 자료·hosted PostgREST/브라우저 E2E 결과를 로컬 MockTransport 검증으로 대체해 표시하지 않는다.
새 이슈를 만들지 않으며 #34·#35·#45 구현은 범위에서 제외한다.
