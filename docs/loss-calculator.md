# 손익 계산기와 긴급방류 요건 — #27

## 실행과 범위

```sh
python -m pip install -r requirements.txt
python -m decision --input scenarios/loss_request.example.json
python -m decision --input scenarios/loss_request.example.json --output loss-result.json
python -m unittest tests.test_decision -v
```

예시는 합성 재고·임시 시세·가정 비용이다. 실제 가격 조회, 방류 승인, 예산 예약, 보상 지급을 수행하지 않는다.
`LossCalculator.compare(ComparisonRequest 또는 dict)`는 부작용 없는 계산 모듈이며 JSON 응답을 CLI로도 제공한다.
현재 HTTP `/recommendation`은 기존 목업이다. DB 재고 조회·원인/경보 연결·추천 저장/조회는 #104,
질병 계열은 #28의 검토된 공통 조치를 반환하며, [진단 접수](diagnosis-api.md)는 별도 인증 POST다. 실행 마감은 #44 후속이다.

## 입력

- cage_id/farm_id/sea_area_id: 호출 범위를 나타내는 문자열 ID. 실제 객체 소유권은 이 모듈이 조회하지 않는다.
- species: 명시적 품종. 기본값으로 조피볼락을 추정하지 않는다.
- level: normal/caution/warning/danger. API의 한국어 경보 단계는 호출 어댑터가 변환한다.
- stock: fry/mid/market 중 중복 없는 1~3그룹, count는 0 이상 bigint 범위 정수. 합계가 0이면 계산을 거부한다.
- avg_weight_kg, price_per_kg: 선택적 양수 무게/0 이상 시세. 생략하면 costs.yaml 가정값을 사용하고 출처를 표시한다.
- insured: 그룹별 가입 여부. boolean이며 생략 시 false다.
- other_area_release_count: **요청 재고를 제외한 같은 해역의 다른 동시 방류 수량**. 0도 명시적 확인값이다.
  미입력/null은 미확인이며 방류 불가로 표시한다. 전체 수량을 여기에 넣으면 요청 수량을 두 번 더하므로 안 된다.
- insurance_confirmed: 실제 보상 적용 여부를 확인한 입력. 기본 false.
- release_support_confirmed, farm_subsidy_remaining_krw: 실제 지원 적용 확인 및 **어가 전체 잔여 상한**.
  확인=true이면 잔여 상한이 필수다. 가입/지원 여부만으로 금액을 자동 차감하지 않는다.
- unavailable_actions: 운영상 불가한 선택지와 이유(장비/출하/이동 등). 지원하는 alias는 canonical ID로 합친다.
- primary_rule_id: R5/R8이면 경제 비교 대신 first_aid_required와 빈 options를 반환한다. 원인 판정은 외부 cause 엔진의 책임이다.

count·무게·금액의 boolean·문자열 수량·음수·비유한 수치·그룹 중복·미지원 필드/선택지는 거부한다.
무게·가격·잔여 금액의 입력은 정수부 최대12자리·소수부 최대6자리이며 정확한 전송에는 십진 문자열을 권장한다.
주요 권한/지원/해역 집계 입력은 후속 서버 어댑터가 신뢰하는 저장소/담당자 확인에서 가져와야 한다.
브라우저가 보내는 확인 플래그만으로 지원금·보험금·해역 수량을 확정하면 안 된다.

## 산식과 9개 선택지

`L(a) = sum_g(N_g * P_g * W_g * m_g(a)) + C(a) - S(a)`

| 사용자/API ID | config ID | 손실률 |
| --- | --- | --- |
| keep | keep | 경보 단계의 예상 폐사율 |
| reduce_feed | feed_reduction | 단계 폐사율 × 감량 계수 |
| stop_feed | feed_stop | 단계 폐사율 × 중단 계수 |
| oxygen | oxygen_boost | 단계 폐사율 × 산소 계수 |
| density_control | density_control | 단계 폐사율 × 밀도 계수 |
| net_depth | net_depth_adjust | 단계 폐사율 × 그물 수심 계수 |
| wintering | overwinter_move | 단계 폐사율 × 이동 계수 |
| harvest | early_harvest | 출하 할인율 |
| release | release | 전량 포기 손실률 |

그룹별 N/P/W/손실률로 손실을 계산해 더하고, C(a)는 그룹 수와 무관하게 선택지당 한 번 더한다.
감량·밀도 계수/비용은 #27의 명시적 가정값으로 YAML에 추가했다. 기존 시세·비용·폐사율도 검증 전 가정값이다.
이 모듈은 실제 실행의 인과적 효과나 최선 조치를 증명하지 않는다.

