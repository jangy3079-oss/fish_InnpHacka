# SDD — 조피볼락 상태 진단 멀티센서 AI 가두리 관리 플랫폼

Nov 27, 2026 · @해리

## 1. 개요

이 문서는 SRS의 요구사항을 **현장-엣지-서버-클라이언트 4계층, 9개 모듈, 7개 AI 모델**로 구현하는 설계를 정의한다. 요구사항 ID(FR-, NFR-)와 모델 번호(M1\~M7)는 SRS와 같다.

### 1.1 설계 목표

- 해커톤(2026-11-28 12:00) 안에 **고수온 → 피난처 소실 시나리오 하나를 처음부터 끝까지** 돌린다.
- 합성 데이터를 실제 장비와 **같은 형식**으로 만들어, 나중에 입력만 바꾸면 되게 한다.
- 판단을 설명할 수 있게 한다. 모든 경보는 근거 지표와 규칙 ID를 남긴다.

### 1.2 설계 원칙

| 원칙 | 내용 |
| --- | --- |
| 오픈소스 우선 | 탐지·추적·소나 처리·이상 탐지·백엔드는 검증된 오픈소스를 그대로 쓴다. 직접 만드는 건 시뮬레이터, 융합, 원인 규칙, 손익 계산기뿐이다 (9장) |
| 단순함 | 판단 AI 하나에 맡기지 않고, 비지도 이상 탐지(M5) + 규칙 기반 원인(M6)으로 시작한다 |
| 엣지 처리 | 영상·에코그램 원본은 현장에서 처리하고 지표만 올린다 (LTE 저속 대응) |
| 센서 독립 | 센서는 모두 같은 "지표 레코드" 형식으로 내보내고, 하나가 빠져도 융합은 계속 돈다 |
| 설정 분리 | 임계값·원인 규칙·방류 요건·단가는 YAML로 두고 코드와 분리한다 |

### 1.3 범위 표시

각 설계 항목에는 **\[MVP\]**(해커톤 구현), **\[확장\]**(2단계 이후)를 붙인다. 표시가 없으면 MVP다.

## 2. 시스템 아키텍처

시스템은 현장-엣지-서버-클라이언트 4계층이다. 영상·에코그램 원본은 엣지에서 처리하고, LTE로는 지표만 올린다. 해커톤에서는 시뮬레이터가 현장 장비를 대신하고, 엣지와 서버는 노트북 한 대에서 돌린다.

&#91;embedded content: 시스템 아키텍처 · 4계층 + 외부 데이터\]

현장 장비가 바뀌어도 엣지가 내보내는 지표 형식(MetricRecord)은 같아서, 서버 모듈은 고칠 필요가 없다.

## 3. 데이터 흐름

한 주기(기본 5분)는 측정에서 어민 결정까지 8단계로 흐른다. 단계마다 결과를 DB에 남겨, 알림 하나를 보고도 어떤 지표와 규칙이 그 판단을 만들었는지 되짚을 수 있다.

&#91;embedded content: 데이터 흐름 · 측정→진단→원인→대응→전달\]

어민의 결정과 실제 폐사 기록은 다시 기준선과 규칙 임계값 보정에 쓰인다(점선).

## 4. 모듈 설계

시스템은 9개 모듈로 나너며, 모듈 사이는 **지표 레코드(MetricRecord)** 하나의 형식으로만 주고받는다. 그래서 합성 시뮬레이터를 실제 소나로 바꿔도 뒤쪽 모듈은 그대로 둔다.

