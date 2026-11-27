# 데이터·모델 후속 계약 구현 — #212

#198/#199/#201/#205/#207의 데이터 담당 구현이다. API/RPC·프론트·실계정·현장 자료와 분리하여
데이터 산출물/모델/업로더/로컬 검증 도구를 제공한다. 기존 RF-DETR 확장 학습 #49는 이 후속 범위 밖이다.

## 설정과 모델 호환

`common.config` 호환 reader도 다섯 파일 전체를 검증한다. 파일/키 오류에 코드 기본값이나
last_good을 자동 적용하지 않는다. `fusion.fuse(snapshot=...)`, `StressModel.fit/score_series/load(snapshot=...)`
와 pipeline·stress CLI는 동일한 고정 snapshot을 전달한다. 원시 관측 업로더는 모델 설정에 의존하지 않는다. 모델 프로세스 설정은 import 시점이 아닌 첫 데이터 처리 때 초기화하여, 잘못된 분석 설정이 raw 수집/health API 시작을 막지 않게 한다.

모델에는 training_settings(schema/알고리즘/주기/윈도/낮밤/융합/품질/특징 목록)와 training_revision을 저장한다.
융합 산출물의 fusion_signature도 확인하여 다른 융합 가중치로 만든 특징을 그대로 학습/채점하지 않는다.
model_version은 학습 특징·시각·seed/추정기 수와 학습 설정을 구분하는 해시를 포함한다.
비용·경보 규칙 변경만으로 재학습을 요구하지 않으며 당시 training_revision은 그대로 보존한다.

- cycle_minutes, window_hours, day_start/day_end 변경: 새 설정으로 재학습하고 프로세스를 재시작한다.
- min_windows만 변경: 프로세스 재시작. 기존 모델의 학습 특징이 같으면 재학습은 필요 없다.
- 융합·품질 설정 변경: 기존 모델과 불일치하면 거부한다. 특징 분포 변경을 검토하고 새 기준선으로 학습한다.
- 구 metadata 없는 모델은 새 프로세스에서도 거부한다. 신뢰된 관측으로 다시 학습한다.

pickle은 실행 가능한 형식이다. 이 프로그램이 만든 **신뢰된 모델만** 읽는다. metadata 검사는
임의 업로드 pickle을 안전하게 만드는 기능이 아니다. 모델 저장은 임시 파일/fsync/원자 교체를 사용한다.

```sh
python -m common.config_loader
python -m pipeline --out data/out --model models/cage-003.pkl
# 실제 신뢰된 정상 관측을 가진 환경에서는:
python -m stress train data/out/normal_14d/metrics.jsonl --model models/cage-003.pkl
```

단계/원인 snapshot은 새 회차로 게시하고 기존 기록을 덮어쓰지 않는다. DB 분석의 입력 창 재계산과
설정 revision 변경 시 현재 원인 이력 초기화를 유지한다. 학습 metadata가 없었던 .pkl을 그대로 배포하면 실패한다.

## 관측과 원인 근거 산출물

R8 `max_pause_hours: 16`은 #127/#201에서 채택한 **MVP 가정**이다. 6시간은 관측한 이상 시간만 더한다.
연속 야간/저품질 16시간은 허용하고 초과하면 누적을 지운다. 돌아온 관측 구간 자체를 대기 시간에 더하지 않는다.
관측된 정상·환경/특보 미확인·stream 공백·설정 변경의 reset은 유지한다. R7의 별도 24시간 유지 정책은 보존한다.

`CauseResult.pending`는 rule_id/reason/progress를 제공한다. R5 progress는 실제 유효 관측 시간,
R8 progress는 실제 이상 관측 누적 시간이다. 특보 unknown에는 진행률을 꾸며 넣지 않는다.
R7 유지/마지막 확인/해제 사유도 후보가 사라진 시점의 metadata에 남는다. UI용 내보내기의 cause_pending은 원인이 비어 있는 경보에만 제공하고 정상/R7 관리·이미 원인이 표시된 프레임에는 빈 목록을 보내 불필요한 질병 대기 안내를 막는다.

오프라인 status_timeline/프론트 내보내기는 다음을 보존한다:

- cause[].display/cause_source, cause_pending 및 원인 revision
- data_status, baseline_windows, level_held_due_to_missing
- r7_held_due_to_missing, feeding_last_observed_at, feeding_reset_reason
- 원시 MetricRecord에서 구한 센서별 last_seen와 last_observation_at. 원시 입력이 없으면 null
- model_version, meta의 config_revision/scenario_revision/training_revision

분석 시각이나 bucket 시각을 실제 관측 시각으로 대신 쓰지 않는다. 서로 다른 센서의 마지막 시각을 하나로 합치지 않는다.
기존 API에서 허용하던 R7/단계 유지 필드는 기존 스키마대로 전달한다. 새 cause/source/pending/last_seen 필드를
실제 DB/API에 노출하는 작업과 UI 표시는 #201/#204의 백엔드·프론트 연계에 남아 있다.
실제 API/DB 평가에는 정답 라벨 fallback을 쓰지 않는다. 파일 시연 fallback에는 scenario_label 표시를 유지한다.

