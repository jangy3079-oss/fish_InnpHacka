# 시나리오 재생 API — #30

`POST /demo/scenario/{name}?t=144`는 합성 센서 → fusion → 학습된 M5 → 원인 규칙 → 단계 → 손익/공통 조치를 한 번에 실행한다.
고정 status fixture·정답 라벨에서 원인/추천을 채우지 않는다. 운영 DB·계정·알림·외부 기관을 호출하지 않고 파일도 쓰지 않는다.
기존 CLI `python -m pipeline`의 파일 내보내기와 `Timeline`은 그대로 유지한다. API는 사전 생성 파일 없이 동작한다.

## 실행

```sh
python -m pip install -r requirements.txt -r requirements-api-dev.txt
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
curl -X POST 'http://127.0.0.1:8000/demo/scenario/heatwave_typhoon?t=144'
```

합성 시연 전용이라 Bearer/Supabase 설정이 필요 없다. 실제 사용자 데이터 경로의 인증은 그대로다.
첫 요청은 정상 14일의 독립 기준선 학습과 시나리오 합성 때문에 수십 초 걸릴 수 있다.
운영 공개 전 비용/동시 요청 제한과 접근 정책은 배포 담당자가 결정해야 한다. 서버는 한 프로세스 안에서 준비·계산을 직렬화한다.
프로세스마다 모델 1개·시나리오 최대5개·회차 응답 최대32개만 메모리에 보관한다. worker별로 별도 준비된다.

## 요청 시간

| name | 전체 길이(시간) | 확인 가능한 예 |
| --- | ---: | --- |
| heatwave_typhoon | 192 | 초기 R7 → t=108 R1 → t=144 R2 |
| turbidity_event | 72 | t=30 탁도에 따른 영상 가중치0, 소나 분석 유지 |
| disease_suspect | 144 | t=144 R5 공통 조치 |
| parasite_suspect | 96 | t=38.5 R8 공통 조치 |
| feeding_drop | 72 | t=48 R7(마지막 급이 관측 유지), 경보 단계 정상 |

`t`는 **시작 후 경과 시간(hours)**이다. 프론트의 프레임 인덱스가 아니다.
유한한0 이상 전체 길이 이하만 허용하며, 범위 밖은422다. 임의 경로/다른 시나리오명은 허용하지 않는다.
현재 설정의5분 주기 끝까지 내림하여 분석한다(`effective_t`). 예: t=1.04 → effective_t=1.
점수/원인 엔진에는 해당 시각까지 완료된 주기만 전달한다. 시간당 그래프 평균도 같은 접두 구간으로 다시 계산하므로 미래 값이 섞이지 않는다.
합성 준비 과정에서 전체 센서를 만들 수 있지만 미래 주기는 분석·응답에 전달하지 않는다.
t=0은 관측 없음·점수/단계 null·빈 시계열이다. 학습 기준선은 준비돼 있어도 초기1시간 관측이 덜 차면 점수는 null이다.
기존 `Timeline.status_at`의 범위 맞춤/첫 미래 주기 반환과 다르다.

## 응답

성공200, `X-Data-Mode: simulation`, `Cache-Control: no-store`.

```json
{
  "mock": false,
  "simulated": true,
  "persisted": false,
  "scenario": "heatwave_typhoon",
  "t": 144,
  "t_unit": "hours",
  "effective_t": 144,
  "duration_h": 192,
  "status": {"cage_id": 3, "ts": "2026-08-10T15:00:00Z", "cause": []},
  "recommendation": null,
  "recommendation_reason": null,
  "timeseries": {},
  "config_revision": "설정 묶음의 SHA256",
  "scenario_revision": "시나리오·정상 기준선·합성 재고의 SHA256",
  "producer_id": "cage-003",
  "cause_source": "cause_engine",
  "stock_source": "synthetic_demo_config"
}
```

위 예시는 외곽 구조만 설명하며 status는 축약했다. 실제 t=144에는 R2와 비교 결과가 들어간다. 전체 필드는 OpenAPI의 DemoResult를 따른다.
status는 #104의 CageStatus와 같은 타입이며 수치 근거·영상 가중치·누락·관측 신뢰도·모델 버전을 포함한다.
status.ts는 effective_t의 종료 시각이며 recommendation.ts도 같다. analysis_id/valid_until은 DB 회차가 아니므로 null이다.
timeseries는 지표별 `{ts,value}` 배열이다. stress_score와 vertical_dist도 포함하고 관측이 없는 지표는 빈 배열이다.

추천이 없는 정상 시점은 recommendation=null, recommendation_reason=no_current_cause다. R1/R2 등은 기존 계산기의9개 선택지를 반환한다.
R5/R8은 first_aid_required이며 금전적 추천 승자/손실은 null, 진단 안내는 dispatch_status=not_created다.
**id=0/selectable=false는 합성 표시 전용**이다. `/recommendations/{id}/choice`나 진단 저장 API에 보내지 않는다.
이 API 호출만으로 선택 저장·조치 실행·접수·알림이 이루어졌다고 표시하면 안 된다.

## 設定と分離

config/demo.yaml에는 합성 재고·표시용 DB ID/품종 가정값만 있다. 현재 중간어1000마리×0.3kg, 미보험이다.
표시3과 생산자cage-003의 대응은 시나리오 전용이며 실제 DB의 같은 ID를 읽지 않는다.
가격/폐사율/임계값은 기존 ConfigLoader의 검증된 설정을 사용하며, 미확인 해역 방류 할당/지원금/보험은 보완하지 않는다.
한 요청은 같은 설정 snapshot을 사용한다. 설정 revision·시나리오/기준선/합성 재고 해시 변경 시 캐시를 무효화한다.
M5 프로세스의 cycle/window와 설정이 다르면503이다. 그 외 모델 학습 설정 변경은 서버 재시작을 동반한다.
입력 설정 누락/손상이나 출력 검증 실패는503(replay_unavailable)이다. 내부 경로·입력 내용은 오류에 노출하지 않는다.

## 担当境界・合意

- #34는 다른 팀원 담당인 알림 생성/저장 연계이며 이번 재생 API는 notify를 실행/변경하지 않는다.
- #35는 다른 팀원 담당인 지자체 집계 API이며 이번에 /areas를 변경하지 않는다.
- #45는 다른 팀원 담당인 R4/R6이며 새 규칙을 추가하지 않고 main의 CauseEngine을 사용한다.
- 프론트: 슬라이더 시간 환산, 합성 표시, null/추천 없음, 합성 ID 저장 금지, 초기 대기와503 재시도 처리를 합의한다.
- 데이터/모델·백엔드·문서: 시나리오/기준선 대응, 합성 재고, 미확인 방류 조건, 대표 시점을 합의한다.
- 운영: 첫 요청 대기·미리 준비, 공개 범위·부하 제한, 프로세스 수, 설정 변경 시 재시작을 결정한다.

프론트의 기존 파일 재생은 유지하며 HTTP 호출로 화면을 전환하는 작업은 이 PR에 포함하지 않는다.