| # | 모듈 | 책임 | 입력 | 출력 | 쓰는 오픈소스 | 관련 요구사항 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `simulator` | 수온·층간 수온 차·DO 시나리오에 따라 개체 위치를 움직이고 합성 에코그램·환경값 생성 | 시나리오 YAML | 에코그램(시간×수심), 층별 수온·DO | NumPy, SciPy (자체 개발) | FR-MEA-03, 07 |
| 2 | `sonar` | 에코그램 → 수심별 분포·활동량 추출 (M4) | 에코그램 | MetricRecord (vertical\_dist, activity) | echopype, NumPy, bluerobotics-ping \[확장\] | FR-MEA-01, 02, 09 |
| 3 | `vision` | 품질 점수(M1) → 개선(M2) → 탐지·추적(M3) → 개체 지표 | 프레임 | MetricRecord (quality, count, speed, depth\_mean, rubbing\_rate) | OpenCV, Community Fish Detector, Ultralytics, trackers, supervision | FR-MEA-04\~06, 10 |
| 4 | `fusion` | 영상 품질로 가중해 소나·카메라 지표 결합 | MetricRecord 여러 개 | FusedFeatures | NumPy (자체 개발) | FR-DIA-01 |
| 5 | `stress` | 가두리·밤낮별 기준선 학습, 스트레스 지수·경보 단계 (M5) | FusedFeatures 시계열 | StressScore (0\~100, level) | PyOD, scikit-learn, PyTorch, Chronos-Bolt \[확장\] | FR-DIA-02\~07 |
| 6 | `cause` | 행동 패턴 × 환경 신호 → 의심 원인·확률·근거 (M6) | StressScore, FusedFeatures, 환경값, 특보 | CauseEstimate | 규칙 YAML (자체 개발), LightGBM \[확장\] | FR-CAU-01\~07 |
| 7 | `decision` | 원인별 대응 선택지, 손익, 방류 요건 확인, 실행 마감 | CauseEstimate, 어가 정보, 시세, 규칙 YAML | Recommendation | 자체 개발 | FR-DEC-01\~07 |
| 8 | `notify` | 근거 수치를 넣은 어민용 문장 생성, 중복 억제 | Recommendation, 관리요령 문서 | Alert | LLM API, LlamaIndex | FR-DEL-01\~03, NFR-REL-04 |
| 9 | `api` + `web` | REST API, 어민 화면, 지자체 대시보드, 시간 슬라이더 | DB | JSON, 화면 | FastAPI, Supabase, React, MapLibre GL JS | FR-DEL-04\~06, FR-ADM-01\~04 |

### 4.1 공통 데이터 형식

```markdown
MetricRecord
  cage_id     : 가두리 ID
  ts          : 측정 시각 (UTC)
  source      : sonar | camera | env
  metric      : vertical_dist | activity | cohesion | feeding | still_ratio | rubbing_rate | quality | temp_surface | temp_mid | do
  value       : 숫자 또는 배열 (vertical_dist는 수심 구간별 비율)
  confidence  : 0~1 (camera는 영상 품질 점수, sonar는 신호 품질)
```

### 4.2 모듈별 오류 처리

- `vision`: 품질 점수가 기준 아래면 탐지를 건너뛰고 `confidence`만 내보낸다. 탐지 수 0을 "물고기 없음"으로 쓰지 않는다 (NFR-REL-01).
- `fusion`: 들어오지 않은 센서는 가중치 0으로 두고 `missing_sources`에 기록한다 (NFR-REL-03).
- `stress`: 기준선 학습량이 부족하면 지수 대신 `warming_up` 상태를 낸다 (FR-DIA-06).
- `notify`: LLM 호출이 실패하면 미리 만든 템플릿 문장으로 보낸다.

## 5. AI 모델 설계

7개 모델 중 해커톤에서 **새로 학습하는 건 M1(가벼운 분류기)과 M5(비지도)뿐**이다. M2·M3는 사전학습 오픈소스를 그대로 쓰고, M4는 신호처리, M6은 규칙이다. M7은 현장 폐사 데이터가 쌓인 뒤에 학습한다.

