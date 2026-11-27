# SBOM — 오픈소스·데이터 라이선스 목록

이슈 #21 / 근거: SDD 9.1, SRS 5.3, NFR-MNT-03, C-06

- 확인일: 2026-11-27
- 확인 방법: 각 저장소의 LICENSE 파일을 GitHub API로 조회하고, 필요한 곳은 LICENSE 본문과 README의 라이선스 안내를 읽었다. Chronos-Bolt는 Hugging Face 모델 카드를 확인했다.
- 이 표는 SDD 9.1의 19개 항목을 기준으로 한다. 실제로 설치한 패키지 버전은 `requirements.txt`를 따르며, 배포 전에 한 번 더 확인한다.
- 표시: ✅ 확인함 / ⚠️ 주의 / ❓ 확인 필요

## 1. 오픈소스 목록 (SDD 9.1 19개)

| # | 모듈 | 오픈소스 | 라이선스 | 확인 | 해커톤 | 상용화 | 비고 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | sonar | [bluerobotics-ping](https://github.com/bluerobotics/ping-python) | MIT | ✅ | 쓰지 않음 (합성) | 사용 | |
| 2 | sonar | [echopype](https://github.com/OSOceanAcoustics/echopype) | Apache-2.0 | ✅ | 쓰지 않음 (합성) | 사용 | |
| 3 | vision | [OpenCV](https://github.com/opencv/opencv) | Apache-2.0 | ✅ | 사용 (M1, M2) | 사용 | |
| 4 | vision | [FUnIE-GAN](https://github.com/xahidbuffon/FUnIE-GAN) | MIT | ✅ ⚠️ | 쓰지 않음 | 조건부 | 아래 2.1 참고 |
| 5 | vision | [Community Fish Detector](https://github.com/filippovarini/community-fish-detector) | 코드 LICENSE 파일 없음, 모델별 안내 있음 | ⚠️ | 사용 (M3) | 교체 또는 재확인 | 아래 2.2 참고 |
| 6 | vision | [Ultralytics](https://github.com/ultralytics/ultralytics) | **AGPL-3.0** | ✅ ⚠️ | 사용 | **제거** | 아래 3 참고 |
| 7 | vision | [RF-DETR](https://github.com/roboflow/rf-detr) | Apache-2.0 (Nano~Large) | ✅ ⚠️ | 선택 | 사용 (M3) | `rfdetr[plus]`의 Atto·Femto·Pico·XL·2XL은 PML 1.0이라 쓰지 않는다 |
| 8 | vision | [trackers](https://github.com/roboflow/trackers) | Apache-2.0 | ✅ | 사용 (ByteTrack) | 사용 | |
| 9 | vision | [supervision](https://github.com/roboflow/supervision) | MIT | ✅ | 사용 | 사용 | |
| 10 | stress | [PyOD](https://github.com/yzhao062/pyod) | BSD-2-Clause | ✅ | 사용 (Isolation Forest) | 사용 | |
| 11 | stress | [scikit-learn](https://github.com/scikit-learn/scikit-learn) | BSD-3-Clause | ✅ | 사용 | 사용 | |
| 12 | stress | [PyTorch](https://github.com/pytorch/pytorch) | BSD-3-Clause 계열 | ✅ | 선택 (LSTM AE) | 사용 | LICENSE 파일에 여러 저작권자 문구가 함께 있어 GitHub가 "Other"로 분류함 |
| 13 | stress | [Chronos-Bolt](https://huggingface.co/autogluon/chronos-bolt-base) | Apache-2.0 | ✅ | 쓰지 않음 | 보조 점수 | 모델 카드 기준. 코드는 [chronos-forecasting](https://github.com/amazon-science/chronos-forecasting) Apache-2.0 |
| 14 | cause | [LightGBM](https://github.com/microsoft/LightGBM) | MIT | ✅ | 쓰지 않음 | M6·M7 | |
| 15 | notify | [LlamaIndex](https://github.com/run-llama/llama_index) | MIT | ✅ | 선택 | 사용 | |
| 16 | api | [FastAPI](https://github.com/fastapi/fastapi) | MIT | ✅ | 사용 | 사용 | |
| 17 | api | [Supabase](https://github.com/supabase/supabase) | Apache-2.0 | ✅ | 사용 | 사용 | 저장소 기준. 호스팅 이용은 Supabase 약관을 따름 |
| 18 | web | [React](https://github.com/facebook/react) | MIT | ✅ | 사용 | 사용 | |
| 19 | web | [MapLibre GL JS](https://github.com/maplibre/maplibre-gl-js) | BSD-3-Clause | ✅ | 사용 | 사용 | LICENSE.txt 본문이 BSD 3조항 |

## 2. 주의가 필요한 항목

### 추가 공통 의존성 (#11)

| 모듈 | 패키지 | 라이선스·근거 | 확인일 | 사용·검증 |
| --- | --- | --- | --- | --- |
| common/config_loader | [PyYAML](https://github.com/yaml/pyyaml) | MIT, [LICENSE](https://github.com/yaml/pyyaml/blob/main/LICENSE) | 2026-11-27 | 설정 YAML 파싱. requirements.txt: `PyYAML>=6.0,<7`, 검증 버전 6.0.4 |

### 카메라 실행 조합 (#18)

`requirements-vision.txt`: OpenCV 5.1.0.95, RF-DETR 1.12.1, trackers 2.7.0,
supervision 0.32.3을 검증했다. 라이선스는 위 OpenCV(Apache-2.0),
[RF-DETR LICENSE](https://github.com/roboflow/rf-detr/blob/develop/LICENSE)(Apache-2.0),
[trackers LICENSE](https://github.com/roboflow/trackers/blob/develop/LICENSE)(Apache-2.0),
[supervision LICENSE](https://github.com/roboflow/supervision/blob/develop/LICENSE.md)(MIT)를 따른다.
M3는 CFD의 RF-DETR Nano 가중치를 사용하며 **Ultralytics를 설치하지 않는다**.
2.2의 가중치·학습 데이터 이용 조건 미확인 사항은 그대로 남는다.
공개 시험 영상의 출처·해시와 실행 결과는 [카메라 시험 기록](docs/vision-pipeline.md)에 있다.
CFD 공식 README는 시험 영상 출처를 Pexels @amnart-keawted-2221199로 밝힌다.
영상·가중치는 저장소에 재배포하지 않는다. 조피볼락 적합성은 #19에서 별도 검증한다.

### 2.1 FUnIE-GAN (이슈 #46)

- 저장소 LICENSE 파일은 **MIT**다 (Copyright 2019 Md Jahidul Islam). SDD와 SRS의 "확인 필요"는 이 결과로 해소된다.
- 다만 GitHub가 자동 분류에 실패해 "Other"로 표시한다. LICENSE 본문 첫 줄이 MIT 문구인 것을 직접 읽어 확인했다.
- **아직 확인하지 못한 것:** 사전학습 가중치와 학습에 쓴 EUVP 등 데이터셋의 라이선스는 별도일 수 있다. 영상 개선에 실제로 도입하기 전에 가중치 배포처의 안내를 확인한다.
- 해커톤은 OpenCV CLAHE를 쓰므로 영향 없다.

### 2.2 Community Fish Detector (M3)

SDD·SRS는 "AGPL"이라고 적었지만, 저장소를 직접 보니 다음과 같다.

- SDD의 링크(`WildHackers/community-fish-detector`)는 GitHub에서 조회되지 않는다. 현재 저장소는 [`filippovarini/community-fish-detector`](https://github.com/filippovarini/community-fish-detector)다. 링크를 고쳐야 한다.
- 저장소 루트에 **LICENSE 파일이 없다.** 코드 자체의 라이선스는 이 방법으로는 확정할 수 없다.
- README는 모델별 "추론 라이선스"를 따로 적는다.

| 모델 | 구조 | 추론 라이선스 (README 표기) |
| --- | --- | --- |
| rf-detr-medium-1024 | RF-DETR Medium | Apache |
| rf-detr-small-1024 | RF-DETR Small | Apache |
| rf-detr-nano-640 | RF-DETR Nano | Apache |
| yolov12x-1.00 | YOLOv12x (Ultralytics) | AGPL (README가 deprecated라고 적음) |

- README는 이 열이 **추론 코드의 라이선스**이고, **학습 데이터(Community Fish Detection Dataset)는 여러 데이터셋이 섞여 라이선스가 제각각**이라고 밝힌다. 모델 가중치와 학습 데이터의 상업 이용 가능 여부는 이 표로 알 수 없다.
- 정리하면: RF-DETR 기반 모델을 쓰면 Ultralytics(AGPL)가 필요 없어질 수 있다. 이 경우 SDD의 "AGPL 2개" 전제가 달라진다. 다만 가중치와 학습 데이터 쪽 조건은 저장소 관리자에게 문의하거나 데이터셋 안내(LILA)를 확인해야 한다.

## 3. 상용화 교체 계획 (AGPL, C-06)

| 항목 | 라이선스 | 해커톤 | 상용화 대안 | 교체 시 할 일 |
| --- | --- | --- | --- | --- |
| Ultralytics | AGPL-3.0 | YOLO 계열 가중치를 돌릴 때만 사용 | **RF-DETR Nano~Large** (Apache-2.0) | `vision` 탐지 호출을 `rfdetr`로 바꾸고, `trackers` 추적 코드는 그대로 둔다 |
| Community Fish Detector (YOLOv12x 가중치) | 추론 AGPL | 사용 | CFD의 RF-DETR 가중치, 또는 조피볼락 이미지로 RF-DETR 미세조정 (이슈 #49) | 가중치·학습 데이터 조건 확인 후 결정 |

- Ultralytics는 AGPL-3.0이라 상용 서비스에 쓰면 애플리케이션 코드 공개 의무가 생길 수 있다. 상용화 단계 전에 제거하거나 상용 라이선스를 확보한다.
- RF-DETR은 `pip install rfdetr`만 쓰면 Apache-2.0이다. `rfdetr[plus]`는 설치하지 않는다.

## 4. 데이터 출처

| 데이터 | 용도 | 라이선스·이용 조건 | 확인 | 이슈 |
| --- | --- | --- | --- | --- |
| [AI Hub 어류 행동 영상](https://www.aihub.or.kr/aihubdata/data/view.do?dataSetSn=155) | 조피볼락 프레임, VC-07 시험셋 | 이용약관의 상업적 이용 가능 여부 미확인 | ❓ | #48 (#63에서 데이터·모델로 이관 제안) |
| Community Fish Detection Dataset | CFD 학습 데이터 (간접 사용) | 여러 데이터셋의 혼합, 제각각 | ⚠️ | 2.2 참고 |
| 국립수산과학원 관리요령 | 알림 RAG 소스 | 출처 URL을 문서마다 기록 | ❓ | #20 |
| 합성 데이터 (시뮬레이터) | 정상·시나리오 데이터 | 자체 생성 | ✅ | #6, #9 |

데이터 항목은 데이터·모델 담당이 조사 결과를 남기면 이 표에 반영한다.

## 5. 갱신 규칙

- 의존성을 추가하거나 바꾸는 PR은 이 표도 같이 고친다.
- 새 항목은 저장소 LICENSE 링크와 확인일을 적는다.
- "확인 필요" 항목이 남아 있으면 상용화 전에 닫는다.
