# DB 계약 및 권한 초안 — #3

근거: SDD 7장, SRS NFR-SEC-01·03, FR-ADM-01. 구현 시점의 최신 main은
`9399f07`이며 다른 미병합 브랜치를 포함하지 않는다.

## 테이블과 데이터 규칙

| 테이블 | 주요 관계·규칙 |
| --- | --- |
| sea_area | bigint ID, name, geom(GeoJSON Polygon), nifs_station_id |
| farm | sea_area_id FK, owner_user_id → auth.users UUID, name, location JSON |
| cage | farm_id FK, 센티넬 여부, 양수 수심, 그물 수심 ≤ 가두리 수심 |
| stock | cage_id FK, fry/mid/market별 한 행, 마릿수 ≥ 0, 무게 > 0, insured, 서버 updated_at |
| sensor | cage_id FK, sonar/camera/env, online/offline/unknown, last_seen |
| metric | (cage_id, ts, source, metric) PK, value JSONB, confidence 0~1, (cage_id, ts) 인덱스 |
| stress_score | (cage_id, ts) PK, score 0~100, 단계·기준선·model_version, evidence·누락 센서·카메라 비중 |
| cause_estimate | cage_id FK, ts, 원인, probability 0~1, R1~R8, evidence JSON |
| recommendation | cause_estimate_id FK, options JSON 배열, deadline, chosen_option, outcome JSON |
| alert | recommendation_id FK, user_id → auth.users, 채널·본문·발송/읽음 시각 |

ID는 SDD API 예시와 맞춰 **bigint identity**를 제안한다. Auth 사용자 ID만 UUID다.
JS에서 안전한 정수 범위를 넘는 bigint의 직렬화는 후속 API 계약에서 정해야 한다.
외래키 삭제는 기본 RESTRICT/NO ACTION으로 이력의 연쇄 삭제를 방지한다.
metric의 ts는 측정 시각 timestamptz이며 UTC와 동일 순간의 오프셋 시각을 같은 키로 취급한다.
정상 관측 0과 결측을 구분한다. NULL·문자열·불리언 value를 거부하며 vertical_dist만 비율 배열을 허용한다.
비율 합은 1±1e-6. quality/still_ratio는 0~1, 나머지는 JSON 숫자다.
JSONB는 비유한 숫자를 표현할 수 없으며 confidence·score 등의 numeric 범위를 검사한다.
동일 PK 지표의 중복 insert는 거부한다. 재전송 시 upsert/무시/정정 정책은 #10에서 합의한다.

warming_up 상태는 score/level의 null을 허용한다. ready는 두 값을 요구한다.
#59~61의 센서 묶음 점수·마지막 관측 등은 evidence JSON에 저장할 수 있으나 키는 데이터 담당과 합의한다.
원인별 probability 합과 추천 선택지의 내부 구조는 #25·#27에서 검증한다.
geom은 WGS84 GeoJSON Polygon이며 기본 구조만 검증한다. PostGIS·공간 연산·완전한 폴리곤 검증은 포함하지 않는다.

## 권한 행렬

| 주체 | 기본·센서·진단 데이터 조회 | stock·recommendation 조회 | stock 입력·수정 | 기타 쓰기 |
| --- | --- | --- | --- | --- |
| 익명 | 불가 | 불가 | 불가 | 불가 |
| 어가 소유자 | 자기 어가만 | 자기 어가만 | 자기 어가만 | 불가 |
| 공무원(해역 허용) | 지정 해역만 | 불가 | 불가 | 불가 |
| 공무원(해역+개별 경영정보 허용) | 지정 해역만 | 명시된 어가만 | 불가 | 불가 |
| trusted backend service_role | 전체 | 전체 | 전체 | 전체 |

alert는 **수신자 본인이고 해당 recommendation 조회 권한도 있을 때만** 읽는다.
재고 외 사용자 직접 쓰기는 허용하지 않는다. 추천 선택·발송·관리 등은 후속 API에서
요청자 권한을 확인한 뒤 처리해야 한다. service_role은 RLS를 우회하므로 서버 전용이다.
지자체 대시보드 #35는 개별 손익 없이 집계만 반환해야 하며, stock/recommendation 원문을
프론트로 전달하면 안 된다. 이 PR은 대시보드 집계 API를 구현하지 않는다.

## 공무원 권한 부여 제안

관리자가 Supabase Auth Admin을 통해 보호된 app_metadata에 다음처럼 설정하는 안이다.
실제 계정에 권한을 부여하는 코드는 이 PR에서 실행하지 않는다.

```json
{
  "role": "official",
  "area_ids": [1],
  "financial_farm_ids": [1]
}
```

JWT의 `app_metadata`를 확인한다. 사용자 수정 가능한 `user_metadata`는 무시한다.
financial_farm_ids가 있어도 해당 어가의 area_ids가 없으면 조회할 수 없다.
숫자 ID 배열을 사용하며 문자열 ID 배열은 허용으로 해석하지 않는다.
JWT 권한 변경은 토큰 갱신 전까지 반영되지 않을 수 있으므로, 즉시 회수가 필요하면
별도 권한 테이블 또는 세션 회수/토큰 갱신 정책으로 변경해야 한다.
private 스키마의 제한된 security definer 함수는 search_path를 고정하고 auth.uid()를 확인한다.
private 스키마를 Data API의 Exposed schemas에 추가하지 않는다.

## 팀 합의가 필요한 항목

1. **백엔드·DB + 데이터 + 프론트:** bigint ID 채택 여부. #2 문자열 cage_id와 #4 정수 API의 통일.
2. **백엔드·DB + 기획/권한 운영:** 공무원 권한을 누가 승인·회수할지, 해역과 개별 어가 경영정보 범위,
   app_metadata 방식의 MVP 사용 여부와 토큰 갱신/즉시 회수 요구.
3. **데이터·모델 + 백엔드:** metric 중복·정정, 센서 source별 지표 허용 조합,
   stress_score evidence 키(#59~61), ts 기준과 model_version 보존 방식.
4. **프론트 + 백엔드:** 재고 insured를 크기 그룹별로 저장할지 어가 공통 보험으로 분리할지,
   warming_up/센서 누락 표현, bigint 직렬화.
5. **프론트·지도 + 데이터 + 백엔드:** GeoJSON 그대로 쓸지 PostGIS로 변경할지,
   실제 해역 경계·수과원 관측소 매핑과 시드 ID(cage=3) 확정.
6. **백엔드 + 문서/기획:** 추천 options/outcome 구조·실행 마감의 의미(#44),
   Auth 사용자 삭제·어가 이전·데이터 보존 기간, 이력 수정/삭제 권한.

합의 전 #3를 자동 종료하지 않는다. 테스트·실행 방법은 [Supabase README](../supabase/README.md)에 있다.

## 참고

- [Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Supabase seed](https://supabase.com/docs/guides/local-development/seeding-your-database)
