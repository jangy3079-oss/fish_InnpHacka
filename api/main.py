"""Run with: python -m uvicorn api.main:app --reload."""

import json
import os
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Body, Depends, FastAPI, HTTPException, Path as PathParam, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime
from common import MetricRecord

from .ingestion import MAX_BATCH_SIZE, SupabaseMetricStore, get_metric_store

from .farmer_data import FarmerDataStore, get_farmer_store
from .areas import router as areas_router
from .access import router as access_router
from .echogram import EchogramRenderer, get_echogram_renderer
from .diagnosis import create_request, read_request, get_diagnosis_loader
from .analysis import read_status,read_recommendation,choose,get_analysis_loader
from .demo import get_replay_engine, replay
from .admin_config import require_admin,get_config_editor,invoke as config_operation
from common.config_editor import ConfigEditor
from pipeline.replay import ReplayEngine
from common.config_loader import ConfigLoader

from .schemas import (
    CageStatus, ChoiceRequest, ChoiceReceipt, ConfigFile, ConfigRequest, ConfigView,ConfigReceipt,DemoResult,
    DiagnosisRequest, DiagnosisReceipt, DiagnosisSummary, Metric, MockReceipt, Recommendation,
    Scenario, StockRequest, Timeseries, StockReceipt, IngestReceipt, WorkLogReceipt, WorkLogRequest,
)

FIXTURE = Path(__file__).parent / "fixtures/status.json"
Id = Annotated[int, PathParam(gt=0)]


def status_fixture(cage_id: int = 3) -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["cage_id"] = cage_id
    return data


def time_window(
    start: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    end: Annotated[AwareDatetime | None, Query(alias="to")] = None,
) -> tuple:
    if start is not None and end is not None and start > end:
        raise HTTPException(422, detail="from must be before or equal to to")
    return start, end


app = FastAPI(
    title="Rockfish Guard API", version="0.9.0",
    description="해역 집계와 어민 상태·추천·선택, 지표 수집·재고·시계열·에코그램·진단 접수는 인증된 실제 데이터 경로입니다. "
                "시나리오 재생은 DB 저장 없는 합성 분석입니다. 관리자 설정은 보호된 역할·버전 검사 후 로컬 YAML을 저장합니다. docs/admin-config-api.md 등 API 계약 문서 참조.",
)
origins = [s.strip() for s in os.getenv("API_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if s.strip()]
app.include_router(areas_router)
app.include_router(access_router)
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                   allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type", "Authorization"],
                   expose_headers=["X-Data-Mode"])


@app.middleware("http")
async def mock_header(request, call_next):
    response = await call_next(request)
    path = request.url.path.rstrip("/")
    real = path.startswith("/areas/") or path in ("/me", "/ingest/metrics") or (path.startswith("/cages/") and path.rsplit("/", 1)[-1] in {"status", "recommendation", "timeseries", "stock", "echogram", "diagnosis-request", "work-log"}) or (path.startswith('/recommendations/') and path.endswith('/choice'))
    response.headers["X-Data-Mode"] = "asset" if real and path.endswith("/echogram") else "database" if real else "mock"
    if path.startswith('/admin/config/'):
        response.headers['X-Data-Mode'] = 'configuration'
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['Vary'] = ', '.join(filter(None,[response.headers.get('Vary'),'Authorization']))
    if path.startswith('/demo/scenario/'):
        response.headers['X-Data-Mode'] = 'simulation'
        response.headers['Cache-Control'] = 'no-store'
    if real:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = ", ".join(filter(None, [response.headers.get("Vary"), "Authorization"]))
    return response


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "mixed"}


edge_bearer = HTTPBearer(auto_error=False, scheme_name="EdgeAccessToken")


def require_edge_token(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(edge_bearer)]) -> str:
    if credentials is None:
        raise HTTPException(401, detail={"code": "missing_token", "message": "Bearer access token required"},
                            headers={"WWW-Authenticate": "Bearer"})
    return credentials.credentials


@app.post("/ingest/metrics", response_model=IngestReceipt, status_code=201, tags=["ingest"],
          responses={401: {"description": "인증 실패"}, 403: {"description": "가두리 수집 권한 없음"},
                     409: {"description": "기존 지표와 값/신뢰도 충돌, 전체 거부"}, 503: {"description": "설정 또는 Supabase 장애"}},
          description="MetricRecord 1~1000개를 원자적으로 저장합니다. 동일 값 재전송은 중복 성공, 다른 값은 전체 거부하며 덮어쓰지 않습니다.")
def ingest(records: Annotated[list[MetricRecord], Body(min_length=1, max_length=MAX_BATCH_SIZE)],
           token: Annotated[str, Depends(require_edge_token)],
           store: Annotated[SupabaseMetricStore, Depends(get_metric_store)]) -> IngestReceipt:
    return store.insert(records, token)


@app.get("/cages/{id}/status", response_model=CageStatus, tags=["cages"])
def status(id: Id,store: Annotated[FarmerDataStore,Depends(get_farmer_store)],
           loader: Annotated[ConfigLoader,Depends(get_analysis_loader)]):
    return read_status(store,id,loader)


@app.get("/cages/{id}/timeseries", response_model=Timeseries, tags=["cages"])
def timeseries(id: Id, metric: Metric, window: Annotated[tuple, Depends(time_window)],
               store: Annotated[FarmerDataStore, Depends(get_farmer_store)],
               source: Literal["sonar", "camera", "env"] | None = None,
               limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
               before: AwareDatetime | None = None):
    start, end = window
    return store.timeseries(id, metric, start, end, source, limit, before)