| 모델 | 역할 | 해커톤 구현 | 오픈소스 | 입력 → 출력 | 상용화 대안 |
| --- | --- | --- | --- | --- | --- |
| M1 영상 품질 판단 | 프레임 신뢰도 | 규칙 점수(대비·라플라시안 분산·평균 밝기) → 맑음/탁함 라벨 프레임으로 임계값만 보정 | OpenCV | 프레임 → quality 0\~1 | 가벼운 CNN 분류기 \[확장\] |
| M2 수중 영상 개선 | 탁한 프레임 보정 | OpenCV CLAHE (quality 0.3\~0.6 구간만 적용) | OpenCV, FUnIE-GAN \[확장, 라이선스 확인 후\] | 프레임 → 개선 프레임 | — |
| M3 탐지·추적 | 개체 위치·ID | Community Fish Detector 그대로 ("fish" 단일 클래스) + trackers ByteTrack | Community Fish Detector, Ultralytics, trackers, supervision | 프레임 → 박스·ID → 속도·평균 수심·정지 비율·비비기 횟수 | RF-DETR(Apache 2.0)을 조피볼락 이미지로 미세조정 |
| M4 소나 지표 추출 | 수직 분포·활동량 | 신호처리 (아래 식) | NumPy, SciPy, echopype \[실장비\] | 에코그램 → 수심 구간별 비율, 활동량 | 경량 CNN \[확장\] |
| M5 스트레스 지수 | 평소 대비 이탈 | Isolation Forest, 가두리×밤낮 기준선 2개 | PyOD, scikit-learn | FusedFeatures 윈도우 → 0\~100 | LSTM 오토인코더(PyTorch), Chronos-Bolt 보조 점수 |
| M6 원인 추정 | 의심 원인·확률 | 규칙 YAML 8개 (6.3절) | 자체 개발 | 패턴+환경 → 원인별 점수 | LightGBM 분류기 |
| M7 폐사 위험 예측 | 며칠 안 폐사 확률 | 구현 안 함 \[확장\] | LightGBM | 행동+환경+폐사 기록 → 확률 | — |
| — 알림 문장 | 어민용 문장 | LLM API + 관리요령 검색 | LlamaIndex | Recommendation → 3문장 이내 | — |

### 5.1 M3 탐지기를 고른 이유

- **Community Fish Detector**는 41개 공개 데이터셋으로 학습됐고, 그중 양식장 영상(노르웨이 양식장, 탁한 영국 패류·해조류 양식장 등)이 들어 있다. 학습 없이 바로 쓸 수 있어 해커톤에 맞다.
- 학습 데이터에 조피볼락은 명시돼 있지 않다. 그래서 VC-07(조피볼락 이미지 10\~20장 시험)을 먼저 돌린다. 잘 안 잡히면 `conf`를 0.1까지 낮추고, 그래도 안 되면 카메라 지표는 시뮬레이션으로 대신한다.
- AGPL이라 상용화 때는 \*\*RF-DETR Nano\~Large(Apache 2.0)\*\*를 조피볼락 데이터로 미세조정해 바꾼다. trackers는 어느 탐지기에도 붙으므로 추적 코드는 그대로 둔다.

### 5.2 M4 소나 지표 계산

에코그램 E(t, z)는 시각 t, 수심 z의 에코 강도다. 수심을 저층·중층·표층 3구간(또는 1m 단위)으로 나눈다.

```latex
p_k(t) = \frac{\sum_{z \in k} E(t,z)}{\sum_{z} E(t,z)} \qquad A(t) = \frac{1}{|Z|} \sum_{z} \left| E(t,z) - E(t - \Delta t, z) \right|
```

- p\_k(t): 수심 구간 k의 어군 비율 (수직 분포, ✅ 지표)
- A(t): 인접 핑 사이 에코 변화량 (활동량, ✅ 지표)
- 응집도는 같은 핑에서 에코가 모인 수심 폭(분산의 역수)으로 계산한다 (🔺 지표)

### 5.3 M5 학습 방법

