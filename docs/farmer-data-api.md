# 어민 데이터 API 1차 — #29

범위는 재고 PUT, 지표 시계열 GET, 에코그램 GET이다.
status·추천·선택 저장은 아직 기존 목업이며 #104 후속 범위다.
DB/RLS PR #67과 어민 API PR #99는 main에 병합되었다.
#81 최신 main 통합으로 edge 전용 수집 API도 함께 제공한다.
#83 내보내기는 그대로 유지하되, 시계열 경로는 DB 원시 지표를 조회하며 시나리오 재생 연계는 #30 범위다.

## 인증·접근 권한

세 경로 모두 `Authorization: Bearer <Supabase user access token>`을 요구한다.
`/auth/v1/user`로 계정을 검증하고 동일한 사용자 토큰으로 PostgREST/RPC를 호출한다.
service_role로 조회하거나 저장하지 않는다. 가두리 접근은 #3의 RLS를 따른다.

- 소유자: 자기 가두리 재고 입력, 시계열·에코그램 조회
- 지정 해역 공무원: 해당 가두리의 시계열·에코그램 조회만 가능, 재고 변경 불가
- 접근할 수 없거나 없는 가두리: 동일한 404. 수신 오류 본문에 DB 내부 정보를 노출하지 않는다.
- 성공한 재고·시계열 경로는 `X-Data-Mode: database`, 에코그램은 `asset`.
  인증·오류 응답도 이 헤더를 가질 수 있으므로 성공은 HTTP 상태와 본문으로 판단한다.
- 실제 데이터 응답은 `Cache-Control: private, no-store`, `Vary: Authorization`을 포함한다.

## PUT /cages/{id}/stock

```json
{"stock":[{"size_group":"mid","count":100,"avg_weight_kg":0.3}],"insured":true}
```

**재고 전체 대체**다. 요청에서 생략한 크기 그룹은 DB에서 제거한다. 이전 값이 남지 않는다.
같은 그룹은 중복할 수 없고 fry/mid/market 중 1~3개를 보낸다.
마릿수는 0 이상의 bigint 범위 정수(불리언·문자열 거부), 무게는 유한한 양수 kg다.
insured는 JSON boolean이며 이번 요청의 모든 그룹에 동일하게 기록한다.
0마리도 저장할 수 있으나 빈 배열은 거부한다.

성공 HTTP 200:

```json
{"persisted":true,"cage_id":3,"received_count":1,"insured":true,"stock":[
  {"size_group":"mid","count":100,"avg_weight_kg":0.3,"updated_at":"2026-11-27T10:00:00Z"}
]}
```

RPC `replace_cage_stock`은 소유권을 다시 확인하고 가두리 행을 잠근 뒤,
생략 그룹 제거와 입력 그룹 upsert를 한 트랜잭션으로 수행한다.
잘못된 한 행 때문에 일부만 저장되지는 않는다. 같은 요청 재전송은 같은 재고 상태가 된다.
updated_at은 서버가 기록하며 동시 요청은 가두리별로 직렬화된다. 최종 도착 요청이 최종 상태다.
버전 기반 충돌 감지나 재고 조회 GET은 이번 범위가 아니다.
private의 제한된 security definer 함수만 전체 교체 권한을 가지며 공개 wrapper는 security invoker다.
익명·일반 사용자의 다른 어가·공무원은 이 쓰기를 수행할 수 없다. 원래의 raw DELETE 권한은 부여하지 않는다.

## GET /cages/{id}/timeseries

필수 `metric`은 공통 10종이다. 선택 query:

- `from`, `to`: 시간대 포함 ISO 8601. 양 끝 포함, from ≤ to. 오프셋은 같은 UTC 순간으로 비교
- `source`: sonar/camera/env, 지정하면 해당 센서만
- `limit`: 1~1000, 기본 1000
- `before`: 이 시각보다 오래된 점만, 다음 페이지용 exclusive cursor

```json
{"cage_id":3,"metric":"activity","points":[
  {"ts":"2026-08-12T05:00:00Z","value":0.6,"source":"sonar","confidence":0.9}
],"has_more":false,"next_before":null}
```

시간 범위에서 **최신 limit개를 선택해 오래된 순서로 반환**한다. 범위가 없으면 전체 중 최신 점이다.
has_more가 true면 next_before를 다음 요청의 before에 넣어 이전 구간을 조회한다.
저장된 점이 없으면 200과 빈 points를 반환하며 가짜 0·보간값을 만들지 않는다.

source를 생략하면 같은 ts의 후보 중 하나를 선택한다:

| 지표 | 우선순위 |
| --- | --- |
| vertical_dist, activity, cohesion | sonar → camera → 기타 |
| temp_surface, temp_mid, do | env → 기타 |
| feeding, still_ratio, rubbing_rate, quality | camera → 기타 |

반환값은 **원시 센서 지표**이며 fusion 결과가 아니다. source·confidence를 함께 반환한다.
품질 게이트·야간 제외·가중 융합은 하지 않는다. 동일 우선순위에서는 source 이름으로 결정한다.
지표 정의·단위는 #2와 같고 vertical_dist는 [저층, 중층, 표층] 배열이다.
원시 값과 #83의 융합된 시간 평균 중 무엇을 화면의 기본값으로 쓸지는 합의가 필요하다.
RPC 자체도 security invoker로 실행하여 metric RLS를 유지한다.