@app.get("/cages/{id}/echogram", tags=["cages"], response_class=Response,
         responses={200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}, "description": "권한 확인한 NPZ 에코그램의 PNG 미리보기"}})
def echogram(id: Id, window: Annotated[tuple, Depends(time_window)],
             store: Annotated[FarmerDataStore, Depends(get_farmer_store)],
             renderer: Annotated[EchogramRenderer, Depends(get_echogram_renderer)]):
    store.require_cage(id)
    return Response(content=renderer.render(id, *window), media_type="image/png")


@app.get("/cages/{id}/recommendation", response_model=Recommendation, tags=["cages"])
def recommendation(id: Id,store: Annotated[FarmerDataStore,Depends(get_farmer_store)],
                   loader: Annotated[ConfigLoader,Depends(get_analysis_loader)]):
    return read_recommendation(store,id,loader)


@app.post("/recommendations/{id}/choice", response_model=ChoiceReceipt, tags=["recommendations"])
def choice(id: Id, body: ChoiceRequest,store: Annotated[FarmerDataStore,Depends(get_farmer_store)],
           loader: Annotated[ConfigLoader,Depends(get_analysis_loader)]):
    return choose(store,id,body,loader)


@app.put("/cages/{id}/stock", response_model=StockReceipt, tags=["cages"],
         description="소유자만 재고 전체를 원자적으로 대체합니다. 요청에서 생략한 크기 그룹은 제거됩니다.")
def stock(id: Id, body: StockRequest, store: Annotated[FarmerDataStore, Depends(get_farmer_store)]):
    return store.replace_stock(id, body)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    if request.url.path.startswith('/admin/config/'):
        return JSONResponse(status_code=422,content={'detail':{'code':'invalid_config_request','message':'Invalid config request'}})
    if request.url.path.rstrip("/").endswith(("/diagnosis-request",'/choice','/work-log')):
        path = request.url.path.rstrip('/')
        code = ('invalid_choice_request' if path.endswith('/choice') else 'invalid_work_log_request' if path.endswith('/work-log')
                else 'invalid_diagnosis_request')
        return JSONResponse(status_code=422, content={"detail": {"code": code, "message": "Invalid request"}})
    return await request_validation_exception_handler(request, exc)


@app.get('/cages/{id}/diagnosis-request', response_model=DiagnosisSummary, tags=['cages'],
         description='소유자 본인의 최신 접수 요약 또는 request_key에 해당하는 접수만 조회합니다. 새 접수·전달을 만들지 않습니다.')
def diagnosis_query(id: Id, store: Annotated[FarmerDataStore, Depends(get_farmer_store)],
                    request_key: UUID | None = None):
    return read_request(store, id, request_key)


@app.post("/cages/{id}/diagnosis-request", response_model=DiagnosisReceipt, status_code=201, tags=["cages"],
          description="소유자의 현재 R5/R8 근거·행동 지표를 저장하고 확인된 공통 조치를 반환합니다. 외부 전달은 대기 상태입니다.")
def diagnosis(id: Id, body: DiagnosisRequest,
              store: Annotated[FarmerDataStore, Depends(get_farmer_store)],
              loader: Annotated[ConfigLoader, Depends(get_diagnosis_loader)]):
    return create_request(store, id, body, loader)


@app.post("/cages/{id}/work-log", response_model=WorkLogReceipt, status_code=201, tags=["cages"],
          responses={401: {"description": "인증 실패"}, 403: {"description": "소유자가 아님"}, 404: {"description": "가두리 없음"},
                     409: {"description": "같은 request_key로 다른 내용"}, 422: {"description": "종류·시각 오류 (미래, 7일 초과)"}},
          description="소유자의 망갈이(net_change)·선별(sorting) 작업 기록을 저장합니다. 원인 규칙 R6(작업 스트레스)의 입력입니다. "
                      "같은 request_key 재전송은 같은 영수증을 돌려줍니다.")
def work_log(id: Id, body: WorkLogRequest, store: Annotated[FarmerDataStore, Depends(get_farmer_store)]):
    return store.record_work_log(id, body)


@app.post("/demo/scenario/{name}", response_model=DemoResult, tags=["demo"],
          description="합성 시나리오의 경과 시간 t(시간 단위)까지 완료된 주기만 분석합니다. 상태·원인·추천·시계열을 반환하며 DB 저장·조치 실행·알림 전송은 하지 않습니다.")
def demo(name: Scenario, engine: Annotated[ReplayEngine,Depends(get_replay_engine)],
         t: Annotated[float, Query(ge=0, allow_inf_nan=False)] = 0):
    return replay(engine,name,t)


@app.get('/admin/config/{file}',response_model=ConfigView,tags=['admin'],
         description='보호된 app_metadata.role=admin만 설정과 전체 revision을 읽습니다.')
def read_config(file:ConfigFile,response:Response,actor:Annotated[object,Depends(require_admin)],
                editor:Annotated[ConfigEditor,Depends(get_config_editor)]):
    result = config_operation(editor,file)
    response.headers['ETag'] = f'"{result.revision}"'
    return result


@app.put('/admin/config/{file}',response_model=ConfigReceipt,tags=['admin'],
         description='전체 파일 값을 교체합니다. expected_revision·UUID request_key·reason 필수. 전체 설정 검증 후 원자 저장·재로드하며 필요한 모델 재시작을 구분합니다.')
def config(file:ConfigFile,body:ConfigRequest,response:Response,actor:Annotated[object,Depends(require_admin)],
           editor:Annotated[ConfigEditor,Depends(get_config_editor)]):
    result = config_operation(editor,file,body,actor)
    response.headers['ETag'] = f'"{result.revision}"'
    return result