1. 특징: 1시간 윈도우마다 p\_저층, p\_중층, p\_표층, A 평균, A 표준편차, 응집도, (카메라 있으면) 정지 비율·급이 반응.
2. 기준선: 가두리마다 낮(06\~18시)과 밤을 따로 학습한다. 해커톤은 시뮬레이터의 "정상 14일"로 학습한다.
3. 점수: Isolation Forest의 이상 점수를 학습 구간 분포의 백분위로 바꿔 0\~100으로 만든다.
4. 콜드 스타트: 기준선 윈도우가 7일치(168개) 미만이면 `warming_up`으로 표시한다. 이 7일은 설계 가정값이다.

### 5.4 몸 비비기 판정 (M3 후처리) \[확장\]

- **입력:** M3 추적 결과(개체 ID별 박스 궁적)와 화면에 미리 지정한 그물·바닥 영역(ROI).
- **이벤트:** 한 개체의 박스가 ROI 경계 가까이에 있고, 1초 안에 몸 축 각도(박스 종횡비)가 크게 바뀌며 방향을 빠르게 틀면 비비기 1회로 센다.
- **지표:** rubbing\_rate = 비비기 횟수 ÷ (추적 개체 수 × 시간). 영상 품질 q ≥ 0.6 프레임만 쓰고, MetricRecord(metric = rubbing\_rate)로 낸다. 등급은 🔺(보조 지표)다.
- **보정:** 거리·각도·시간 기준은 가정값이며, 비비기 장면 영상(SRS DR-10)으로 맞춘다. 해커톤은 시뮬레이터가 rubbing\_rate를 직접 만든다.
- 비비기는 기생충 외에 수질·아가미 자극 때도 늘어나기 때문에, M6에서 '기생충 의심'으로만 쓴다. 확정은 검경이다.

## 6. 핵심 알고리즘

직접 만드는 로직은 네 가지다. 임계값과 가중치는 모두 설계 가정값이며 `config/*.yaml`에 두고, 2·3단계 실증에서 다시 맞춘다.

### 6.1 품질 가중 융합 (fusion)

지표마다 소나 값 s와 카메라 값 c를 영상 품질 q(0\~1)로 섞는다. 카메라만 있는 지표(정지 비율, 외관)는 q가 0.6 미만이면 사용하지 않는다.

```latex
f = (1 - w)\, s + w\, c, \qquad w = \begin{cases} 0 & q < 0.3 \\ 0.5\,(q - 0.3)/0.4 & 0.3 \le q < 0.7 \\ 0.5 & q \ge 0.7 \end{cases}
```

- 카메라 비중은 최대 0.5로 둔다. 카메라는 가두리 일부만 보기 때문이다.
- 소나가 없으면 f = c, 단 q < 0.3이면 지표를 비운다 (거짓 경보 방지).

### 6.2 스트레스 지수와 경보 단계 (stress)

| 지수 | 단계 | 조건 |
| --- | --- | --- |
| 0\~39 | 정상 | — |
| 40\~59 | 주의 | 2시간 연속 유지 |
| 60\~79 | 경고 | 2시간 연속 유지 |
| 80\~100 | 위험 | 1시간 연속 유지, 또는 '피난처 소실' 규칙 발동 시 즉시 |

- 단계가 내려갈 때는 6시간 연속으로 조건이 풀려야 한다 (경보가 오르내리는 것 방지).
- 같은 단계 알림은 6시간에 1회만 보낸다 (NFR-REL-04).

### 6.3 원인 추정 규칙 (cause)

규칙마다 조건을 만족한 정도로 점수(0\~1)를 내고, 점수를 합이 1이 되게 나눠 "확률"로 표시한다. 스트레스 지수가 40 미만이면 원인 추정을 하지 않는다 (섭식 저하 규칙만 예외).

