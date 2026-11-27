# 카메라 파이프라인 시험 — #18

M1(OpenCV 품질) → M2(CLAHE) → M3(Community Fish Detector RF-DETR Nano +
`trackers.ByteTrackTracker`)를 파일 영상에서 실행한다. 공통 `MetricRecord`를 JSONL로
출력하며 기존 `fusion`이 그대로 읽을 수 있다. API·실시간 RTSP·현장 보정은 포함하지 않는다.

## 설치·재현

저장소 루트, Python 3.11 이상. 별도 가상환경을 권장한다. 아래 조합은 Python 3.13 / macOS
CPU에서 검증했다. 카메라 의존성은 `requirements-vision.txt`에 분리해 기존 시뮬레이터·API
사용자가 PyTorch를 설치할 필요가 없게 했다.

```sh
python -m pip install -r requirements-vision.txt
mkdir -p models data/samples
curl -fL 'https://github.com/filippovarini/community-fish-detector/releases/download/2026.07.06-release/cfd-rf-detr-nano-640-2026.02.02.cp-011.20260706-release.pth' -o models/cfd-rf-detr-nano-640.pth
curl -fL 'https://raw.githubusercontent.com/filippovarini/community-fish-detector/main/test-images/3883897-hd_1920_1080_30fps.mp4' -o data/samples/cfd_test_video.mp4
python -m common.config_loader
python -m vision data/samples/cfd_test_video.mp4 --weights models/cfd-rf-detr-nano-640.pth --cage-id cage-003 --start 2026-11-27T10:00:00Z --stride 3 --out data/out/vision-public-full
python -m unittest discover -s tests -v
```

`--start`는 **명시적으로 정한 재생 시각**이며 이 공개 영상의 실제 촬영 시각이 아니다.
`--stride 3`은 3프레임마다 처리한다. 원본 29.970 FPS의 시각을 유지하므로 처리 시각은
약 0.1001초씩 증가한다. `--max-frames 30`으로 짧게 시험할 수 있다. 가중치는 별도
다운로드한 로컬 파일만 받으며 실행 중 모델을 자동 다운로드하지 않는다.

출력은 `data/out/` 아래에 두며 영상·가중치·생성물은 Git에 넣지 않는다.

| 출력 | 내용 |
| --- | --- |
| `records.jsonl` | source=camera인 공통 MetricRecord. 프레임별 품질과 관측 가능한 행동 지표 |
| `frames.jsonl` | 원본 frame_index, 품질 구성값, 개선 여부, 탐지 수, 추적 수·ID·중심, 속도·보정 수심 |
| `summary.json` | 완료 상태, 건수, 처리 시간, 실행 설정, 입력/가중치 SHA-256 |
| `preview.jpg` | 처음 추적 ID가 확정된 원본 프레임의 중심·ID 표시. summary의 preview_written 확인 |

## 측정·누락 계약

- M1: 원본 회색조 표준편차 / Laplacian 분산 / 중간 밝기와의 거리를 각각 0~1로
  정규화하고 가중합한다. 보정값·가중치는 `config/vision.yaml`에 있으며 **시험 가정값**이다.
  현장 맑음/탁함 라벨로 검증한 품질 확률이 아니다.
