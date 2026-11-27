# 원인 추정 엔진 — #25 / #26 / #60

> #152에서 R7 섭식 저하와 지수 게이트 예외·야간 관측 유지·관리 추천을 추가했다.
> [R7 판정·근거·유지 정책·API 계약](r7-feeding.md)을 따른다. 아래 R1/R2/R3/R5/R8 설명은 해당 구현 범위의 기록이다.

## 입력과 실행

원시 센서는 기존 `MetricRecord` 계약을 유지한다. 새 엔진은 기존 파생 산출물인
`StressScore`와 `FusedFeatures`를 받는다. 가두리 문자열 ID와 시각으로 정확히 연결하며
`score.ts - thresholds.cycle_minutes == fused.ts`여야 한다. 현재 주기는 5분이다.
다른 가두리·중복·역순·서로 겹치는 주기·naive 시각·비유한 숫자·짝이 없는 입력은 오류다.
누락된 관측값은 조건을 충족시키지 않으며 0이나 최근 값으로 채우지 않는다.

```python
from cause import CauseEngine
from common.config_loader import ConfigLoader
from stress.levels import apply_levels

loader = ConfigLoader()
results = CauseEngine(loader).evaluate_series(scores, fused)
apply_levels(scores, [r.immediate_danger for r in results],
             cfg=loader.last_good.to_dict()["thresholds"])
```

`python -m pipeline`과 `python -m stress score ... --model ...`은 이 경로를 사용한다.
가두리별 시각 증가를 지키면 여러 가두리를 섞은 배치도 평가할 수 있다.
`apply_levels`는 기존 계약대로 한 가두리씩 호출한다. 엔진은 순차 스트림 전용이며
동시에 여러 스레드에서 공유하지 않는다. 과거부터 재생할 때는 새 엔진을 만든다.

## 조건과 확률 초안

모든 업무 임계값은 `cause_rules.yaml`과 `thresholds.yaml`에서 읽는다.
구현된 R1/R2/R3/R4/R5/R6/R8 조건 키·범위·R2 boolean·점수 정책은 공통 로더에서 검증하며,
잘못된 설정이나 미지원 조건을 조용히 무시하지 않는다.

| 규칙 | 행동 | 환경 |
| --- | --- | --- |
| R1 고수온(피신 중) | `lower_layers_ratio_x >= 1.5` AND `activity_change <= -0.20` | 표층 >=25℃ AND 표층−중층 >=3℃ |
| R2 피난처 소실 | `activity_change <= -0.40` OR 응집도 변화의 이전 최고값 대비 하락 >=0.30 | 표층 >=25℃ AND 표층−중층 <1℃ |

R1은 **중층+저층 합계 비율 / 같은 낮·밤 기준선 합계**를 쓴다.
`mid_layer_ratio_x`(중층만)는 대신 사용하지 않는다. 이 선택은 담당자 검토 대상이다.
R2는 #60과 현재 SDD/YAML의 OR 조건을 따른다. 응집도 이력은 `[현재−12시간, 현재)`이며,
최고값에는 현재·미래·다른 가두리 관측이 들어가지 않는다. 이력이 없으면 응집도 조건은 거짓이지만
활동량 조건으로 R2가 발동할 수 있다. 표층−중층 차는 기존 코드와 같은 부호 있는 차이이며 절댓값이 아니다.
설정 revision이 바뀌면 이력을 비운다. 프로세스 재시작 뒤 이력 복원은 아직 없다.

`probability.scoring: binary_all_required`는 **초안 가정**이다. 행동/환경 필수 조건이 모두 참이면
점수 1, 아니면 0이다. 양수 점수를 합으로 나눠 `probability`를 낸다.
R1/R2 수온차 영역은 겹치지 않지만 R3/R5/R8과는 중첩될 수 있다. 단일 후보는 1.0,
후보가 여러 개면 동일 점수이므로 균등 분배한다. 이는 **후보 간 상대값이며 통계적
진단 확률이나 원인 확정이 아니다**. 같은 점수의 순서는 YAML의 tie_break_order를 따른다.
점수 곡선·부분 조건 가중치·미구현 원인과의 정규화는 후속 합의 대상이다.
`min_display`는 표시 여부에만 쓰며 저장 후보를 제거하거나 확률을 다시 바꾸지 않는다.