| 규칙 | 의심 원인 | 행동 조건 | 환경 조건 | 첫 추천 |
| --- | --- | --- | --- | --- |
| R1 | 고수온 (피신 중) | 중·저층 비율 기준선 대비 1.5배 이상, 활동량 −20% 이하 | 표층 25℃ 이상, 표층−중층 차 3℃ 이상 | 사료 중단, 산소 보강 |
| R2 | 피난처 소실 | 응집도 급락 + 활동량 −40% 이하 | 표층−중층 차 1℃ 미만으로 떨어짐, 표층 25℃ 이상 | 즉시 경보, 그물 수심·월하장·출하·방류 비교 |
| R3 | 저산소 | 표층 비율 증가 (카메라: 수면 호흡) | DO 기준값 미만 (값 확인 필요) | 산소 공급, 밀도 조절 |
| R4 | 적조 | 급격한 활동 변화 | 적조 특보 발령 해역 | 사료 중단, 적조 대응 요령 |
| R5 | 질병 의심 | 이상 지수가 3일 이상 서서히 상승 | 수온·DO 정상, 특보 없음 | 공통 1차 조치 + 수산질병관리사 진단 요청 (6.5절) |
| R6 | 작업 스트레스 | 작업 기록 직후 지수 상승, 24시간 내 회복 안 됨 | 망갈이·선별 기록 | 회복 모니터링, 급이 조절 |
| R7 | 섭식 저하 | 급이 반응만 감소, 나머지 정상 | 표층 23℃ 이상 | 사료량 감량 |
| R8 | 기생충 의심 | 비비기 횟수(rubbing\_rate)가 기준선 대비 3배 이상, 6시간 이상 지속 (가정값) | 수온·DO 정상, 특보 없음 | 공통 1차 조치 + 검경 진단 요청 (6.5절) |

해커톤에서는 R1, R2, R3, R5를 구현하고, R8은 시뮬레이터의 rubbing\_rate 값으로 시연한다. 수온 기준(23·25℃)은 조피볼락 논문에서, 층간 수온 차는 2023년 통영 사례(표층 26\~27℃, 중층 21\~22℃)에서 가져왔다. 나머지 배수·비율은 가정값이다.

### 6.4 손익 계산 (decision)

선택지 a의 예상 손실은 "살아남을 가치의 감소 + 조치 비용 − 지원금·보험금"으로 계산한다. 크기별 재고(치어, 중간어, 출하 크기)마다 따로 계산하고 더한다.

```latex
L(a) = \sum_{g} N_g \, P_g \, W_g \, m_g(a) + C(a) - S(a)
```

- N\_g: 크기 그룹 g의 마릿수, P\_g: kg당 시세, W\_g: 마리당 무게(kg)
- m\_g(a): 선택지 a를 골랐을 때의 예상 손실률. 유지는 예상 폐사율, 방류는 1(전량 포기), 출하는 시세 할인율
- C(a): 조치 비용 (산소·장비·운송), S(a): 지원금·보험금 (방류 지원금은 어가당 상한 적용)
- 예상 폐사율은 MVP에서 경보 단계별 고정값(가정)을 쓰고, M7이 생기면 모델 값으로 바꾼다.

**긴급방류 요건 확인:** 품종 = 조피볼락, 크기 = 치어 또는 중간어, 동일 해역 일시 10만 마리 이내(넘으면 1주 간격 순차 또는 1km 이격 분산). 하나라도 어긋나면 방류 선택지를 "불가"로 표시하고 이유를 보여준다.

### 6.5 질병 계열 공통 1차 조치 (decision, notify)

R5(질병 의심)나 R8(기생충 의심)이 1순위면 손익 비교 대신 아래 흐름으로 간다. 증상이 비슷한 질병은 하나의 대응으로 묶되, 약품은 다루지 않는다 (SRS C-01, FR-DEC-07).