- 게이트는 기존 `config/thresholds.yaml`의 `fusion.gate_low`,
  `quality.clahe_min/clahe_max/camera_only_min`을 읽는다. 영상 실행 시작 시 고정한다.
  공통 ConfigLoader의 기존 5개 문서 검증도 요구한다. 다른 모듈의 오류·갱신 정책은 바꾸지 않는다(#90).
- q < 0.3: 탐지·추적을 건너뛰고 quality 레코드(value=q, confidence=q)만 낸다.
  진단의 탐지·추적 수는 null이다. 추적·이동 이력을 초기화한다.
- 0.3 ≤ q < 0.6: LAB 밝기 채널에 CLAHE를 적용한다. **confidence는 원본 품질**을 유지한다.
- 탐지 없음 또는 ByteTrack 확정 전 ID=-1: 상태를 no_detections/warming_up으로 기록한다.
  행동 지표를 0으로 채우지 않는다. 탐지 수 0은 실제 물고기 부재의 근거가 아니다.
- 속도는 같은 확정 ID의 **연속 관측** 사이 중심 이동 / 원본 경과 시간(px/s)이다.
  누락·저품질 프레임을 건너 연결하지 않는다. 최초 관측에는 속도·정지 비율이 없다.
- still_ratio는 속도를 계산할 수 있는 ID 중 정지 기준 이하의 비율이다.
  이전·현재 관측 모두 q ≥ 0.6인 ID만 사용한다. 계산에 쓰인 ID와 속도는 진단에서 확인한다.
- count/speed/depth_mean은 공통 10종 enum에 없어 **진단 파일에만** 기록한다(#2 계약).
  지표 enum이나 DB/API 형식을 임의로 늘리지 않는다.

### 설치별 보정

기본값은 아래 항목 모두 null이다. 영상의 y좌표나 px/s를 실제 수심·활동량으로 내보내지 않는다.
설치별 설정 파일을 복사해 `--config path/to/vision.yaml`로 지정한다.

| 보정 키 | 출력·의미 |
| --- | --- |
| `activity_reference_px_s` | 정상 낮 평균 속도. activity = 현재 평균 px/s / 기준 속도. 소나와 모두 정상 낮≈1로 보정해야 융합 가능 |
| `meters_per_pixel` | 진단 speed_m_s. 고정 카메라/평면·원근 보정 가정 필요 |
| `depth_top_m`, `depth_bottom_m`, `net_depth_m` | 세 값이 모두 있으면 화면 높이→수심 선형 보정, depth_mean_m과 vertical_dist [저층, 중층, 표층] 출력 |

수심 보정은 0 ≤ top < bottom ≤ net을 요구한다. 카메라가 일부 층만 보는 경우에는
전체 가두리 분포를 대표하지 못한다. 정지 기준(`still_speed_px_s`)도 해상도·카메라 흔들림에
민감하다. 급이·비비기·응집도는 이 모듈에서 계산하지 않는다.

## 공개 영상 실행 결과 (2026-11-27)

[CFD 공식 시험 영상](https://github.com/filippovarini/community-fish-detector/tree/main/test-images)의
`3883897-hd_1920_1080_30fps.mp4`를 사용했다. 저자는
[공식 README](https://github.com/filippovarini/community-fish-detector#test-video-credit)에
Pexels의 [@amnart-keawted-2221199](https://www.pexels.com/@amnart-keawted-2221199)로 기록되어 있다.
일반 수중 물고기 영상이며 **조피볼락 검증셋이 아니다**. 영상·가중치를 재배포하지 않는다.

| 항목 | 결과 |
| --- | --- |
| 원본 | 1920×1080, 206프레임, 29.970 FPS, 약 6.87초 |
| 처리 | stride=3, 69프레임 전체 클립 |
| 품질 | 최소 0.2862 / 평균 0.3167 / 최대 0.3570 |
| 저품질 생략 | 24프레임, 행동 레코드 없음 |
| CLAHE | 45프레임 |
| 추적 상태 | warming_up 3, tracked 42 |
| 프레임별 최대 | 탐지 11 / 확정 추적 10 |
| 공통 출력 | quality 69개. q<0.6이므로 still_ratio 없음, 미보정이므로 activity/vertical_dist 없음 |
| 융합 입력 검증 | 69개 모두 공통 모델 검증 통과. fuse()의 행동값은 None, 임의의 정상 행동값 없음 |
| 처리 시간 | CPU 7.733초 / 모델 적재 포함 8.923 처리프레임/초 |

이 속도는 저품질 생략 24개를 포함한 로컬 측정이며 Jetson 5 FPS 요건이나
탐지 정확도를 입증하지 않는다. 조피볼락 누락·오탐은 #19(VC-07), AI Hub 수집은 #48이다.

입력 검증 SHA-256 (`shasum -a 256 <파일>`로 비교):

```text
d436ea5039ca946e4d434f693a442a10ed58b32eba9607211ee65e841877a2b5  cfd_test_video.mp4
e3eac301b778e251c3826eae247cc783e1f2dab3fd71a18de92bf3e8999c6f10  cfd-rf-detr-nano-640.pth
```

추가 테스트 16개는 저품질 모델 미적재, CLAHE 경계·원본 보존, 탐지 0·미확정 ID,
보정 속도·수심·층 순서, 미보정 누락, 정지 품질 게이트, 관측 중단 뒤 이력 초기화,
UTC/stride 시각, 잘못된 설정·입력, 실제 ByteTrack의 ID 확정·reset을 확인한다.
카메라 패키지가 없는 기본 설치에서는 카메라 테스트만 skip한다.

## 모델·라이선스 근거

[CFD 현행 안내](https://github.com/filippovarini/community-fish-detector#models)는
YOLOv12x를 deprecated로 표시하며 RF-DETR 모델을 제공한다. 이 구현은 CFD의
`cfd-2026.02.02-rf-detr-nano-640` 가중치와 RF-DETR 런타임을 쓴다.
Ultralytics와 CFD 저장소 코드를 복사하지 않는다. 추론 코드·가중치·학습 데이터의
이용 조건 구분은 [SBOM](../SBOM.md)의 2.2를 따른다.
