# 근거 기반 알림 문장 생성 — #34

`notify`는 현재 상태·근거와 기존 `LossCalculator.compare()` 결과를 받아 어민용
문장을 최대 3개 생성한다. #104와 분리된 모듈이며 API 라우트·추천 저장 구조를 바꾸지 않는다.

| 범위 | 상태 |
| --- | --- |
| 근거 수치·손익 비교 요약, 최대 3문장 | 구현 |
| 공식 관리요령 구절 검색·출처/본문 검증 | 구현 |
| 선택적 LLM HTTP 호출, 실패·부적합 출력 시 템플릿 | 구현, 실제 유료 API 호출은 미실행 |
| 기존 금칙어/단위 검사 재사용 | 구현 |
| 수신자·가두리·같은 단계별 6시간 중복 판정 | 순수 함수 구현 |
| `alert` 테이블 저장·DB 기준 원자적 중복 억제·조회 API·실제 발송 | #104 이후 후속 연결 |

마지막 저장·API 연결이 남아 있어 #34 전체를 종료하지 않는다.

## 실행

저장소 루트, Python 3.11 이상. 기존 API 의존성의 httpx를 재사용하며 새 패키지는 없다.

```sh
python -m pip install -r requirements.txt -r requirements-api-dev.txt
python -m unittest discover -s tests -p test_notify.py -v
```

`context.json` 예시:

```json
{
  "cage_id": 3,
  "ts": "2026-11-27T13:00:00Z",
  "baseline_status": "ready",
  "level": "경고",
  "stress_score": 64,
  "rule_id": "R1",
  "evidence": {"temp_surface": 26.4, "activity_change": -0.28}
}
```

```sh
python -m notify context.json
# 기존 손익 계산기의 JSON 결과가 있다면:
python -m notify context.json --recommendation comparison.json
```

