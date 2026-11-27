# Rockfish Guard — 조피볼락 가두리 관리 플랫폼

현재 API는 프론트 연동을 위한 **목업**입니다. DB·인증·분석·저장·발송은 연결하지 않습니다.
모든 응답에 `X-Data-Mode: mock`을 표시하며 변경 요청도 `persisted: false`입니다.

## 로컬 실행

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
python -m pip install -r requirements-api-dev.txt
python -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

- [Swagger UI](http://127.0.0.1:8000/docs)
- [OpenAPI JSON](http://127.0.0.1:8000/openapi.json)
- [ReDoc](http://127.0.0.1:8000/redoc)
- [Health](http://127.0.0.1:8000/health)
- [SDD 상태 예시](http://127.0.0.1:8000/cages/3/status)

프론트 개발 서버 `http://localhost:5173`, `http://127.0.0.1:5173`에 CORS를 허용합니다.
다른 주소는 `API_CORS_ORIGINS` 환경변수에 쉼표로 구분해 지정합니다.

## 테스트

```sh
python -m unittest discover -s tests -v
```

라우트·응답 형식·목업의 한계·합의할 항목은 [API 계약](docs/api-contract.md)을 확인하세요.