1. `config/first_aid.yaml`의 공통 1차 조치를 추천한다: 사료 감량, 밀도 조절, 폐사체 즉시 수거, 이상 가두리 작업 도구 분리, 산소 보강. 문구는 수과원 관리요령 검색 결과만 쓴다.
2. 진단 요청(`POST /cages/{id}/diagnosis-request`)을 자동으로 만든다. 행동 지표 그래프, 이상 시작 시점, 의심 원인·확률을 담는다.
3. `notify`는 생성 문장에 약품 사전(항생·항균제, 구충제, 백신 등 성분명·제품명)의 단어가 있으면 그 문장을 버리고 템플릿으로 보낸다. 약품명·투약량은 내보내지 않는다 (SRS VC-08).

어민 알림 예: "질병 또는 기생충이 의심됩니다. 오늘 사료를 줄이고 죽은 고기는 바로 건지세요. 수산질병관리사에게 진단 요청을 보냈습니다."

## 7. 데이터 설계

DB는 Supabase(PostgreSQL) 하나에 테이블 10개를 둔다. 시계열 지표는 `metric` 한 테이블에 쌓고 `(cage_id, ts)` 인덱스를 건다. 어가 경영 정보는 행 단위 보안(RLS)으로 해당 어가와 권한 있는 공무원만 본다 (NFR-SEC-01).

| 테이블 | 주요 필드 | 설명 |
| --- | --- | --- |
| `sea_area` | id, name, geom(폴리곤), nifs\_station\_id | 해역. 수과원 관측소와 연결 |
| `farm` | id, sea\_area\_id, owner\_user\_id, name, location | 양식장 (어가) |
| `cage` | id, farm\_id, is\_sentinel, depth\_m, net\_depth\_m | 가두리. 센티넬 여부 표시 |
| `stock` | id, cage\_id, size\_group(fry/mid/market), count, avg\_weight\_kg, updated\_at | 크기별 재고 (손익·방류 요건용) |
| `sensor` | id, cage\_id, type(sonar/camera/env), model, status, last\_seen | 설치 센서 |
| `metric` | cage\_id, ts, source, metric, value(jsonb), confidence | 지표 레코드 (4.1절 형식) |
| `stress_score` | cage\_id, ts, score, level, baseline\_status, model\_version | 스트레스 지수 |
| `cause_estimate` | id, cage\_id, ts, cause, probability, rule\_id, evidence(jsonb) | 의심 원인과 근거 |
| `recommendation` | id, cause\_estimate\_id, options(jsonb: 선택지·손익·가능 여부), deadline, chosen\_option, outcome | 추천과 어민 선택·결과 (FR-DEC-06) |
| `alert` | id, recommendation\_id, user\_id, channel, message, sent\_at, read\_at | 보낸 알림 |

**설정 파일 (DB가 아닌 YAML)**

| 파일 | 내용 |
| --- | --- |
| `config/thresholds.yaml` | 경보 단계 임계값, 유지 시간, 중복 억제 간격 |
| `config/cause_rules.yaml` | R1\~R8 조건과 추천 |
| `config/release_rules.yaml` | 긴급방류 품종·크기·수량 요건, 지원금 상한 |
| `config/costs.yaml` | 시세(임시 입력), 장비·운송 비용, 단계별 예상 폐사율 |
| config/first\_aid.yaml | 질병 계열 공통 1차 조치 목록, 알림에서 막을 약품 사전 |
| `scenarios/*.yaml` | 시뮬레이터 시나리오 (수온 곡선, 태풍 시점, 탁도 이벤트) |

## 8. 인터페이스 설계

REST API는 FastAPI로 12개를 둔다. 엣지는 `/ingest`로만 데이터를 올리고, 화면은 나머지 조회 API를 쓴다. 인증은 Supabase Auth 토큰을 쓴다.