Decimal로 계산하고 **최종 선택지 합계만 정수 원으로 ROUND_HALF_UP**한다. 중간 근거 금액은 정확한 십진 문자열이다.
estimated_loss_krw는 정수/null이며 exact_loss_krw는 반올림 전 합계다. 지원금이 손실보다 크면 산식 그대로 음수를 보존한다.
lowest_loss_option은 불가 선택지를 제외한 반올림 전 값의 최솟값이며, 동률이면 위 표 순서다.
이는 비용 가정에 따른 비교 결과이고 추천 저장/어민 선택/실행을 의미하지 않는다.

예시의 중간어1000마리·0.3kg·10000원/kg·위험 폐사율0.4에서:
유지120만원, 산소99만원, 그물 수심102만원, 출하100만원, 방류320만원이다(지원·보험0).
산소는 `1000 * 10000 * 0.3 * (0.4 * 0.7) + 150000 = 990000`원이다.

## 방류 확인

제공된 release_rules.yaml의 품종·크기·동시 수량 요건만 평가한다.
양수 재고 중 하나라도 부적합 크기면 **요청 전체 방류**를 불가로 처리한다. 출하 크기0마리 행은 막지 않는다.
부분 그룹만 방류하거나 나눠서 자동 승인하지 않는다. 요청 수량+다른 동시 수량이 정확히10만이면 통과,
초과하면 불가이며 YAML의 7일 순차/1km 분산 대안을 반환한다. 대안 제시만으로 available=true로 바꾸지 않는다.

다른 해역/가두리 계획을 직접 조회하거나 예약하지 않으므로 동시에 두 계산이 같은 한도를 이용할 수 있다.
HTTP/DB 연결 시 서버 측 해역 집계·동시성·버전·유효시간을 구현해야 한다.
available의 범위는 출력의 scope에 명시하며, 현재 고시/시기/사유/신청·승인 절차 전체에 대한 법적 판정이 아니다.
금지 이유를 모두 반환하고 불가 선택지의 estimated_loss_krw는 null로 둔다. calculation은 hypothetical=true인 가정 계산이며 최솟값 비교에서 제외한다.

## 지원금과 보험금 초안

- 방류 지원금: 방류가 가능하고 적용 확인=true일 때만
  `min(마리당 지원액 * 요청 수량, YAML 어가 상한, 입력된 어가 전체 잔여 상한)`.
- 보험금: 적용 확인=true이며 YAML insurance.eligible_options에 속할 때만,
  **가입된 그룹의 예상 손실 합계 × 지급률**. 기본 정책은 폐사 손실 조치만 포함하고 방류/출하는 제외한다.
- S(a)는 위 두 금액의 합이다. 현재 기본 YAML 지원액/보험률은0이므로 반영 금액도0이다.
- 잔여 예산 차감·예약, 실제 보장 범위/심사, 보조금 중복/보험금 조정은 수행하지 않는다.
  정식 정책·상한/기지급 합산·중복 제한은 담당자 합의 대상이다.

## 응답과 설정

응답은 요청 원본, 적용한 그룹별 P/W와 출처, 손실률/입력식/항목 합계, 비용, 보험금/지원금, 불가 사유,
방류 판단 수량/상한/대안, config_revision/loaded_at을 포함한다. 기존 API option ID를 유지하며
원인 규칙의 stop_feeding/oxygen_supply/emergency_release 등은 decision.actions 한 곳에서 변환한다.
unsupported config action은 조용히 빠뜨리지 않고 ConfigError를 발생시킨다.

한 compare 호출에 검증된 5개 설정 snapshot 하나만 사용한다. 다음 호출은 수정된 YAML을 검증해 다시 계산하고,
실패 시 ConfigError를 전파한다. 이전 응답을 자동으로 고치거나 이전 설정으로 몰래 대체하지 않는다.
커스텀 costs.yaml은 feed_reduction/density_control 손실·비용과 insurance.eligible_options를 함께 추가해야 한다.
CLI 실패 종료코드는2이며 기존 출력 파일을 삭제하지 않는다. 성공 종료코드0의 새 결과인지 확인해야 한다.

검증은 손 계산·그룹별 합산·소수/반올림·방류 10만 경계·다른 가두리 수량·혼합 크기·지원 상한·보험 대상·
불가 제외·질병 분기·입력/설정 오류·재로드·CLI를 다룬다. 원격 DB/HTTP 통합·실제 시세/지원제도·현장 경제 효과는 검증하지 않았다.
