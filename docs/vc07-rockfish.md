# VC-07 조피볼락 탐지 적용성 시험 — #19

CFD RF-DETR Nano를 같은 조피볼락 이미지 10~20장에서 conf=0.3/0.1로 비교한다.
시험 조건은 `config/vision_eval.yaml`, 카메라 품질·개선 설정은 기존
`config/vision.yaml`과 `thresholds.yaml`을 사용한다. 운영 탐지 임계값은 바꾸지 않는다.

## 데이터 준비

[AI Hub 어류 행동 영상](https://www.aihub.or.kr/aihubdata/data/view.do?dataSetSn=155)의
다운로드한 이미지와 JSON 라벨을 로컬에서 사용한다. 공식 구조는
`categories/images/annotations`, bbox는 `[x,y,width,height]`이다.
샘플 다운로드에도 AI Hub 로그인이 필요하다. 이미지를 다른 어종으로 대체하지 않는다.

```sh
python -m pip install -r requirements-vision.txt
python -m vision.evaluate extract --archive '/path/to/Training/[원천]kr_part2.zip' --labels '/path/to/Training/[라벨]kr_part2.json' --category KoreaRockfish --candidates 120 --out data/out/vc07/pool
python -m vision.evaluate prepare --labels data/out/vc07/pool/labels.json --images-root data/out/vc07/pool --category KoreaRockfish --source-url 'https://www.aihub.or.kr/aihubdata/data/view.do?dataSetSn=155' --out data/out/vc07/manifest.json
python -m vision.evaluate run --manifest data/out/vc07/manifest.json --images-root data/out/vc07/pool --weights models/cfd-rf-detr-nano-640.pth --out data/out/vc07/results
```

`--category`는 다운로드한 JSON의 **실제 categories.name**과 정확히 같아야 한다.
이름이 다르면 출처 설명을 확인한 뒤 지정한다. 일부 문자열이나 파일 이름만 보고
조피볼락으로 분류하지 않는다. 이번 원본의 실제 범주명은 `KoreaRockfish`(category_id=2)다.
비교할 여러 조피볼락 범주가 있으면 옵션을 반복한다.

큰 ZIP은 `extract`로 후보만 푼다. 라벨상 조피볼락 단일 범주 이미지 중 조명 코드별
동일한 수를 배정하고, 각 조명 안에서 날짜·파일명 정렬 순위에 균등 간격으로 뽑는다.
원본 JSON은 바꾸지 않으며 추출 라벨에 원본 라벨 해시·출처와 후보 선정 방법을 보존한다.
선택한 ZIP 항목의 CRC를 확인한다. 전체 ZIP 해시는 계산하지 않는다.

준비 단계는 실제 이미지 크기·JSON ID 연결·상자 유효성을 확인한다. 다른 어종이 함께
라벨링된 이미지는 제외하며, 같은 픽셀의 파일을 여러 장으로 세지 않는다. 후보를 원본
품질 점수 순서로 정렬한 뒤 균등 간격 순위를 뽑는다. 결과를 보고 잘 잡힌 사진만 고르지 않는다.
선택은 품질 범위를 살피기 위한 것이며 무작위 표본이나 독립 검증셋을 보증하지 않는다.

10~19장만 확보한 경우 `config/vision_eval.yaml`을 복사해 `sample_count`를 수정하고,
명령의 하위 명령 앞에 `--eval-config /path/to/eval.yaml`을 지정한다. 시험 문서에 실제 장수를 남긴다.

## 결과 읽기

| 파일 | 내용 |
| --- | --- |
| `manifest.json` | 이미지·라벨 파일 해시, 원본 상대경로·ID, 선택 방법, 정답 상자·품질 |
| `results/results.json` | 입력·가중치 해시, 전체 시험 조건, 이미지별 탐지 상자·대응, 합계 |
| `results/counts.csv` | 이미지별 TP / FP / FN과 품질 생략 여부 |
| `results/*-raw-conf*.jpg` | 원본에 바로 탐지한 상자와 정답 상자 |
| `results/*-pipeline-conf*.jpg` | 실제 품질 게이트·CLAHE 경로의 상자와 정답 상자 |

raw는 모델 적용성을 보기 위해 품질 게이트를 우회한 **시험 경로**다. pipeline은
#18처럼 q<0.3이면 탐지를 생략하고 0.3≤q<0.6이면 CLAHE를 적용한다. 원본과 개선본은
각각 최소 conf(기본 0.1)에서 한 번 추론하고, 같은 예측을 점수로 필터링해 0.3/0.1을 비교한다.
ByteTrack의 첫 프레임 ID 준비 상태가 탐지 평가에 끼어들지 않도록 이 시험은 정적 상자를 비교한다.

- IoU≥0.5인 정답·탐지 상자를 1:1로 연결한다. 대응 수가 최대가 되게 한 뒤 IoU 합을 높인다.
- TP는 연결된 탐지, FN은 연결되지 않은 정답, FP는 연결되지 않은 탐지다.
  중복 탐지가 같은 정답을 여러 번 사용하는 것을 허용하지 않는다.
- pipeline이 품질 때문에 생략한 이미지는 `quality_skipped=true`이며 정답은 FN으로 센다.
  raw와 비교해 모델의 누락과 품질 정책의 생략을 구분할 수 있다.
- precision=TP/(TP+FP), recall=TP/(TP+FN). 분모가 0이면 null로 남긴다.
  여러 이미지의 개체 수를 합산하며 이미지별 비율을 단순 평균하지 않는다.
- 녹색=대응된 정답, 자홍색=놓친 정답, 하늘색=대응된 탐지, 빨간색=추가 탐지다.

AI Hub 소개는 `iscrowd`를 고정값 1로 설명한다. 일반 COCO의 crowd 제외를 그대로 적용하면
정답 전체를 버릴 수 있어 이 도구는 **게시된 상자 하나를 개체 하나**로 보존하고 원본
iscrowd도 기록한다. 실행 후 오버레이에서 상자가 실제 개체에 맞는지 확인해야 한다.
라벨 누락·중복이 있으면 FP/FN을 확정하지 말고 원본 라벨과 육안 검토를 구분해 기록한다.

## 시험 범위와 시연 결정

작은 표본의 라벨 기준 누락·오탐 보고용이며, 합격 기준이나 질병 정확도를 만들지 않는다.
학습셋과의 중복 여부, 현장 조명·탁도·어종 일반화, 수심·활동량 보정은 별도 검증 사항이다.
conf=0.1이 누락을 줄여도 오탐이 늘 수 있으므로 양쪽 건수를 함께 기록한다.
정적 탐지가 가능하다는 결과만으로 실제 가두리 행동 지표를 대체하지 않는다.

영상·샘플·가중치는 Git에 넣지 않는다. 사용·재배포 조건 확인은 #48과 SBOM의 데이터 출처 항목을 따른다.

## 실행 결과 — 2026-11-27

사용자가 확보한 AI Hub Training `[원천]kr_part2.zip`(24,736,607,990 bytes),
`[라벨]kr_part2.json`을 사용했다. 이미지 35,693장 중 조피볼락 라벨이 있는 35,692장에서
L01/L03 각 60장, 총 120장 후보를 추출했다(라벨 없는 이미지 1장 제외).
후보의 품질 순위를 나눠 **20장, 정답 상자 59개**를 고정했다.
최종 표본은 L01 11장/L03 9장, q=0.1838~0.5356이다.

| 경로 | conf | 대응 TP | 라벨 기준 FN | 라벨 미대응 탐지(FP*) | 정답 대응 재현율 | 품질 생략 |
| --- | --- | --- | --- | --- | --- | --- |
| 원본 raw | 0.3 | 56 | 3 | 89 | 94.92% | 0장 |
| 원본 raw | 0.1 | 58 | 1 | 330 | 98.31% | 0장 |
| 품질 게이트+CLAHE | 0.3 | 28 | 31 | 53 | 47.46% | 8장 |
| 품질 게이트+CLAHE | 0.1 | 33 | 26 | 242 | 55.93% | 8장 |

**FP*는 실제 오탐 총수가 아니다.** 20장 오버레이를 확인한 결과, 화면 가장자리의 잘린
물고기·가려진 물고기가 원본 라벨에 포함되지 않은 사례가 여럿 있었다.
예: source_image_id=73158의 왼쪽 물고기, 24559의 전경 물고기, 36707의 주변 물고기.
이들을 잡은 탐지도 자동 집계에서는 FP*가 된다. 따라서 위 수치로 실제 precision이나
실제 오탐률을 확정하지 않는다. 36707의 raw/conf=0.3 prediction_index=11은 바닥의
선/배경을 잡은 실제 오탐 사례(conf=0.3183)로 확인했다. 낮은 conf에서는 같은 물고기의
머리·몸통·지느러미를 겹쳐 잡는 중복도 증가했다. 상자별 완전한 수작업 재라벨링은 하지 않았다.

raw/conf=0.3에서 놓친 정답의 source_image_id는 74567, 52266, 84465다.
conf=0.1은 74567·84465를 회복하지만 52266의 겹쳐 가려진 개체는 대응하지 못했다.
작은 표본의 **게시 라벨 기준 대응**이며 전체 물고기의 실제 검출률은 아니다.

### 시연 결정

**운영 conf=0.3을 유지하고, 해커톤의 카메라 행동 지표는 기존 시뮬레이션을 유지한다.**
conf=0.1은 정답 대응을 2개 늘리는 동안 미대응 탐지가 241개 늘었다. 중복 억제·라벨 범위
검토 없이 일괄 적용할 근거가 부족하다. 현재 M1의 가정값은 20장 중 8장(정답 상자 25개)을
생략했고 모든 이미지가 q<0.6이었다. 실제 영상으로 품질 점수를 보정하고, 완전한 검증 라벨과
연속 추적·설치 보정까지 확보한 뒤 행동 지표를 전환한다. 탐지기 자체의 조피볼락 적용 가능성과
실제 운영 준비 상태를 구분한다. 이 PR은 운영 설정이나 데모 데이터 경로를 바꾸지 않는다.

기계 판독용 집계·선택 이미지 ID/해시·육안 검토 메모는
[vc07-20261127.json](validation/vc07-20261127.json)에 있다.
원본 이미지·정답 좌표·전체 예측·오버레이는 로컬 `data/out/vc07/`에만 둔다.

- 원본 라벨 SHA-256: `a0e41369fecd56c398a6d968ea5f39a110a53999a1ae6fcef31a1dad5a2e5081`
- 가중치 SHA-256: `e3eac301b778e251c3826eae247cc783e1f2dab3fd71a18de92bf3e8999c6f10`
- 실행 환경: Python 3.13, macOS CPU. 20장×두 경로 추론·미리보기 생성 7.184초(모델 적재 포함).
  성능 벤치마크나 현장 속도 보증이 아니다.
