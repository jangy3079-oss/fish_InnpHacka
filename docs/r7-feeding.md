# R7 섭식 저하 — #152

기존 M5의 `evidence.feeding_change`를 같은 낮/밤 기준선 대비 급이 반응 변화율로 읽는다.
원시 MetricRecord·M5 산식·알림 생성·지자체 API·R4/R6는 변경하지 않는다.

## 판정과 가정

모든 조건을 충족해야 한다:

- 기준선 ready이고 현재 지수가 있으며, `cause_min_stress_exceptions`에 R7이 있으면40 미만도 허용.
- 표층 >= R7.env.temp_surface_min_c(현재23℃).
- 표층 < 공통 normal_env.temp_surface_max_c(25℃), DO >= normal_env.do_min_mg_l(4.0).
  환경 누락/저산소/행동 교란 온도에서는 평시 급이 관리로 판단하지 않는 보수적 초안이다. 이 수치는 현장 권고를 뜻하지 않는다.
- |activity_change| <0.2, lower_layers_ratio_x <1.5. 경계값과 누락은 불충족.
- R1/R2/R3/R5/R8의 조건이 충족되지 않음. R7은 그 원인과 동시 후보가 되지 않는다.
- feeding_change <= -0.15이며 현재 영상 품질이 camera_only_min(0.6) 이상. 값·품질 누락으로 첫 판정을 만들지 않는다.

숫자 기준은 YAML에 가정 주석을 붙였다. R7.phase=mvp는 구현 범위 표시이며 현장 검증 완료가 아니다.
원인 확률1.0은 이진 규칙의 상대값이며 확정 진단이 아니다. R7은 `immediate_danger`를 만들지 않고 기존 지수/경보 단계 산식을 바꾸지 않는다.

## 야간·낮은 품질

유효한 R7 관측 뒤 급이/영상 품질이 누락되면 직전 판정을 유지한다. 현재 값이 생긴 것처럼 채우지 않는다.
계속 확인되는 소나 행동·환경·현재 지수 조건은 유지 중에도 검사한다.

- 원인 근거에는 feeding_change(현재 null 가능), feeding_current_observed,
  feeding_held_due_to_missing, feeding_last_observed_at, feeding_change_last_observed를 기록한다.
- 상태 API/DB 분석 snapshot/시나리오 재생에는 r7_held_due_to_missing과 feeding_last_observed_at을 전달한다.
- 마지막 유효 급이 관측부터 최대24시간 유지(max_hold_hours, 가정). 정확히24시간은 허용, 초과하면 해제.
- 관측된 정상 급이·행동/환경 이상·다른 원인·점수 없음·warming_up·주기 공백·설정 revision 변경은 유지 상태를 해제한다.
- 새 엔진/프로세스에는 과거 유지 상태가 없다. CLI·분석 작업자는 입력 구간을 순차 재생해 이력을 만든다.

## 추천과 API

`recommendation_for`는 R7에서 keep/reduce_feed만 손익 비교 가능하게 한다.
정상 단계의 손익 최솟값이 keep여도 **R7 관리 권고는 reduce_feed**다.
`recommended_option=reduce_feed`, `details.lowest_loss_option=keep`,
`details.recommendation_basis=R7_feeding_management`로 기준을 구분한다. 감량률/알림 문장을 새로 만들지 않는다.
재고가 없으면 기존 계약대로 추천 없음(stock_required)이며 원인 상태 조회는 가능하다.

`POST /demo/scenario/feeding_drop?t=48`도 합성 재생 시나리오로 허용한다.
5분 완료 주기·시간 단위·비저장·합성 추천ID0 계약은 #30과 같다. 기존 프론트 파일 재생은 변경하지 않는다.
실제 DB 경로 `python -m pipeline.analyze_db`는 같은 엔진/추천 함수를 사용한다. 새 migration은 필요 없다.

## 검증과 공동 후속

기본 합성 시드에서 feeding_drop R7·정상/탁도 R7 없음·고수온 초기 R7→R1/R2 전환을 검사한다.
지수 게이트·조건 경계·관측 유지/해제/만료·가두리/설정 격리·잘못된 배치의 상태 불변·실제 MetricRecord 분석 snapshot·HTTP 재생도 검사한다.
합성 시드 검증이며 현장 또는 모든 시드에서 정확도를 입증한 결과가 아니다.

- 데이터/모델·문서: -15%·활동/쏠림 기준, 공통 정상 환경 범위·24시간 유지와 mvp 표시 확인.
- 프론트·백엔드: 밤에는 마지막 관측 유지 표시, 손익 최솟값/급이 관리 권고 구분, feeding_drop 시나리오 연동.
- 데이터/검증 담당: R7 병합 후 VC-03 feeding_drop 추가와 프론트 라벨 fallback 재측정은 이슈에 기록된 해당 담당의 후속 작업이다.
- #34/#35/#45 담당: 알림·집계·R4/R6 구현은 해당 팀원 소유. 새로운 원인/조치와의 호환은 병합 후 함께 확인한다.