기본 실행은 템플릿만 사용한다. `--llm`을 지정한 경우에만 환경변수
`NOTIFY_LLM_URL`, `NOTIFY_LLM_MODEL`, `NOTIFY_LLM_API_KEY`로 설정한
Chat Completions 호환 서버를 호출한다. 키를 JSON·명령줄 인자에 넣지 않는다.
모델은 배포 환경에서 명시적으로 선택한다. 어댑터의 요청·응답 형식은
[공식 Chat Completions 문서](https://developers.openai.com/api/reference/resources/chat)를 기준으로 했다.
SDK 없이 httpx를 사용하고 재시도·리다이렉트는 하지 않는다.

## 애플리케이션에서 연결

```python
from decision.calculator import LossCalculator
from notify import NotificationGenerator

comparison = LossCalculator().compare({
    "cage_id": "3", "farm_id": "1", "sea_area_id": "1",
    "species": "조피볼락", "level": "warning", "primary_rule_id": "R1",
    "stock": [{"size_group": "mid", "count": 1000}],
    "other_area_release_count": 0,
})
context = {
    "cage_id": 3, "ts": "2026-11-27T13:00:00Z",
    "baseline_status": "ready", "level": "경고", "stress_score": 64,
    "rule_id": "R1", "evidence": {"temp_surface": 26.4, "activity_change": -0.28},
}
result = NotificationGenerator().generate(context, recommendation=comparison)
print(result.message)
```

현재 설정의 예시 출력:

> 가두리 3번은 '경고' 단계이며 의심 원인은 고수온입니다. 근거는 스트레스 지수 64점, 표층 수온 26.4℃, 활동량 평소보다 28% 감소입니다; 손해가 가장 적은 방법은 사료 중단이며 예상 손해는 약 405,000원입니다(추정). 수과원 2018년 고수온 관리요령: 수온·용존산소 모니터링 등 자기 어장 점검.

R7(섭식 저하)은 손해 최소가 아니라 급이 관리 권고를 씁니다 (#232). 분석 추천(`pipeline/analysis.py`의 `recommendation_basis=R7_feeding_management`)과 같은 규칙이며, 감량률 수치(`feed_reduction_pct`)는 알림에 쓰지 않습니다.

> 가두리 3번은 '정상' 단계이며 의심 원인은 섭식 저하입니다. 근거는 스트레스 지수 38점, 표층 수온 23.4℃, 먹이 반응 평소보다 18% 감소입니다; 평소 관리로 사료 양을 줄이세요. …

쉬운 표현 원칙 (#232, NFR-USE-01): "기준선 대비"는 "평소보다", "최소 손실 선택지"는 "손해가 가장 적은 방법", 손익 금액은 "약 …원(추정)"으로 쓴다.

이 손익 수치는 설정의 가정값을 포함한 비교 결과이며 실제 효과·실행 가능성을 확정하지 않는다.
권한·최신 관측·추천 저장 여부는 호출자가 검증한다. `NotificationContext`는 알림 내부 입력이며
공통 MetricRecord·기존 API 응답 스키마를 확장하지 않는다.

추천을 전달하면 가두리 문자열 ID(`"3"`)·원인·단계·공통 설정 revision이 일치해야 한다.
생산자 ID(`cage-003`)를 숫자로 추정하지 않는다. #104가 기존 명시적 매핑을 거쳐 손익 입력을
구성해야 한다. 선택지는 기존 `decision.actions.ACTIONS`에 있어야 하며 available=true,
정수 KRW 예상 손실이 있어야 한다. 전달된 임의 label은 출력하지 않는다.
R5/R8의 `first_aid_required` 결과에는 손익 순위를 붙이지 않는다.

## 검색·LLM·대체 정책

`config/notify.yaml`은 이 모듈에서 검증한다. 공통 로더의 기존 5개 파일과 스키마를 바꾸지 않는다.
한 알림에서 공통 snapshot 하나를 사용하고, 생성 정책은 별도 SHA-256으로 기록한다.

1. 규칙에 맞는 검토 구절을 `notify/docs/`에서 직접 검색한다. 경로·출처 URL·본문 안의 구절을 확인한다.
   R1/R2/R7은 2018년 고수온 관리요령의 모니터링 구절, R4는 적조속보 확인 구절을 사용한다.
2. R5/R8은 기존 `decision.first_aid.reviewed_guide()`로 official 조치만 얻는다.
   pending/partial/secondary 조치나 진단 접수 메시지는 생성 후보에 넣지 않는다.
3. R3/R6처럼 확정한 구절이 없거나 출처 파일이 바뀌어 검증에 실패하면 상태·수치 2문장만 낸다.
   공식 대응을 지어내지 않는다. 근거의 None은 생략하고 실제 0은 표시한다.
4. 상태·수치·출처 범위를 고정한 문장 후보를 만든다. LLM은 이 후보 중 하나를 선택한다.
   응답은 `{"message": "후보 원문", "source_id": "선택한 출처 ID"}`여야 한다.
5. 후보와 정확히 일치하고 기존 `ensure_safe()` 검사를 통과한 출력만 채택한다.
   숫자 변경, 새 조치·출처, 진단 확정·전달 주장, 추가 필드·중복 JSON 키 등은 템플릿으로 대체한다.
   자유로운 의료·현장 조치 재작성은 허용하지 않는다.
6. 타임아웃·HTTP 오류·중단된 완료·잘못된 JSON·금칙어도 템플릿으로 대체한다.
   제공자 오류 내용·키·응답 원문을 결과나 오류에 노출하지 않는다.

`mode`는 template/llm, `fallback_reason`은 llm_disabled, no_reviewed_guidance,
guidance_unavailable, llm_error, invalid_response, blocked_output, ungrounded_response 중 하나다.
LLM 출력 채택 시 reason은 null이다. `citations`에는 실제 채택한 출처만 넣고
경로·URL(있을 때)·본문 해시·범위·구절을 남긴다. `dispatch_status`는 항상 not_dispatched다.

금칙어 사전은 기존 `first_aid.yaml`을 따른다. 그 사전 자체가 모든 약품·제품명을 포함하는 것은 아니다.
이 모듈은 금칙어 검사에 더해 검토된 문장 후보와의 완전 일치로 임의 조치가 추가되는 것을 막는다.
최신 설정 로드가 실패하면 예외를 발생시킨다. 이전 설정을 묵시적으로 사용하지 않는다.

## 중복 판정과 저장 연결

```python
from datetime import datetime, timezone
from notify.policy import should_notify

decision = should_notify(context, recipient_id, now=datetime.now(timezone.utc), history=committed_history)
if decision.allowed:
    pass  # 후속 DB 트랜잭션이 같은 키를 잠그고 재확인한 뒤 alert 저장
```

`history`는 실제 저장이 성공한 이력이다. 각 행은 recipient_id(UUID), cage_id, level,
accepted_at(시간대 있는 시각)이다. 키는 **수신자+가두리+단계**이며 가장 최근 이력을 사용한다.
6시간 경계는 포함한다. 정확히 6시간 후부터 다시 허용한다. 다른 단계·가두리·수신자는
별개이며 warming_up은 경보 대상으로 선택하지 않는다. 시간 역행은 거부한다.
기간은 매번 공통 snapshot의 `thresholds.alert_dedup_hours`에서 읽는다.

이 함수는 저장하지 않고 다중 worker의 경쟁을 잠그지 않는다. 생성 성공만으로 이력을 추가하면
DB 실패 시 재시도가 막히므로, 저장 성공한 이력만 전달해야 한다. #104 후속 연결에서는
추천 소유권 확인·동일 키 잠금/재확인·alert 저장을 한 트랜잭션으로 구성해야 한다.
현재 alert 테이블에는 단계·가두리 중복 키 전용 필드가 없으므로 이 저장 계약을 먼저 확정한다.
`sent_at`이나 진단 접수 상태를 생성 모듈이 임의로 채우지 않는다.

## 검증

전용 테스트는 실제 손익 계산기 결과 연결, 3문장·출처 존재·official 선택, 관측 0/결측·변화 방향,
질병 문구·접수 상태, LLM 수치/지시 변조·금칙어·잘못된 JSON, HTTP 타임아웃/오류/중단,
6시간 경계·수신자/가두리/단계 분리·YAML 기간 변경을 확인한다. HTTP 제공자는 MockTransport로 대체한다.
실제 유료 LLM·원격 Supabase·메신저 발송은 실행하지 않았다.