| 메서드 | 경로 | 용도 | 사용자 | 관련 요구사항 |
| --- | --- | --- | --- | --- |
| POST | `/ingest/metrics` | MetricRecord 묶음 업로드 | 엣지 | FR-MEA-01\~07 |
| GET | `/cages/{id}/status` | 현재 스트레스 지수·단계·의심 원인 | 어민 | FR-DIA-03\~05 |
| GET | `/cages/{id}/timeseries?from&to&metric` | 근거 그래프용 지표 시계열 | 어민, 공무원 | FR-DEL-02 |
| GET | `/cages/{id}/echogram?from&to` | 에코그램 미리보기(축소 이미지) | 어민, 공무원 | FR-DEL-02 |
| GET | `/cages/{id}/recommendation` | 최신 추천과 선택지별 손익 | 어민 | FR-DEC-01\~05 |
| POST | `/recommendations/{id}/choice` | 어민이 고른 선택지 기록 | 어민 | FR-DEC-06 |
| PUT | `/cages/{id}/stock` | 크기별 재고·보험 입력 | 어민 | FR-ADM-03 |
| GET | `/areas/stress-map` | 해역별 스트레스 지도 (집계만) | 공무원 | FR-DEL-04, NFR-SEC-03 |
| GET | `/areas/{id}/release-demand` | 긴급방류 수요 예측 | 공무원 | FR-DEL-05 |
| POST | `/cages/{id}/diagnosis-request` | 수산질병관리사 진단 요청 | 어민 | FR-DEL-06 |
| POST | `/demo/scenario/{name}?t=` | 시나리오 재생·시간 이동 | 시연 | FR-ADM-04 |
| PUT | `/admin/config/{file}` | 임계값·규칙 수정 | 관리자 | FR-ADM-02 |

**응답 예시: `GET /cages/3/status`**

```markdown
{
  "cage_id": 3,
  "ts": "2026-08-12T05:00:00Z",
  "stress_score": 64,
  "level": "경고",
  "baseline_status": "ready",
  "cause": [
    {"name": "고수온(피신 중)", "probability": 0.72, "rule_id": "R1"},
    {"name": "저산소", "probability": 0.18, "rule_id": "R3"}
  ],
  "evidence": {"mid_layer_ratio_x": 1.8, "activity_change": -0.35, "temp_surface": 26.4, "temp_mid": 22.1},
  "camera_weight": 0.2,
  "missing_sources": []
}
```

예시의 수치는 시연 시나리오 값이다.

## 9. 오픈소스 구성, 배포, 해커톤 구현 계획

바로 가져다 쓰는 오픈소스는 19개다. 직접 만드는 건 `simulator`, `fusion`, `cause` 규칙, `decision` 손익 계산기다. AGPL 2개는 해커톤에서만 쓰고 상용화 때 교체한다.

### 9.1 오픈소스 구성