## 지수 문턱과 R2 즉시 위험

원인 후보는 ready 상태의 유효 지수가 `cause_min_stress`(현재40) 이상일 때만 만든다.
예외 목록의 R7은 #25에서 구현하지 않는다. 지수 미존재·warming_up에는 후보가 없다.

**경보 신호와 원인 표시는 구분한다.** 기존 데이터 코드의 안전 동작을 유지하기 위해
ready 상태에서 관측된 R2 조건은 지수가 40 미만/미존재여도 `r2_triggered`에 기록한다.
R2 `immediate_danger: true` 및 `immediate_danger_rules`에 R2가 있으면 이를 `apply_levels`에 전달한다.
이때 원인 목록은 비어 있고 suppression은 low_stress/missing_score일 수 있지만 단계는 즉시 위험이다.
warming_up에는 R2 경보 신호도 만들지 않는다. 이 표시·게이트 정책은 팀 합의가 필요하다.

## 결과와 저장

- `CauseResult`: 가두리·UTC 시각·후보 목록·R2 신호·즉시 위험·억제 이유·설정 revision
- `CauseEstimate`: cause·rule_id·raw score·정규화 probability·display·evidence
- evidence: 행동/환경 원시 근거·층간 차·이전 응집도 최고값/하락량·조건별 boolean·적용 규칙/문턱·model_version·config_revision
- `cause_estimate.jsonl`: 후보별 설명/근거 행. 후보가 없으면 빈 파일
- `api/status_timeline.jsonl`: cause 목록·cause_config_revision·cause_suppression·r2_triggered
- `to_row(db_cage_id)`: 기존 DB의 cause_estimate 6개 입력 컬럼. 생산자 ID→DB bigint 매핑을 호출자가 명시

DB 행 변환까지 제공하며 원격 DB 쓰기·계정/RLS 변경은 하지 않는다. 재전송 시 중복을 피할 키와
저장 주체는 합의해야 한다. 현재 HTTP `/status`는 기존 목업이다. 실제 조회/DB 저장 연결은 #104 후속이며
파이프라인 JSONL을 API가 즉시 읽는다고 가정하면 안 된다. R4/R6은 아래 #174 절, R7·추천·알림은 후속 범위다.

## 설정 적용과 검증

한 배치에서 ConfigLoader snapshot 하나를 사용한다. 다음 호출은 변경을 검증해 적용하며 오류는
ConfigError로 전파한다. 암묵적 last_good 대체는 없다. 설정 변경 시 응집도 이력은 초기화되며
이를 보존할지/어떤 시점에 적용할지는 운영 합의 대상이다.

```sh
python -m pip install -r requirements.txt -r requirements-api-dev.txt
python -m unittest tests.test_cause tests.test_pipeline -v
python -m unittest discover -s tests -v
python -m pipeline --help
```

검증 범위는 R1/R2 경계·누락·숫자·시각·이력 격리/만료·설정 재로드/실패·원인 억제·즉시 위험·
JSONL 및 DB 행 형식·실제 simulator→fusion→M5 시나리오다. 원격 Supabase 통합이나 현장 진단 성능을 검증한 결과가 아니다.

## #26: R3·R5·R8 입력과 판정

```python
from cause import CauseContext, CauseEngine

# 실제 서비스에서는 호출자가 해당 가두리/해역·시점의 특보 상태를 확인해 제공한다.
context = CauseContext(advisory_active=False)
results = CauseEngine().evaluate_series(scores, fused, contexts=[context] * len(scores))
```

contexts는 점수와 동일한 순서/길이이며 각 시점에 유효한 외부 컨텍스트다. 특보 있음=True,
없음을 확인함=False, 조회 실패·미입력·정보 없음=None이다. **기본값 None을 특보 없음으로 해석하지 않는다.**
원인 엔진이 특보 API를 조회하거나 시나리오 정답/이벤트명을 조건으로 읽지 않는다.
실제 특보 어댑터·갱신 주기·유효기간·해역 매핑은 별도 통합/합의 범위다.

