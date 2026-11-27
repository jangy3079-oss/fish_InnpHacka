# R1·R2 원인 추정 엔진 — #25 / #60

## 입력과 실행

원시 센서는 기존 `MetricRecord` 계약을 유지한다. 새 엔진은 기존 파생 산출물인
`StressScore`와 `FusedFeatures`를 받는다. 가두리 문자열 ID와 시각으로 정확히 연결하며
`score.ts - thresholds.cycle_minutes == fused.ts`여야 한다. 현재 주기는 5분이다.
다른 가두리·중복·역순·naive 시각·비유한 숫자·짝이 없는 입력은 오류다.
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
구현된 R1/R2 조건 키·범위·R2 boolean·점수 정책은 공통 로더에서 검증하며,
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
현재 R1/R2 수온차 영역은 겹치지 않아 후보가 있으면 값은 1.0이다. 이는 **후보 간 상대값이며 통계적
진단 확률이나 원인 확정이 아니다**. 점수 곡선·부분 조건 가중치·미구현 원인과의 정규화는 후속 합의 대상이다.
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
파이프라인 JSONL을 API가 즉시 읽는다고 가정하면 안 된다. R3~R8·추천·알림도 이번 범위가 아니다.

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
