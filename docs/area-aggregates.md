# 해역 집계 API — #35

`GET /areas/stress-map`, `GET /areas/{id}/release-demand`는 목업을 대체한 인증된 DB 조회다.
근거는 FR-DEL-04·05, NFR-SEC-03이다. #104의 게시 회차를 읽으며 분석·추천·선택을 변경하지 않는다.

## 인증과 데이터 경계

- `Authorization: Bearer <Supabase user access token>`과 서버의 `SUPABASE_URL`,
  `SUPABASE_PUBLISHABLE_KEY`가 필요하다. service_role을 사용하지 않는다.
- 매 요청 Auth `/user`의 최신 `app_metadata.role=official`, 양의 정수 배열 `area_ids`를 확인한다.
  같은 Bearer를 RPC에 전달해 DB JWT의 역할·해역 권한도 확인한다. 두 권한이 모두 허용해야 한다.
  `user_metadata`, 어민 소유권, 분석 worker 역할은 공무원 권한을 대신하지 않는다.
- map은 허용된 해역만 ID순으로 반환한다. 빈 scope는 `areas=[]`다. 중복 ID는 제거한다.
  demand의 미허용 ID는 존재 여부와 무관하게 403, 허용되었으나 없는 ID는 404다.
- `private` 내부 집계만 security definer이며 빈 search_path를 고정한다. 노출된 public RPC는
  invoker wrapper다. 내부 가두리별 facts 함수·분석 원문은 authenticated/anon 실행·조회 불가다.
  stock·recommendation의 기존 금융 RLS와 `financial_farm_ids`는 변경하지 않는다.
- 응답은 해역 집계 필드만 허용한다. 개별 가두리·어가 ID, 재고 행, 손익, 추천 원문·선택 기록은 없다.
  서버와 SQL 모두 권한을 확인하고 Pydantic 응답 allowlist도 적용한다.
- `X-Data-Mode: database`, `Cache-Control: private, no-store`, `Vary: Authorization`을 보낸다.
  401 인증 실패, 403 권한 없음, 422 잘못된 ID/범위, 503 설정·저장소·응답 오류다. 목업으로 폴백하지 않는다.

API를 우회한 직접 RPC는 JWT 권한만 확인한다. #68의 JWT 갱신 전 권한 회수 지연은 기존과 같으며,
이 PR이 실시간 JWT 회수를 구현하는 것은 아니다. RPC의 revision은 API가 현재 ConfigLoader snapshot에서 보낸다.

## 공통 집계 시점

해역의 모든 가두리를 모수로 삼고, **가두리별 최신 게시 회차 하나를 먼저 선택**한다.
최신 회차가 없거나 미래 시점이거나 `valid_until`이 지났거나 현재 설정 revision과 다르면 미집계다.
이때 과거의 정상/방류 추천을 되살리지 않는다. 여러 회차·크기 그룹이 있어도 가두리는 한 번만 센다.
각 RPC의 조회는 같은 PostgreSQL statement snapshot을 사용한다. map과 demand의 별도 HTTP 요청 사이에
새 분석·재고가 게시될 수 있다. `ts`는 조회 기준시각이며 센서 관측시각을 뜻하지 않는다.

## GET /areas/stress-map

```json
{
  "ts": "2026-11-27T05:00:00Z",
  "config_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "aggregation_method": "max_current_level_and_score",
  "areas": [{
    "area_id": 35, "name": "시연 해역", "level": "위험", "stress_score": 80,
    "cage_count": 3, "assessed_cage_count": 1, "held_cage_count": 1,
    "unavailable_cage_count": 1, "data_status": "partial",
    "latest_analysis_at": "2026-11-27T04:55:00Z"
  }]
}
```

- `ready`/`baseline_status=ready`의 유효한 점수·단계가 있으면 assessed다.
- `insufficient_data`에서 `level_held_due_to_missing=true`인 유효한 단계는 held다.
  자료 부족으로 유지된 위험 경보를 지우지 않으며, 점수는 새로 만들어 넣지 않는다.