| 규칙 | 현재 조건 | 누락·경계 |
| --- | --- | --- |
| R3 저산소 | surface_ratio_x >=1.5 AND DO <4.0 | DO=4.0은 제외. 카메라나 특보 상태 없이도 판정 가능 |
| R5 질병 의심 | 정상 환경·명시적 특보 없음·완료된 최근 3개 24시간 블록 평균이 매번 1점 이상 상승·블록별 유효 점수 비율 >=0.8·마지막 블록 평균 >=cause_min_stress | 완전한 72시간 구간 필요. 순간적인 높은 점수나 하루 상승/나머지 정체는 부족 |
| R8 기생충 의심 | 정상 환경·명시적 특보 없음·quality >= rubbing_min(현재0.6)에서 rubbing_rate_x >=3 관측을 6시간 누적 | 현재도 관측 가능/이상이어야 후보 발생. 야간·저품질·미관측 시간은 누적하지 않고 대기 |

정상 환경은 YAML env_constants.normal_env의 표층 **25℃ 미만**, DO **4.0 이상**이다.
이 수치는 구현의 설정값이며 특히 DO는 공식 양식 권고값이 아닌 임시 가정이다. 알림 문구로 변환하지 않는다.
R3의 선택 입력 camera_surface_breathing은 고품질일 때만 보조 근거로 기록하며 표층 쏠림을 대체하지 않는다.
새 MetricRecord 지표를 임의로 추가하지 않는다. 실제 영상 생산자 연결은 후속이다.

R5는 현 시각에 끝나는 24시간 블록들을 사용한다. 각 주기는 score.ts에서 끝나는 cycle_minutes 구간으로 계산한다.
점수 없음은 관측 비율을 낮추며, 정상 환경/특보 없음/ready 상태를 잃으면 이력을 비운다.
평균은 낮·밤 모두 포함하며 정상 평균의 작은 상승에 순간 점수만 높은 경우를 막기 위해 마지막 24시간 평균에도 문턱을 적용한다.
평균 계산은 과거와 현재 점수만 사용한다. 상승 곡선의 공식/기간·관측 비율은 검증용 초안이다.