## GET /cages/{id}/echogram

`from`, `to`의 형식·포함 범위는 시계열과 같다. 실제 가두리 조회 권한을 먼저 확인한 후
설정한 로컬 파일 `<ECHOGRAM_ROOT>/<DB 가두리 ID>/echogram.npz`만 읽는다.
파일명이나 경로를 query로 받지 않는다. 경계 밖 symlink 경로도 읽지 않는다.

파일은 #7 `simulator.echogram.save_npz` 형식:

- Sv: 유한 숫자 (ping_time × depth), dB
- ping_time: UTC Unix seconds 정수 1차원, 엄격히 증가
- depth: 0 이상 유한 숫자 수심 m, 엄격히 증가
- meta: JSON 문자열, cage_id 포함

meta.cage_id가 DB ID의 문자열과 같거나 서버의 `ECHOGRAM_PRODUCER_IDS` 매핑과 같아야 한다.
pickle 객체 배열은 허용하지 않는다. 압축 해제 크기는 128 MiB 이하로 제한한다.
구간을 선택한 뒤 선형 강도 공간에서 축소하고 시간(UTC)·수심(m)·Sv(dB) 축을 표시한 900×300 PNG를 반환한다.
기본 색 범위는 #7 미리보기와 같은 -80~-50 dB다. 입력 핑은 #7의 일정 간격 출력을 전제로 한다.
인증이 필요한 이미지이므로 프론트에서 Bearer로 fetch한 blob을 object URL로 표시해야 한다.

에코그램 원본은 metric 테이블에 없으므로 지표에서 재구성하거나 가짜 이미지를 반환하지 않는다.
자산 없음/선택 구간에 핑 없음은 404, 손상된 자산·소유 가두리 불일치는 503이다.
파일 보관·동기화·Storage 전환은 운영/데이터 담당과 합의한다.

## 실행·설정

```sh
python -m pip install -r requirements.txt -r requirements-api-dev.txt
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

환경변수는 명시적으로 설정한다(.env 자동 로드 없음):

```text
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_PUBLISHABLE_KEY=YOUR_PUBLISHABLE_OR_LEGACY_ANON_KEY
ECHOGRAM_ROOT=ABSOLUTE_PATH_TO_ECHOGRAM_ASSETS
ECHOGRAM_PRODUCER_IDS={"3":"cage-003"}
```

Supabase에는 초기 #3 migration과 `20261127000300_farmer_data.sql`이 필요하다.
계정의 farm.owner_user_id 또는 공무원 area_ids는 #68 정책에 맞춰 관리자가 연결해야 한다.
원격 DB 적용·계정 권한 변경은 이 PR에서 수행하지 않는다.

## 프론트 연계 주의

현재 frontend API client는 Bearer를 전달하지 않으며 에코그램은 img src URL을 직접 사용한다.
이번 PR에서는 프론트 담당의 인증·세션 정책을 임의로 바꾸지 않았다.
실제 모드에서 이 세 경로를 쓰려면 세션 access token 헤더 연결과 인증된 이미지 fetch가 필요하다.
기존 목업 모드는 계속 사용할 수 있다. PNG는 img가 표시 가능하지만 URL 직접 호출은 401이다.
현재 StockPage는 0마리 그룹을 보내지 않으므로 생략 그룹 제거 정책과 맞는지 확인해야 한다.

## 검증·제한

```sh
python -m unittest discover -s tests -v
npm ci --prefix supabase/tests
npm test --prefix supabase/tests
```

Python API 테스트는 실제 FastAPI/인증 의존성/PNG 렌더링을 호출하고 Supabase HTTP만 대체한다.
PostgreSQL 테스트는 migration·RPC·RLS·전체 재고 대체·오류 rollback·센서 선택·페이지 경계를 실행한다.
실제 hosted Supabase Auth/PostgREST와 프론트 세션 연결은 접속 정보·인증 UI 연계 후 확인해야 한다.

## 합의 필요

1. **프론트·백엔드·권한 운영:** access token 전달, 세션 갱신·만료 UI, 인증 이미지 blob 처리.
2. **프론트·백엔드·DB:** PUT 전체 대체/생략 그룹 제거, 0마리·보험 저장 위치, 동시 수정 정책.
3. **데이터·모델·백엔드·프론트:** 원시 센서 우선순위와 품질 처리, #83의 융합 평균 사용 여부, 페이지 크기·기간.
4. **데이터·운영·백엔드:** NPZ 배포 경로·보관·생산자 ID 매핑·여러 파일/실장비 에코그램 관리.
5. **프론트·백엔드:** 추가 source/confidence와 has_more/next_before, 401/403/404/422/503 표시 및 실제 프로젝트 통합 검증.

나머지 #29 status/recommendation/choice는 후속 구현이므로 이 이슈를 자동 종료하지 않는다.


## 최신 main 통합 상태

#81 충돌 해결에서 최신 main의 #29 재고·시계열·에코그램과 수집 API를 함께 유지했다.
수집은 edge 인증 DB 경로이고, 재고·시계열·에코그램은 사용자 인증 실제 데이터 경로다.
status·추천·선택 저장은 계속 목업이며 #104 후속 범위다.