```sh
python -m pipeline.frontend --no-label-causes --dest data/out/frontend/engine-only.json
# 시연 파일에는 기존 기본값의 label fallback을 허용하되 엔진 판단과 구별한다.
python -m pipeline.frontend --dest data/out/frontend/scenarios.json
```

이 작업은 프론트가 이미 갱신한 mocks/scenarios.json을 덮어쓰지 않는다. 새 데이터 파일의 연결은 프론트 담당이 한다.

## 수집 업로더

`scripts.upload_metrics.upload_report`는 confirmed_count/inserted_count/duplicate_count를 구분한다.
새 응답은 received=inserted+duplicate를 엄격한 정수로 검사한다. 구 응답은 duplicate_count가 없고
inserted=received일 때만 허용한다. 숫자 문자열/불리언·음수·불일치 영수증은 성공으로 처리하지 않는다.
기존 `upload()`의 int 반환은 확인된 건수이며 CLI는 received_count/inserted_count/duplicate_count를 출력한다. upload_report는 동일 canonical upload_counts 구현을 감싼 typed 데이터 클라이언트 뷰다.

서버의 멱등 RPC/migration과 canonical 업로더는 PR #219로 main에 병합되었다. 해당 구현/CLI 계약을 보존하며 이 PR은 typed report 호환 함수를 더한다. 원격 migration 적용을 확인한 것은 아니며 자동 재시도는 켜지 않는다. 구 서버의 중복409와 네트워크 결과 불명확 시 중단도 유지한다.
소나가 필요한 입력은 `metrics.jsonl`이며 `records.jsonl`만으로 소나 관측을 대체하지 않는다.

## 실행 중 API 예열·재현 기록

```sh
python -m scripts.prewarm_replay --base-url http://127.0.0.1:8000 \
  --point heatwave_typhoon:0 --point heatwave_typhoon:15 --point heatwave_typhoon:26 \
  --point feeding_drop:48 --output data/out/rehearsal/warmup.json
```

이 도구는 **실행 중인 서버**의 합성 POST만 호출한다. 별도 프로세스의 ReplayEngine을 만들고 종료하는 것으로
API 캐시가 예열되었다고 하지 않는다. 요청별 시간·scenario/config revision·model_version을 기록하고
중간 설정 변경/실패/실제 persisted 응답에는 실패한다. 자동 재시도/DB 쓰기/실제 선택·진단 접수는 없다.
서버 재시작·다른 worker에는 캐시가 공유되지 않는다. t=0으로 모델은 준비해도 뒤 시점 계산까지 끝난 것은 아니다.
대본 대표 시점 예열 후 응답시간을 실제 환경에서 확인하며 1초 이내 같은 보장을 하지 않는다.

## 검증·담당 경계

요청대로 구현·문서·테스트 작성 전체를 끝낸 뒤 한꺼번에 테스트를 실행한다. 모델 설정/구 모델 거부,
원인 시간 경계, snapshot 실패/변경, 센서 실제 시각·내보내기, 업로더 두 응답 형식과 예열 도구를 검증한다.
합성 VC 결과와 실제 계정/현장 효과 검증은 구분한다. 원격 DB 적용·실제 해역/기관/지원·보험 조건은 확인하지 않았다.
테스트 결과와 PR은 #212 및 부모 이슈에 기록하며 다른 파트의 미완료 작업은 닫지 않는다.

## 이번 실행 결과 (2026-11-28 KST)

기준 main은 `2da6f20`이며 PR #209의 R8 pause 구현·경계 테스트를 병합하여 보존했다.
최종 데이터·모델 코드의 검증 결과는 [기계 판독 결과](validation/data-model-followups-20261128.json)에 기록했다.

| 확인 | 결과 |
| --- | --- |
| Python 전체 unittest | 405개 통과 |
| 실제 PostgreSQL migration/RLS 검증(PGlite) | 252개 통과 |
| 데이터 생성·모델 학습·채점·내보내기 | 8개 시나리오, 각48프레임(총384), R7 유지20프레임 |
| 별도 프로세스 모델 로드/채점 | 고수온2304회차 성공 |
| VC-01/02/04/05, 10개 시드 | 해당 요약 판정 통과 |
| VC-03 주요 시나리오 | 고수온/탁도/질병10/10, 기생충9/10 |
| R4/R6 별도 VC-03 | 각각10/10 |
| R7 별도 VC-03 | 주 구간10/10, 모든 구간5/10; 주 구간 지표를 전체 구간으로 대체하지 않음 |
| 실행 중 로컬 API 예열 도구 | 첫 모델 준비2.7189초, 같은 시점 재요청0.0038초 등 요청별 실측 기록 |

API 예열은 격리된 로컬 서버로 수행하고 종료했다. 운영 서버 성능·필드 효과 검증을 뜻하지 않는다.
새 코드에서 학습한 모델만 사용했으며 기존 사용자 모델/프론트 목업·원격 DB는 덮어쓰지 않았다.

PR #219의 canonical 수집/업로더 계약과 #222의 보류 안내 컴포넌트를 병합한 뒤 전체405개·PostgreSQL252개를 확인했다. 수치 검증은 기존 main의 R8 검증 기록과 요약이 동일하며, 정상/엔진이 이름 붙인 원인에는 보류 안내를 내보내지 않는다. 파일 시연의 임시 라벨은 엔진 판단과 구분한다.
