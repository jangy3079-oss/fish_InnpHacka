# 추천 실행 기한 — #44

FR-DEC-04·SDD7의 추천 시점/마감을 상태와 같은 분석 회차에 연결한다.
`GET /cages/{id}/recommendation`과 합성 재생 응답의 recommendation에 아래 필드를 추가한다.
손익 카드와 공통 조치 카드 모두 같은 표시 컴포넌트를 사용한다.

## 필드

| 필드 | 의미 |
| --- | --- |
| ts | 분석/추천 기준 시각. 기존 계약 유지 |
| deadline | 적용 가능한 항목 중 가장 이른 시각. 다음 판단 시점도 포함하므로 법정 신청 마감으로 해석하지 않는다 |
| deadline_expired | GET 시각에 위 시각이 지났는지. 재생은 합성 기준 시각으로 판단 |
| deadlines | 종류별 시각/출처/적용 여부 목록 |

각 항목은 kind(next_assessment/release_application/harvest), label, action_id(null/release/harvest),
applicable, at(시간대 포함 ISO 또는null), source_status(pending/assumed/confirmed),
basis(unconfirmed/relative/absolute), source, expired를 포함한다.

- 다음 판단 시점은 모든 실제 추천에 포함한다. 추천 자체가 없는 경우 새 추천/기한을 만들지 않는다.
- 방류·출하 항목은 해당 옵션이 존재할 때 포함한다. 불가 옵션의 at는 표시할 수 있지만 대표 deadline 선정에서 제외한다.
- pending의 at=null은 **확인 필요**다. 기한 없음·무기한 또는 승인 완료가 아니다.
- assumed는 화면에서 **가정 기준**을 표시한다. confirmed에는 운영자가 확인한 출처를 설정해야 한다.
- 이미 지난 절대 시각을 숨기거나 미래로 돌리지 않는다. 화면에는 지났으니 다시 확인하라고 표시한다.
- 기존 회차에 필드가 없으면 deadline=null/deadlines=[]이며 화면은 기한 정보 없음으로 표시한다. 현재 설정으로 과거 마감을 소급 생성하지 않는다.
- R5/R8은 다음 판단만 포함하고 방류/출하 마감이나 금전 추천 승자를 만들지 않는다.

## YAML 설정

기본 release_rules.application_deadline과 costs.harvest_deadline은 모두 pending/null이다.
현행 지역별 방류 신청 마감·거래처 출하 기한을 확인하지 않았으므로 시각을 임의로 만들지 않는다.
다음 판단 간격만 costs.reassessment_minutes_by_level에서 가정값으로 설정한다:
normal60분, caution30분, warning15분, danger5분. 실제 유효기간(valid_until)과는 별개다.

운영에서 사용할 수 있는 설정 예(확인된 실정책을 뜻하지 않음):

```yaml
# release_rules.yaml. 상대 시각 가정 예시
application_deadline:
  source_status: assumed
  hours_from_assessment: 2
  at: null
  source: "시연용 운영 가정"

# costs.yaml. 확인 후 입력할 절대 시각의 형식 예시
harvest_deadline:
  source_status: confirmed
  hours_from_assessment: null
  at: "2026-11-28T15:00:00+09:00"
  source: "운영자가 확인한 거래처 일정의 식별 정보"
```

pending은 두 시각 기준 모두null, assumed/confirmed는 상대 시간 또는 절대 시각 중 정확히 하나가 필요하다.
상대 시간은 양수이며 최대1년, 절대 시각은 시간대 포함 문자열이어야 한다. confirmed는 비어 있지 않은 source가 필요하다.
단계별 다음 판단 간격은 네 단계 모두 유한한 양수여야 한다. 잘못된 설정은 ConfigLoader에서 거부한다.
현재 정책은 배포 전체에 적용되는 설정이며 해역/거래처별 개별 정책 저장소는 아니다. 실제 운영 전 적용 범위를 합의해야 한다.

## 계산·저장·조회

1. build_bundle/ReplayEngine에서 기존 판정/추천을 만든 뒤 같은 config snapshot과 회차 종료 시각을 with_deadlines에 전달한다.
2. 상대 기한은 해당 시각에서 계산한다. 호출할 때마다 현재 시각을 더하지 않으므로 재전송·조회로 연장되지 않는다.
3. migration **20261127000800_recommendation_deadlines.sql**의 제한된 trigger가 snapshot 구조·출처 상태·옵션 적용 범위·대표 최솟값을 검사한다.
4. 기존 public.recommendation.deadline(timestamptz)과 snapshot을 분석 게시 트랜잭션에서 함께 저장한다. 실패는 전체 rollback이다.
   공개 테이블 수·RLS·RPC identity·계정 권한은 변경하지 않는다. 기존 행은 소급 수정하지 않는다.
5. GET은 게시 당시 시각/정책을 유지하고 현재 expired 표시만 계산한다. 설정이 달라지면 기존 #104의 config_changed/selectable 정책을 따른다.

이는 **기한 표시** 기능이다. 기한을 이유로 승인·집행·방류 권한을 부여하거나 실제 신청·출하를 수행하지 않는다.
다음 판단 권고가 지났다고 선택 의사 기록을 자동 거부하지 않는다. 선택 저장은 기존 최신 회차·설정·재고·유효기간·소유자 검사를 그대로 따른다.
기한별 서버 실행 차단/예약/승인 연계는 별도 합의·후속 범위다.

## 화면과 검증

- 한국 시각으로 월/일·시/분을 표시하고 원본 ISO는 time.dateTime에 남긴다.
- 가정/미확인/지남/선택 불가/근거를 분리해 표시한다. 기존 mock이나 과거 snapshot도 오류 없이 표시한다.
- 공통 조치 응답의 first_aid.actions 객체와 기존 목업 배열을 모두 해석하도록 API client를 보완했다. 기한 필드는 그대로 유지한다.
- Python: 정책 경계·시간대·과거 시각·불가 옵션·오류 설정·API 검증·게시 후 설정 변경/비연장 검증.
- PostgreSQL(PGlite): 원자적 column/snapshot 저장·재전송·잘못된 마감 rollback·기존 RLS와 권한 검증.
- 프론트: 실제 카드 SSR 렌더링·한국 시각·상태 문구·HTML 이스케이프·실제 API adapter와 production build 검증.
- 원격 Supabase migration 적용·실계정·현장 정책 검증은 수행하지 않는다.

## 공동 합의

프론트/백엔드/데이터·모델/문서/운영은 다음 판단 간격, 해역별 신청/출하 정책 출처·입력 책임,
지남 표시와 선택 기록/실제 실행의 구분, 정책 배포 범위를 확인한다.
#34·#35·#45는 다른 팀원 담당이며 알림·집계·적조/작업 입력은 이번 기능에서 구현하지 않는다.
