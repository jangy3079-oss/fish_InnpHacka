# MetricRecord 공통 계약 — #2

상태: 구현 초안. SDD 4.1에 명시된 부분과 아래 팀 합의 항목을 구분한다.
데이터 담당과 동일한 예시 JSON으로 검증하고 합의한 뒤 #2를 완료 처리한다.

## 문서에 명시된 계약

근거: 제공된 SDD 4.1·4.2·5.2·7장, SRS NFR-REL-01·03 및 5.2.

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| cage_id | 비어 있지 않은 문자열 | 가두리 ID. 실제 DB ID 형식은 #3에서 합의 |
| ts | 시간대가 있는 ISO 8601 문자열 | 측정 시각. 입력 오프셋을 UTC로 변환하여 출력 |
| source | sonar / camera / env | 처리한 센서 종류. simulator는 별도 source가 아님 |
| metric | 아래 10종 | 지표 이름 |
| value | 숫자 또는 숫자 배열 | vertical_dist만 수심 구간 비율 배열, 나머지는 숫자 |
| confidence | 숫자, 0~1 | camera: 영상 품질, sonar: 신호 품질 |

지표: `vertical_dist`, `activity`, `cohesion`, `feeding`, `still_ratio`,
`rubbing_rate`, `quality`, `temp_surface`, `temp_mid`, `do`.

수온 단위는 ℃, DO 단위는 mg/L이다(SRS 5.2). `quality`, `still_ratio`는
0~1이다. 수직 분포 원소는 0~1, 합은 1(절대 오차 1e-6 허용)로 검증한다.
NaN·무한대·불리언·문자열 숫자·null·알 수 없는 필드는 거부한다.
모델은 생성/검증 시 계약을 검사하므로, 수정한 데이터도 `model_validate`로 다시 검증한다.

## 데이터 담당과 합의할 사항

| 항목 | 현재 구현/제안 | 남은 결정 |
| --- | --- | --- |
| 수직 분포 | 예시는 `[저층, 중층, 표층]`. 임의 길이 배열 허용 | MVP 3구간 순서, 실제 수심 경계. 1m 구간 사용 시 경계 메타데이터 전달 방식 |
| 수치 정의 | activity·cohesion·feeding·rubbing_rate는 유한 숫자만 검증 | 활동량 스케일, 응집도 분산 단위·0분산 처리, 급이 정규화, 비비기 시간 단위(초/분/시간) |
| 저품질 카메라 | `quality` 레코드로 품질/confidence를 보내고 관측하지 못한 지표는 생략하는 안 | SDD 4.2의 “confidence만 출력”과 필수 metric/value를 함께 만족하는 표현 확정. 현재 null은 거부 |
| 센서 누락 | 가짜 0값 레코드를 생성하지 않음 | fusion이 누락을 판단할 주기·타임아웃. 실제 0 관측과 누락은 구별 |
| source별 지표 | source와 metric 조합을 추가 제한하지 않음 | 카메라 activity/vertical_dist 허용 범위, env confidence 계산 방식. 예시의 env=1은 실측 보증이 아님 |
| 시간·식별 | 측정 시각 UTC, 임의의 비공백 문자열 ID | SDD 기본 5분 주기의 집계 시각(시작/끝), 지연·중복 전송 정책, DB 가두리 ID |
| 문서 간 지표 차이 | #2·SDD 4.1의 10종만 구현 | SDD 모듈 표의 count/speed/depth_mean은 목록에 없음. 추후 추가 여부 |

수직 비율 합 검증 허용 오차와 다른 지표에서 배열을 거부하는 정책도 이 초안의
구현 선택이다. 합의 없이 feeding을 0~1로 제한하거나 물리적 임계값을 임의로 추가하지 않는다.
이 파일은 데이터 형식만 정의하며 품질 게이트·누락 판정·센서 융합은 #22 범위다.

## 공동 검증

저장소 루트에서 Python 3.11 이상 사용:

```sh
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

데이터 생산자와 백엔드는 같은 파일 `scenarios/metric_records.example.json`을 사용한다.
예시 수치는 형식 검증용이며 관측값이나 알고리즘 기준값이 아니다.
JSON 최상위 배열은 공유 테스트용이며 #10의 HTTP 요청 envelope 확정은 별도다.

```python
from pathlib import Path
from pydantic import TypeAdapter
from common import MetricRecord

adapter = TypeAdapter(list[MetricRecord])
records = adapter.validate_json(
    Path("scenarios/metric_records.example.json").read_text(encoding="utf-8")
)
payload = adapter.dump_json(records)
schema = MetricRecord.model_json_schema()
```

JSON Schema는 필드 구조를 제공한다. metric별 value 형식·비율 합의 교차 검증은
Pydantic 모델 검증이 수행하므로 JSON Schema 검사만으로 대체하지 않는다.