- 단계는 assessed+held 중 최대(`정상 < 주의 < 경고 < 위험`), 점수는 assessed 중 최댓값이다.
  두 값이 같은 가두리에서 나온다는 의미는 아니다. 평균·경보 임계값을 새로 도입하지 않는다.
- `cage_count = assessed_cage_count + held_cage_count + unavailable_cage_count`.
  전체가 assessed면 complete, assessed/held가 일부라도 있으면 partial, 그 외 unavailable이다.
  빈 해역도 unavailable이며 `level=null`, `stress_score=null`이다.
- assessed가 없으면 score=null, assessed/held 모두 없으면 level=null이다.
  `latest_analysis_at`는 최신 회차 시각의 최대이며 오래된/미래 회차도 포함할 수 있다.
  신선도·모수 판단에는 data_status와 각 count를 사용한다.

## GET /areas/{id}/release-demand

```json
{
  "area_id": 35, "ts": "2026-11-27T05:00:00Z",
  "config_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "basis": "recommended_not_chosen", "estimated_fish_count": 700,
  "cage_count": 3, "recommended_cage_count": 1, "assessed_cage_count": 1,
  "unavailable_cage_count": 2, "data_status": "partial"
}
```

현재 ready 회차의 `loss_comparison` snapshot에서 `recommended_option=release`이고
해당 옵션이 `available=true`일 때, 게시 당시 stock_version과 현재 stock_version이 같으면
그 가두리의 현재 재고 전체 마릿수를 합산한다. 크기 그룹 합계이며 부분 방류량을 추정하지 않는다.
stock_version에는 재고뿐 아니라 어가 소유자·해역도 포함된다. 재고 변경 후에는 새 분석 게시가 필요하다.
`recommended_cage_count`는 이 합계에 실제 포함된 가두리 수다. 합계는 JSON 정수의 정확성을 위해
9,007,199,254,740,991 이하만 허용하며 초과 시 422로 실패한다.

유효한 경제 추천이 다른 available 옵션을 추천하면 알려진 0으로 집계한다.
유효한 first_aid_required 회차나 no_current_cause 회차도 방류 추천이 없으므로 알려진 0이다.
워밍업·자료 부족·오래된 회차·설정 변경·경제 추천의 재고 변경/미입력·불완전/불가 추천은 미집계다.

- 모든 가두리를 판단했으면 complete이며 알려진 합계를 반환한다. 빈 해역은 complete/0이다.
- 일부만 판단했으면 partial이며 **확인된 합계(하한)**를 반환한다. 0도 전체 수요 0을 뜻하지 않는다.
- 가두리는 있지만 하나도 판단하지 못했으면 unavailable/`estimated_fish_count=null`이다.
- `cage_count = assessed_cage_count + unavailable_cage_count`다.

실제 chosen_option·실행·승인 여부와 다른 **추천 기준 예상 수요**다. 선택 저장은 재고를 감소시키지 않는다.
현행 #104 builder는 other_area_release_count가 없으면 방류 옵션을 불가로 두므로 집계가 0인 경우가 정상이다.
이 API는 방류를 새로 승인하거나 기존 불가 옵션을 가능으로 바꾸지 않는다.

## 적용·검증

`supabase/migrations/20261127000600_area_aggregates.sql`까지 순서대로 적용한다.
운영 DB migration 실행·실제 계정 설정은 배포 환경에서 별도로 필요하다. 이 작업에서는 원격 DB를 변경하지 않았다.
예: 권한 있는 공무원 계정으로 토큰을 발급한 뒤 `curl -H "Authorization: Bearer $ACCESS_TOKEN" "$API_BASE/areas/stress-map"`.

프론트는 null을 ‘미집계’, partial을 일부 미집계로 표시하며 알려지지 않은 수량에 막대를 그리지 않는다.
기존 데모 목업은 유지한다. 프론트 로그인·Bearer 전달 및 실제 지도 경계 연결은 #162 등의 연계 범위다.

검증은 전체 Python unittest, PGlite의 실제 migration/RLS·집계 SQL, 프론트 production build로 수행한다.
SQL은 격리된 테스트 DB fixture를 사용하며 실제 Supabase/Auth/PostgREST 연결 결과를 의미하지 않는다.
