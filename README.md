# 조피볼락 상태 진단 멀티센서 AI 가두리 관리 플랫폼

경남 남해안 해상가두리 조피볼락의 행동을 소나·카메라로 측정해 **스트레스 지수**를 만들고, **의심 원인**을 추정해 **대응 선택지와 손익**을 비교해 주는 시스템입니다. 최종 결정은 어민이 합니다.

> 해커톤 MVP 목표: **고수온 → 태풍(피난처 소실) 시나리오 하나를 처음부터 끝까지** 돌린다. (마감 2026-11-28 12:00)

설계 근거는 SRS(요구사항)·SDD(설계) 문서와 [이슈](../../issues)에 있습니다.

## 실행 방법

그룹별 손익과 긴급방류 요건은 [손익 계산기 안내](docs/loss-calculator.md)를 확인하세요.
`python -m decision --input scenarios/loss_request.example.json`으로 합성 입력의 계산 근거를 조회할 수 있습니다.

R1·R2·R3·R5·R8 원인 추정과 #60의 피난처 소실 판정, 파이프라인 결과·설정·검증 방법은
[원인 엔진 안내](docs/cause-engine.md)를 확인하세요.

### API 로컬 실행

지표 수집 POST·재고 PUT·시계열·에코그램은 사용자 인증과 가두리 권한을 확인하는 실제 데이터 경로입니다.
수집은 전용 edge 계정, 어민 데이터는 사용자 계정으로 인증합니다. 나머지는 기존 목업입니다. 실제 데이터 경로의 설정·RPC migration·Bearer/이미지 연계는
[어민 데이터 API 1차 안내](docs/farmer-data-api.md)를 확인하세요.
Python 3.11 이상, 저장소 루트에서 실행합니다.

```sh
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

macOS/Linux:

```sh
source .venv/bin/activate
```

```sh
python -m pip install -r requirements.txt -r requirements-api-dev.txt
python -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

- [Swagger UI](http://127.0.0.1:8000/docs)
- [OpenAPI JSON](http://127.0.0.1:8000/openapi.json)
- [ReDoc](http://127.0.0.1:8000/redoc)
- [Health](http://127.0.0.1:8000/health)
- [SDD 상태 예시](http://127.0.0.1:8000/cages/3/status)

프론트 개발 서버 `http://localhost:5173`, `http://127.0.0.1:5173`에 CORS를 허용합니다.
다른 주소는 `API_CORS_ORIGINS` 환경변수에 쉼표로 구분해 지정합니다.

### API·공통 모델 테스트

```sh
python -m unittest discover -s tests -v
```

라우트·응답 형식·목업의 한계·합의할 항목은 [API 계약](docs/api-contract.md)을 확인하세요.
Supabase 환경변수·edge 권한·JSONL 업로드는 [지표 수집 계약](docs/ingestion-contract.md)을 확인하세요.
다른 모듈의 실행법은 각 구현 PR과 이슈 #42에서 이어 정리합니다.

### 카메라 파일 영상 시험 (M1~M3)

```sh
python -m pip install -r requirements-vision.txt
python -m vision --help
```

공개 영상·CFD 가중치 다운로드, 실행 명령, 품질 게이트와 수심·활동량 보정,
시험 결과는 [카메라 파이프라인 안내](docs/vision-pipeline.md)를 확인하세요.
AI Hub 조피볼락 20장의 탐지 비교 방법·결과와 시연 판단은 [VC-07 시험 기록](docs/vc07-rockfish.md)에 있습니다.

## 디렉터리 구조

### 공통 설정 검증·로더

```sh
python -m pip install -r requirements.txt
python -m common.config_loader
```

5개 YAML의 묶음 검증·재시작 없는 재조회·오류 정책은 [설정 로더 안내](docs/config-loader.md)를 확인하세요.

### 모듈 목록

| 폴더 | 역할 |
| --- | --- |
| `simulator/` | 개체 기반 시뮬레이터, 합성 에코그램 |
| `sonar/` | M4 소나 지표 추출 (수직 분포·활동량) |
| `vision/` | M1 품질 점수, M2 CLAHE, M3 탐지·추적 |
| `fusion/` | 영상 품질 가중 센서 융합 |
| `stress/` | M5 기준선·스트레스 지수·경보 단계 |
| `cause/` | M6 원인 추정 규칙 엔진 |
| `decision/` | 손익 계산, 긴급방류 요건 확인 |
| `notify/` | 알림 문장 생성, 템플릿, 관리요령 문서(RAG) |
| `api/` | FastAPI |
| `web/` | React (어민 화면, 지자체 대시보드) |
| `config/` | thresholds, cause_rules, release_rules, costs, first_aid, vision (YAML) |
| `scenarios/` | 시뮬레이터 시나리오 YAML |
| `docs/` | SRS(요구사항), SDD(설계) 문서. 구현과 달라지면 PR로 같이 고친다 |
| `SBOM.md` | 오픈소스 라이선스 목록 |

모듈 사이는 `MetricRecord` 하나의 형식으로만 데이터를 주고받습니다.

## 팀 역할

| 역할 | 맡은 모듈 | 먼저 끝낼 것 | 담당 |
| --- | --- | --- | --- |
| 데이터·모델 | `simulator`, `sonar`, `stress`, `fusion` | 고수온→태풍 시나리오의 합성 에코그램과 MetricRecord | _(이름)_ |
| 백엔드 | `api`, Supabase, `cause`, `decision`, `notify` | `/status`, `/recommendation` API, R1·R2 규칙 | _(이름)_ |
| 프론트 | `web` | 목업 JSON으로 화면 먼저 만들고 API로 교체 | _(이름)_ |
| 문서·검증 | 시나리오·config YAML, 비전 시험, RAG 자료, 검증, 발표 | 시나리오 YAML·규칙·비용 YAML | _(이름)_ |

## 협업 규칙

브랜치·PR·이슈 규칙은 [CONTRIBUTING.md](CONTRIBUTING.md)를 봅니다.

## 한계

현장 미검증, 합성 데이터 기반입니다. 질병·기생충은 **의심**까지만 추정하며 병명 확정·약품 안내는 하지 않습니다.

## 라이선스

[LICENSE](LICENSE) 참고. 사용한 오픈소스는 `SBOM.md`에 정리합니다.
