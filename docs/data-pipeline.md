# 데이터 파이프라인 실행 안내

합성 시나리오에서 지표를 만들고, 스트레스 지수(M5)를 학습·채점해 API용 파일, 시연 데이터, 검증 수치를 만드는 방법입니다.
모든 명령은 저장소 루트에서 실행합니다. 결과는 **합성 데이터** 기준이며 현장 검증이 아닙니다.

## 준비

Python 3.11 이상이 필요합니다.

```sh
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`ModuleNotFoundError: No module named 'pyod'`가 나오면 가상환경이 켜져 있지 않거나 `requirements.txt`를 설치하지 않은 것입니다.

## 한눈에 보기

| 하고 싶은 일 | 명령 | 걸리는 시간 |
| --- | --- | --- |
| 시나리오 전체를 만들고 채점해 API용 파일 생성 | `python -m pipeline` | 약 10초 |
| 시연 화면용 `scenarios.json` 생성 | `python -m pipeline.frontend` | 1초 이내 |
| 검증 수치 VC-01~05 재현 | `python -m pipeline.validate --seeds 10` | 약 1분 |

시간은 노트북(Apple Silicon) 기준입니다. 순서는 `pipeline` → `pipeline.frontend`입니다. `pipeline.validate`는 따로 돌아가며 앞의 결과를 쓰지 않습니다.

## 1. `python -m pipeline`: 생성 → 학습 → 채점 → API 파일

```sh
python -m pipeline                          # 기본 시나리오 전부
python -m pipeline heatwave_typhoon         # 하나만
python -m pipeline --no-generate            # 이미 만든 metrics.jsonl을 재사용 (학습·채점만 다시)
```

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| `names` | 기본 시나리오 5개 | 채점할 시나리오 이름 (`scenarios/<이름>.yaml`) |
| `--out` | `data/out` | 결과 폴더 |
| `--model` | `models/cage-003.pkl` | 학습한 M5 모델을 저장할 경로 |
| `--no-generate` | 꺼짐 | 시뮬레이터를 다시 돌리지 않음. `config/`나 `stress/`만 바꿨을 때 씁니다 |

하는 일:

1. `normal_14d`(정상 14일)와 지정한 시나리오를 시뮬레이터로 생성합니다.
2. `normal_14d`로 가두리 기준선(낮·밤 분리)을 학습해 `--model`에 저장합니다.
3. 시나리오마다 센서 융합 → 스트레스 지수 → 원인 엔진(`cause/`) → 경보 단계를 계산합니다.
   원인 엔진에는 시나리오 YAML의 `advisory_active`(특보 여부)를 넘깁니다.

기본 시나리오는 `heatwave_typhoon`, `turbidity_event`, `disease_suspect`, `parasite_suspect`, `feeding_drop`입니다.
`hypoxia`(R3), `red_tide`(R4 적조), `net_change`(R6 작업 스트레스)는 기본 목록에 없어서 `python -m pipeline red_tide`처럼 이름을 주고 따로 돌립니다.

마지막에 시나리오마다 한 줄씩 요약이 나옵니다.

```json
{"scenario": "heatwave_typhoon", "cycles": 2304, "max_level": "위험", "level_changes": 3, "lead_time_h": 94.17}
```

`lead_time_h`는 28℃ 주의보 기준 도달보다 첫 '주의'가 몇 시간 먼저였는지입니다 (고수온 시나리오만).

### 결과 파일 (`data/out/<시나리오>/`)

| 파일 | 내용 | 쓰는 곳 |
| --- | --- | --- |
| `metrics.jsonl` | env·sonar·camera `MetricRecord` (5분 주기, 시간 순) | `/ingest/metrics`, `fusion` 입력 |
| `labels.json` | 정답 경보 구간·정답 원인, 주의보 도달 시각 | 검증(VC-02~05) |
| `echogram.npz`, `echogram.png` | 합성 에코그램(Sv, dB)과 미리보기 | `sonar` 입력, 시연 화면 |
| `truth.csv`, `records.jsonl`, `positions.npy` | 시뮬레이터 정답값(층별 비율·활동·탁도), 원본 레코드, 개체 위치 | 디버깅 |
| `stress_score.jsonl` | `stress_score` 테이블 행 (기준선이 준비됐는데 점수가 없는 주기는 빠짐, #68) | Supabase 적재 |
| `cause_estimate.jsonl` | 원인 엔진 결과 | Supabase 적재 |
| `api/status_timeline.jsonl` | 주기별 상태 (지수·단계·원인·근거) | API `/cages/{id}/status` |
| `api/timeseries.json` | 시간별 지표 | API 시계열 |
| `api/meta.json` | 단계 변화, 최고 단계, 선행 시간, 모델 버전 | API·시연 |

`data/out/`과 `models/`는 Git에 올리지 않습니다. 필요하면 위 명령으로 다시 만듭니다.

## 2. `python -m pipeline.frontend`: 시연 데이터

`python -m pipeline`을 먼저 실행해야 합니다 (`api/status_timeline.jsonl`을 읽습니다).

```sh
python -m pipeline.frontend                                         # → data/out/frontend/scenarios.json
python -m pipeline.frontend --dest frontend/src/mocks/scenarios.json   # 시연 화면 데이터 교체
```

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| `names` | 기본 시나리오 5개 | 내보낼 시나리오 |
| `--out-dir` | `data/out` | `python -m pipeline`의 `--out`과 같게 |
| `--frames` | `48` | 시나리오당 장면 수. 시연 슬라이더 끝이 고수온 시나리오 장면 수로 정해지므로 모든 시나리오를 같게 맞춥니다 |
| `--dest` | `<out-dir>/frontend/scenarios.json` | 저장 위치 |
| `--no-label-causes` | 꺼짐 | 원인 엔진이 비운 경보 구간을 정답 라벨로 채우지 않음 |

- 장면마다 `cause_source`가 붙습니다. `cause_engine`은 원인 엔진 결과이고, `scenario_label`은 엔진이 원인을 비운 경보 구간을 시나리오 정답으로 **임시로** 채운 것입니다 (#127). 화면은 이것을 '임시 원인'으로 표시합니다.
- 시연 화면 데이터(`frontend/src/mocks/scenarios.json`)를 바꾸면 #41 시연 대본의 슬라이더 위치(t)가 맞는지 다시 확인합니다.

## 3. `python -m pipeline.validate`: 검증 수치 재현

```sh
python -m pipeline.validate --seeds 10      # → data/out/validation/vc_results.json, vc_results.md
```

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| `--seeds` | `5` | 반복 횟수. 발표·결과표 수치는 10입니다 |
| `--out` | `data/out/validation` | 결과 폴더 |

- 시드마다 정상 14일과 시나리오를 새로 만들고 M5를 다시 학습합니다. 시드 0은 YAML의 `seed` 그대로(= `python -m pipeline` 결과), 시드 k는 `seed + 1000·k`입니다.
- 계산하는 항목: VC-01 탁도 구간 지표 안정성, VC-02 이상 구간 재현율, VC-03 원인 추정 정확도, VC-04 28℃·27℃ 주의보 대비 선행 시간, VC-05 거짓 경보.
- 결과표와 해석은 [validation-results.md](validation-results.md)에 있습니다. `vc_results.md`에는 실행 시각과 설정 리비전이 함께 기록됩니다.

## 단계별로 따로 돌리기

모듈 하나만 고쳤을 때 씁니다. `python -m pipeline`이 아래를 한 번에 합니다.

```sh
# 시뮬레이터: 시나리오 → metrics.jsonl, labels.json, 에코그램
python -m simulator.generate heatwave_typhoon --out data/out

