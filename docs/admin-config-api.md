# 관리자 규칙·임계값 API — #36

GET/PUT `/admin/config/{file}`을 실제 인증·검증·YAML 저장 경로로 연결한다.
file은 thresholds/cause_rules/release_rules/costs/first_aid만 허용한다. notify/demo 설정과 임의 경로는 열지 않는다.

## 권한과 실행

Supabase 등록 사용자 Bearer로 `/auth/v1/user`를 확인하고 **보호된 app_metadata.role=admin**만 허용한다.
사용자가 수정할 수 있는 user_metadata, 어민·공무원·edge·analysis 역할은 관리자 권한이 아니다.
service_role을 사용하지 않고, 이번 PR에서 실제 계정에 관리자 역할을 부여하지 않는다.

```sh
python -m pip install -r requirements.txt -r requirements-api-dev.txt
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

SUPABASE_URL/SUPABASE_PUBLISHABLE_KEY를 기존 API와 같이 설정한다.
`ROCKFISH_CONFIG_DIR`는 검증 로더·관리자 편집기·기존 개별 설정 reader가 함께 사용하는 디렉터리다.
미지정이면 저장소 config/를 사용한다. API와 분석 worker를 같은 설정 경로/배포에 연결한다.
환경변수는 프로세스 시작 전에 같은 절대 경로로 설정한다. API 요청에서 디렉터리를 지정할 수 없다.

## 읽기와 수정

GET은 file, revision, values(전체 검증 값), loaded_at을 반환하고 `ETag: "revision"`을 붙인다.
PUT은 **해당 파일 전체 교체**이며 부분 merge가 아니다:

```json
{
  "values": {"여기에": "GET에서 받은 파일 전체 값을 수정해 넣는다"},
  "expected_revision": "GET의 64자리 전체 설정 SHA256",
  "request_key": "논리 수정 요청마다 생성한 UUID",
  "reason": "변경 근거/검토 기록"
}
```

예시는 외곽 형식만 설명하며 values의 실제 형식은 해당 파일 스키마를 따른다.
누락 필수 키·알 수 없는 구조·잘못된 범위·비유한 수·역전 임계값·잘못된 출처는 저장 전 거부한다.
제안한 파일뿐 아니라 **다섯 파일 전체**를 검증한다. JSON 제안과 최종 YAML의 의미가 같은지도 확인한다.
요청의 JSON 표현은1MiB 이하, 생성되는 각 파일도1MiB 이하로 제한한다. 변경 이유는 공백이 아닌1~500자다.

성공200은 persisted=true, changed, file, request_key, previous_revision, revision,
validated_at, reload_status=validated, application, restart_required를 반환한다.
같은 값이면 changed=false이며 파일 바이트/revision을 바꾸지 않는다. 이 경우에도 요청 영수증은 기록한다.
반환 ETag는 이 요청 결과의 revision이다. 이전 영수증을 재조회한 경우 현재 최신 revision과 다를 수 있으므로 GET으로 갱신한다.

- 다른 관리자가 먼저 수정하면409. GET으로 최신 값을 읽고 검토한 뒤 새 request_key로 요청한다.
- 타임아웃/503은 **같은 body·같은 key**로 재전송한다. 성공했던 요청은 당시 영수증을 반환하고 다시 쓰지 않는다.
- 같은 key에 파일/값/reason/expected_revision을 바꾸면409다. 키는 관리자 사용자별로 구분한다.
- 401: 인증 없음/만료/등록 사용자 아님. 403: 관리자 역할 없음.
- 422: 요청/설정 검증 실패. 409: revision/키/다른 프로세스 쓰기 충돌. 503: 저장·복구·현재 설정 읽기 문제.
- 오류에는 설정 본문·내부 경로·토큰을 반사하지 않는다. 관리자 설정 값은 다른 사용자에게 공개하지 않는다.
- 응답 모드는 configuration이며 private/no-store와 Vary:Authorization을 사용한다.

## 저장·감사·복구

같은 디렉터리의 `.admin-config.lock`으로 스레드/프로세스 간 쓰기를 직렬화한다.
기존 파일은 ruamel.yaml의 round-trip 편집으로 가정/출처 주석과 따옴표를 가능한 한 유지한다.
목록을 재정렬하면 주석은 기존 위치에 남을 수 있어 운영자는 변경 파일도 검토해야 한다.
최종 바이트를 검증한 뒤 같은 디렉터리의 임시 파일을 flush/fsync하고 os.replace한다. 부분 YAML을 노출하지 않는다.

`.admin-config.sqlite3`는 관리자 ID·UUID·이유·본문 해시·이전/이후 revision과 파일 바이트·영수증을 기록한다.
토큰은 기록하지 않는다. 편집 전에 prepared 기록을 커밋하고, 교체·reload 확인 뒤 committed로 바꾼다.
다음 관리자 GET/PUT에서 중단된 작업을 확인한다:

- 현재 전체 revision이 이후 revision과 같으면 적용됐던 것으로 영수증을 확정한다.
- 이전 revision과 같으면 파일이 바뀌지 않았던 작업으로 표시하고 동일 요청의 재시도를 허용한다.
- 어느 쪽도 아니면 외부 수정과 결과를 구별할 수 없어503으로 막고 임의 덮어쓰기하지 않는다.
  운영자가 서버 파일/감사 journal을 대조해 해결한다. CLI 로더로 현재 파일 검증은 할 수 있다.

파일과 SQLite는 하나의 DB 트랜잭션이 아니다. 프로세스 중단의 명확한 전후 상태를 복구하며 모든 전원/디스크 손실을 보장하지 않는다.
수정 API와 수동 배포가 동시에 파일을 바꾸는 운영은 지원하지 않는다. 파일 lock을 공유하는 같은 로컬/공유 파일시스템과 쓰기 가능한 볼륨이 필요하다.
여러 호스트의 독립 복사본으로 자동 배포하는 기능은 없다. 감사 파일/잠금은 Git에서 제외하며 백업·권한·보관 정책은 운영 담당이 정한다.
GET도 복구를 수행하므로 해당 설정 디렉터리에 잠금/journal을 만들 수 있어야 한다.

## 적용 시점과 보호 영역

검증 로더는 다음 snapshot 조회에서 새 revision을 읽고 기존 요청이 잡은 snapshot은 유지한다.
개별 reader의 캐시도 파일 식별자·mtime·크기로 구분해 원자 교체된 새 파일을 다른 프로세스에서도 읽는다.
외부 편집기가 파일 식별자/시각/크기를 모두 보존하는 수동 수정은 이 캐시 갱신 계약 밖이다.
모델 모듈에서 import 시 고정하는 cycle_minutes/baseline 변경은 **restart_required**를 반환한다.
저장 성공이 모델 재학습/배포 완료를 뜻하지 않는다. 분석 worker/API 재시작과 모델·시나리오 호환을 운영자가 맞춘다.
일반 규칙·비용 변경은 next_snapshot이며 과거 분석/추천 snapshot을 덮어쓰지 않는다. 기존 #104의 config_changed/selectable 정책을 따른다.

first_aid.blocked_terms는 API로 완화/변경할 수 없다. 약품/투약량 필터 변경은 검토된 코드·설정 배포로 처리한다.
official 공통 조치는 기존 출처 문서 일치·출력 필터 검증을 통과해야 한다. 새 문구/출처를 임의로 채우지 않는다.

## 검증·담당 합의

임시 설정 디렉터리에서 실제 파일 교체/SQLite/OS 파일 잠금·독립 로더·중단 전후 재시도·동시 수정·주석 보존을 검사한다.
FastAPI/Supabase Auth HTTP는 MockTransport로 관리자/위조 역할/인증 만료·오류 비반사·ETag/재전송을 검사한다.
실제 원격 Supabase 계정·운영 설정·다중 호스트 배포를 변경/검증하지 않았다.

운영/백엔드는 관리자 승인·회수·공유 경로/권한·감사 보관·중단 복구·모델 재시작 순서를,
프론트/백엔드는 전체 교체·revision·키·변경 이유·restart_required/409/503 UX를,
데이터/모델/문서는 변경값의 근거와 가정·모델 호환·필터의 별도 검토 배포를 합의한다.
#34·#35·#45의 다른 팀원 구현은 유지하며 이번 기능에서 알림·집계·적조/작업 API를 구현하지 않는다.