| 모듈 | 오픈소스 | 설치 | 라이선스 | 해커톤 | 상용화 |
| --- | --- | --- | --- | --- | --- |
| `sonar` | [bluerobotics-ping](https://github.com/bluerobotics/ping-python) | `pip install bluerobotics-ping` | MIT | 쓰지 않음 (합성) | 사용 |
| `sonar` | [echopype](https://pypi.org/project/echopype/) | `pip install echopype` | Apache 2.0 | 쓰지 않음 (합성) | 사용 |
| `vision` | OpenCV | `pip install opencv-python` | Apache 2.0 | 사용 (M1, M2) | 사용 |
| `vision` | [FUnIE-GAN](https://github.com/xahidbuffon/funie-gan) | 저장소 클론 | 확인 필요 | 쓰지 않음 | 라이선스 확인 후 |
| `vision` | [Community Fish Detector](https://github.com/WildHackers/community-fish-detector) | Releases에서 .pt | AGPL | 사용 (M3) | RF-DETR로 교체 |
| `vision` | Ultralytics | `pip install ultralytics` | AGPL | 사용 | 제거 |
| `vision` | [RF-DETR](https://roboflow.com/model-licenses/rf-detr) | `pip install rfdetr` | Apache 2.0 (Nano\~Large) | 선택 | 사용 (M3) |
| `vision` | [trackers](https://trackers.roboflow.com/latest/learn/about/) | `pip install trackers` | Apache 2.0 | 사용 (ByteTrack) | 사용 |
| `vision` | supervision | `pip install supervision` | MIT | 사용 | 사용 |
| `stress` | [PyOD](https://pypi.org/project/pyod/) | `pip install pyod` | BSD | 사용 (Isolation Forest) | 사용 |
| `stress` | scikit-learn | `pip install scikit-learn` | BSD | 사용 | 사용 |
| `stress` | PyTorch | `pip install torch` | BSD | 선택 (LSTM AE) | 사용 |
| `stress` | [Chronos-Bolt](https://huggingface.co/autogluon/chronos-bolt-base) | `pip install chronos-forecasting` | Apache 2.0 | 쓰지 않음 | 보조 점수 |
| `cause` | LightGBM | `pip install lightgbm` | MIT | 쓰지 않음 | M6·M7 |
| `notify` | LlamaIndex | `pip install llama-index` | MIT | 선택 | 사용 |
| `api` | FastAPI | `pip install fastapi` | MIT | 사용 | 사용 |
| `api` | Supabase (PostgreSQL) | 호스팅 또는 셀프 호스팅 | Apache 2.0 | 사용 | 사용 |
| `web` | React | `npm i react` | MIT | 사용 | 사용 |
| `web` | MapLibre GL JS | `npm i maplibre-gl` | BSD | 사용 | 사용 |

라이선스는 검색으로 확인한 항목(링크 있음)과 널리 알려진 항목이 섞여 있다. 최종 배포 전에 각 저장소의 LICENSE 파일을 다시 확인해 `SBOM.md`에 기록한다. supervision의 `sv.ByteTrack`은 폐기 예정이라 [trackers](https://supervision.roboflow.com/latest/trackers/)를 쓴다.

### 9.2 배포

| 환경 | 구성 |
| --- | --- |
| 해커톤 | 노트북 1대에서 `simulator` + 백엔드 + 프론트 실행, Supabase 클라우드, LLM API |
| 2단계 (수조) | 연구기관 RAS 수조에 카메라 + 에코사운더, 엣지 PC |
| 3단계 (현장) | 센티넬 가두리에 Ping360 + 수중·수면 카메라 + 층별 센서, Jetson + 태양광 + LTE |

### 9.3 디렉터리 구조

```markdown
rockfish-guard/
  simulator/      개체 기반 시뮬레이터, 합성 에코그램
  sonar/          M4 지표 추출 (echopype·brping 어댑터 자리)
  vision/         M1 품질, M2 CLAHE, M3 탐지·추적
  fusion/         품질 가중 융합
  stress/         M5 기준선·지수
  cause/          M6 규칙 엔진
  decision/       손익·방류 요건
  notify/         알림 문장, 템플릿
  api/            FastAPI
  web/            React (어민 화면, 대시보드)
  config/         thresholds, cause_rules, release_rules, costs (YAML)
  scenarios/      heatwave_typhoon.yaml, turbidity_event.yaml, disease_suspect.yaml, parasite_suspect.yaml
  SBOM.md         오픈소스 라이선스 목록
```

### 9.4 해커톤 구현 계획 (4인)

| 담당 | 모듈 | 먼저 끝낼 것 |
| --- | --- | --- |
| 음향·융합 | `simulator`, `sonar`, `fusion` | 고수온→태풍 시나리오 하나의 에코그램과 MetricRecord |
| 비전 | `vision` | 공개 영상 1개에 품질 점수 + 탐지·추적, 조피볼락 이미지 시험(VC-07) |
| 판단·결정 | `stress`, `cause`, `decision`, `notify` | R1·R2 규칙과 손익 계산 |
| 프론트·기획 | `api`, `web` | 시간 슬라이더 대시보드, 발표 |

시나리오 하나를 처음부터 끝까지 잇는 것이 먼저다. 탐지 모델 성능 개선에는 시간을 쓰지 않는다.