R8은 저품질·비비기 미관측 시 이력을 유지하지만 그 시간은 더하지 않는다.
연속 미관측이 `R8.behavior.max_pause_hours`(현재16h, #127 MVP 가정)를 **초과**하면 누적을 초기화한다.
정확히16h 미관측 뒤 재개한 유효 관측은 이어서 계산한다. 재개한 관측 주기를 미관측 시간에 더하지 않는다.
각 유효 관측은 연속 pause 시간을 비우며, 관측된 비비기가 3배 미만이면 이상 누적도 즉시 초기화한다.
정상 환경·특보 없음·ready 조건이 깨지거나 스트림의 시각 간격이 한 주기를 넘으면 초기화한다.
따라서 6시간은 벽시계 6시간이 아닌 **유효하게 관측한 이상 시간**이며 밤을 사이에 두고 합산될 수 있다.
이 제한은 합성 검증용 MVP 결정이며 현장 진단 기준 승인을 뜻하지 않는다.
2h 잡음 유예·최근 비비기만으로 R5 제외·이전 R5/R8 원인 자동 유지는 #127 결정에 따라 적용하지 않는다.
현재 점수 게이트·R5 일평균 추세·R5/R8 동시 후보·경보 단계 유지 정책은 보존한다.

같은 이진 점수는 `R2 → R3 → R4 → R8 → R6 → R1 → R5` 순으로 표시한다(가정, #174에서 R4·R6 추가). R8의 구체적 비비기 신호와 R5의
일반 상승 추세가 함께 맞으면 각각 0.5이면서 R8이 먼저 나온다. 우선순위를 진단 정확도로 해석하지 않는다.

## #26 실행·설정 호환·검증 범위

```sh
python -m pipeline hypoxia disease_suspect parasite_suspect
python -m pipeline.frontend hypoxia disease_suspect parasite_suspect --no-label-causes
python -m stress score data/out/parasite_suspect/metrics.jsonl --model models/cage-003.pkl --advisory-state clear
python -m unittest tests.test_cause_extended -v
```

합성 시나리오는 env.advisory_active=false를 명시한 고정 입력이다. 새로운 hypoxia.yaml은 R3용 합성 시나리오이며
기존 기본 시나리오 목록은 유지한다. CLI는 이름을 명시해 실행한다. stress CLI의 --advisory-state 기본은 unknown이며
입력을 확인한 경우에만 clear/active로 지정한다.

최근 main의 프론트 내보내기는 기본적으로 엔진이 빈 경보 구간에 정답 라벨을 보충할 수 있다.
실제 엔진 결과 검증/전달에는 **--no-label-causes**를 사용한다. 이번 테스트는 라벨 보충 없이 세 시나리오의
JSONL→status 타임라인→프론트 형식까지 연결되는지 확인한다. 정답은 테스트의 비교에만 사용한다.

새로 필수화한 YAML 키는 probability.tie_break_order, R5 behavior의 min_daily_increase/min_daily_coverage,
R8 behavior의 missing_observation=pause 및 max_pause_hours(양수)다. 커스텀 설정 묶음도 저장소 예시와 함께 갱신해야 한다.
누락·미지원 값은 ConfigError이며, R1/R2/추세/지속시간 이력은 revision 변경 시 모두 초기화된다.

evidence에는 DO·표층 비율·정상 환경 경계·특보 상태·카메라 품질/보조 근거·24시간별 평균/관측 비율·
관측된 비비기 누적시간·시작 시각을 추가한다. 기존 JSONL 및 DB 행 변환 계약은 유지한다.
R8의 `rubbing_pause_hours`는 연속 미관측 누적시간이며, 미관측 중에는 현재 원인 후보를 새로 게시하지 않는다.
상태 응답의 pending/source/display 확장과 프론트 표시는 #127의 별도 후속 작업이다.
실제 HTTP status 연결·원격 DB 저장·외부 특보 조회·질병/기생충 확정 및 조치/약품 안내는 구현하지 않는다.
시뮬레이터 기본 시드의 결과이며 현장 진단 성능이나 모든 시드에 대한 일반화를 의미하지 않는다.

## #174: R4 적조·R6 작업 스트레스

#45 중 원인 엔진 판정을 먼저 구현했다. DB 입력(작업 기록 API, 적조 특보 적재)과 분석 연결은 [적조 특보·작업 기록 안내](red-tide-work-log.md)에 있다.
외부 입력은 `CauseContext`로 받고, 값이 없으면(None) 해당 규칙은 후보가 되지 않는다.

| 규칙 | 외부 입력 | 조건 (`cause_rules.yaml`) |
| --- | --- | --- |
| R4 적조 | `red_tide_advisory` (적조 특보 발령 해역, 미확인=None) | 특보 True, 기준선 대비 활동량 변화 \|activity_change\| ≥ 0.50 [확인] |
| R6 작업 스트레스 | `work_log_at`·`work_log_type` (그 주기까지 가장 최근 작업 기록, 둘 다 있거나 둘 다 없음) | 종류가 `work_log`(net_change, sorting) 중 하나. 작업 전 6시간 평균 대비 작업 후 6시간 안 최고 지수가 20점 이상 상승. 작업 24시간 뒤에도 지수 ≥ cause_min_stress. 작업 후 72시간까지만 후보 [가정] |

- R6의 지수 이력은 ready 주기만 쓴다. 점수가 없는 주기는 회복 근거로 보지 않는다. revision이 바뀌면 이력을 초기화한다.
- 둘 다 cause_min_stress 예외가 아니다. 지수 40 미만이면 low_stress로 억제된다.
- evidence에 `red_tide_advisory`, `work_log_type`, `work_log_at`, `hours_since_work`, `pre_work_mean_score`,
  `post_work_peak_score`, `score_rise_after_work`를 추가한다.

시연·검증에서는 시나리오 YAML 이벤트로 컨텍스트를 만든다 (`pipeline/contexts.py`의 `scenario_contexts`).
`red_tide` 이벤트의 `advisory_h`(기본 start_h)~`end_h`가 특보 기간이고, `work` 이벤트의 `at_h`·`kind`가 작업 기록이다.

```sh
python -m pipeline red_tide net_change
python -m unittest tests.test_cause_r4_r6 -v
```

합성 시나리오 `red_tide.yaml`, `net_change.yaml`의 행동 변화 크기는 시뮬레이터 가정값이다 (각 YAML 머리말).
검증(`python -m pipeline.validate`)은 두 시나리오의 VC-02·03을 따로 계산하고 기존 요약 합격에는 넣지 않는다.