# M4 소나: 에코그램 → sonar MetricRecord (기본: 에코그램 옆 sonar_records.jsonl)
python -m sonar data/out/heatwave_typhoon/echogram.npz

# M5: 기준선 학습, 채점 (+ 원인 엔진·경보 단계)
python -m stress train data/out/normal_14d/metrics.jsonl --model models/cage-003.pkl
python -m stress score data/out/heatwave_typhoon/metrics.jsonl --model models/cage-003.pkl --advisory-state active
```

- `stress score`의 `--advisory-state`는 외부 특보 상태입니다 (`unknown`·`clear`·`active`). 생략하면 `unknown`이고, 이때 원인 규칙 R5·R8은 판정하지 않습니다.
- `stress score --supabase`는 `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` 환경변수로 `stress_score` 테이블에 upsert합니다.

### 실제 수온 자료 (수과원 NIFS)

```sh
# 실시간 수온 → env MetricRecord (NIFS_API_KEY 필요)
python -m nifs realtime --map s0017=cage-003,cage-004 --out data/out/nifs_env.jsonl

# 과거 자료 CSV → 시나리오 YAML의 env 키프레임
python -m nifs keyframes past.csv --station s0017 --start 2023-08-05T00:00:00+09:00
```

관측층 1은 표층 수온, 2는 중층 수온으로 넣습니다.

## 시나리오 만들기·바꾸기

시나리오는 `scenarios/<이름>.yaml`입니다. `env`에 수온·DO 키프레임과 `advisory_active`(특보 여부), `camera`에 영상 품질, `events`에 태풍·질병·기생충 같은 사건을 적고 `seed`로 재현합니다. 정답 구간·원인(`labels.json`)은 이 내용에서 자동으로 만들어집니다.
수온 반응 기준점(23·25·28·30℃)과 태풍 사례(2023 통영 카눈)는 논문·실제 사례를 따랐고, 행동의 크기와 센서 신호는 가정값입니다 (SRS DR-02·03).

새 시나리오를 만들면 다음 순서로 확인합니다.

```sh
python -m pipeline <이름>                        # 요약 줄의 max_level 확인
python -m unittest discover -s tests             # 전체 테스트
```

## 설정을 바꿨을 때

경보 임계값·융합 가중치는 `config/thresholds.yaml`, 원인 규칙은 `config/cause_rules.yaml`에 있습니다. 바꾼 뒤에는 다음 순서로 실행합니다.

```sh
python -m common.config_loader                   # 5개 YAML 묶음 검증
python -m pipeline --no-generate                 # 다시 채점
python -m pipeline.validate --seeds 10           # 검증 수치가 유지되는지 확인
```
