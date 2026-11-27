> #212 데이터·모델 소비 경로는 검증 snapshot을 사용하며 암묵적 기본값/last_good 폴백을 하지 않습니다.
> [현재 데이터·모델 후속 계약](data-model-followups.md)을 따릅니다. 아래 연계 제안은 초기 구현 시점 기록입니다.

# config YAML 로더 — #11

SDD 7장·NFR-MNT-01의 thresholds, cause_rules, release_rules, costs, first_aid를 읽는다.
설정값은 기존 `config/*.yaml`을 사용하며 로더 코드에 업무 임계값·단가 기본값을 복사하지 않는다.
이 PR은 최신 main에서 독립 구현했으며 미병합 API/DB/데이터 모듈 브랜치를 포함하지 않는다.

## 설치·사전 검증

Python 3.11 이상, 저장소 루트 기준:

```sh
python -m pip install -r requirements.txt
python -m common.config_loader
python -m unittest discover -s tests -v
```

CLI는 다섯 파일의 검증 성공 여부·SHA-256 revision·UTC 적재 시각·파일 이름만 출력한다.
설정 본문은 출력하지 않는다. 성공 종료코드는 0, 오류는 1이다.
다른 디렉터리를 검증하려면 `python -m common.config_loader --directory path/to/config`를 사용한다.
CLI는 일회성 검증 명령이며 다른 프로세스의 설정을 갱신하는 명령은 아니다.

## 애플리케이션에서 사용

```python
from common.config_loader import ConfigError, ConfigLoader

loader = ConfigLoader()  # 기본 경로는 이 저장소의 config/, 현재 작업 폴더와 무관

def process_cycle():
    # 업무 한 주기에서 한 번 조회하고 같은 스냅샷을 모든 계산에 전달한다.
    snapshot = loader.snapshot()
    thresholds = snapshot["thresholds"]
    rules = snapshot["cause_rules"]["rules"]
    release_hours = thresholds["release_hours"]
    # 지수·원인·손익 계산의 근거에 snapshot.revision을 함께 기록할 수 있다.
    return snapshot.revision, release_hours

first = loader.snapshot()
# 프로세스를 재시작하지 않고 YAML을 저장한 뒤:
second = loader.snapshot()  # 다음 조회에서 내용 변경 감지·검증 후 새 스냅샷 반영
manual = loader.reload()   # 내용이 같아도 강제 재검증
costs = loader.get("costs")  # 단일 문서 편의 조회. 이 호출도 묶음 전체를 확인한다.
```

- 백그라운드 감시 스레드·HTTP 재로드 엔드포인트는 없다. `snapshot()`/`get()` 호출 시 변경을 확인한다.
- 임계값을 이미 변수나 모델 인스턴스에 복사한 소비자는 새 snapshot을 다시 전달해야 한다.
  파일만 바꾼다고 학습된 모델이나 진행 중인 경보 상태가 자동 초기화되지는 않는다.
- dict는 읽기 전용 mapping, list는 tuple로 반환한다. 기존 dict/list 기반 소비자는
  `snapshot.to_dict()`로 독립된 수정 가능 사본을 얻는다. 사본을 바꿔도 로더에는 영향이 없다.
- `get()`을 여러 번 호출하는 사이 설정이 바뀔 수 있으므로 한 업무 처리에는 snapshot 하나를 사용한다.

## 변경 반영·오류 정책

5개 파일의 이름과 전체 바이트 내용으로 revision을 계산한다. mtime이나 파일 크기만 비교하지 않는다.
같은 내용이면 기존 snapshot 객체를 반환한다. 주석·줄바꿈만 바뀌어도 revision은 바뀐다.
변경 시 다섯 파일을 모두 파싱·검증하고 파일 내용을 한 번 더 확인한 뒤 잠금 안에서 snapshot을 교체한다.
기존 독자는 이전의 완전한 snapshot을 계속 사용할 수 있다.
읽기 도중 파일 내용이 바뀌면 새 snapshot을 적용하지 않고 재시도를 요구한다.

초기 로드 실패는 정상 snapshot을 만들지 않는다. 이후 reload 실패도 이전 snapshot을 바꾸지 않지만,
기본 `snapshot()`/`reload()` 호출은 **ConfigError를 발생**시킨다. 오류를 숨기거나 일부 값만 적용하지 않는다.
서비스가 이전 설정으로 계속 동작할지는 호출자가 명시적으로 결정한다:

```python
try:
    current = loader.snapshot()
except ConfigError:
    current = loader.last_good
    error = loader.last_error
    if current is None:
        raise
    # 이전 설정 사용 여부를 업무 정책으로 결정하고 오류·revision을 운영 로그에 기록한다.
```

파일을 고친 뒤 다음 조회가 성공하면 last_error가 해제된다.
여러 파일을 사람이 순서대로 저장하는 작업 전체를 하나의 파일시스템 트랜잭션으로 만들지는 않는다.
동시에 배포해야 하는 설정은 배포 절차나 향후 manifest/revision 정책을 별도로 정해야 한다.
여러 worker·서버 사이의 설정 배포/동기화는 포함하지 않는다.

## 검증 범위

- SafeLoader 기반: UTF-8/BOM, 비어 있지 않은 mapping, 문자열 key만 허용
- 중복 key, alias/merge key, 임의 Python 태그·비유한 숫자 거부. 파일당 1 MiB 제한
- 모든 파일과 필수 필드 요구. 숫자 문자열·불리언을 숫자로 자동 보정하지 않음
- 경보 단계 순서·유지 시간·품질/융합 구간·기준선 시각 형식 검사
- R1~R8과 공통 rule 필드, 크기 그룹 fry/mid/market, 비용·손실률·선택지 key 일치 검사
- 1차 조치 ID 중복 및 차단 정규식의 문법 검사
- 파일의 최상위·고정 구조는 알 수 없는 key를 거부한다.
  아직 발전 중인 rule의 behavior/env와 추가 rule 필드는 JSON 호환 값으로 보존한다(#25·#60).

검증은 설정 형식과 기본 일관성을 확인한다. 규칙 엔진의 전체 실행 가능성, 대응 action ID의 전역 매핑,
법적 요건·가격·의학적 근거·문구 출처의 적절성을 확정하지 않는다.
현재 YAML의 `pending` 출처·0원 미반영 값·설계 가정값은 그대로 유지한다.

## 모듈 연계와 합의 사항

1. **데이터·모델 + 백엔드:** #77의 fusion/stress 개별 로더를 언제 공통 snapshot으로 연결할지.
   #77의 “파일/키 없으면 기존 기본값”과 이 로더의 “필수 5개 전체 검증”은 서로 다른 정책이다.
   기존 데이터 모듈의 fallback 정책을 이 PR에서 바꾸지는 않는다.
2. **데이터·모델 + 백엔드·기획:** 주기 시작/시나리오 시작/요청 시작 중 반영 시점,
   기준선 모델·진행 중 경보의 유지 시간에 새 설정을 적용하는 방법.
3. **백엔드·운영:** 오류 시 요청 거부 또는 last_good 사용, 알림 방식,
   다중 worker 배포·rollback 및 재현용 revision 저장 위치.
4. **문서·기획 + 백엔드·데이터:** 정식 설정 스키마/버전과 확장 key 관리.
   R2의 cohesion_drop/lookback_h 등 #60 결정은 아직 YAML 값을 바꾸지 않았다.
5. **문서·기획 + 백엔드:** `stop_feeding`/`feed_stop`, `emergency_release`/`release` 등
   cause_rules와 costs의 action ID를 연결할 표준 이름/매핑은 #25·#27과 합의한다.
6. **백엔드 + 프론트·운영:** 관리자 설정 수정·재로드 API, 인증·권한·감사 이력은 #36 범위다.
   이번에는 웹에서 설정을 수정하는 기능을 추가하지 않는다.

합의 내용은 별도 검토 이슈로 관리한다. #11은 실제 소비 모듈의 전환 완료를 의미하지 않는다.
# 관리자 수정 연계 (#36)

보호된 관리자 GET/PUT·동시 수정·주석 보존·감사/복구는 [관리자 API 계약](admin-config-api.md)을 따릅니다.
기본 ConfigLoader와 기존 reader는 `ROCKFISH_CONFIG_DIR`를 공유하며, 명시적 directory 인수가 있으면 우선합니다.
모델 주기·기준선은 import 시 고정되므로 해당 수정은 저장 후 모델/프로세스 재시작이 필요합니다.
