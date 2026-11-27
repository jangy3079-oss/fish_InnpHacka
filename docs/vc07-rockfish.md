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
python -m vision.evaluate prepare --labels /path/to/labels --images-root /path/to/images --category 조피볼락 --source-url 'https://www.aihub.or.kr/aihubdata/data/view.do?dataSetSn=155' --out data/out/vc07/manifest.json
python -m vision.evaluate run --manifest data/out/vc07/manifest.json --images-root /path/to/images --weights models/cfd-rf-detr-nano-640.pth --out data/out/vc07/results
```

`--category`는 다운로드한 JSON의 **실제 categories.name**과 정확히 같아야 한다.
이름이 다르면 출처 설명을 확인한 뒤 지정한다. 일부 문자열이나 파일 이름만 보고
조피볼락으로 분류하지 않는다. 비교할 여러 조피볼락 범주가 있으면 옵션을 반복한다.

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
