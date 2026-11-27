# 프론트엔드 (React + Vite)

어민 모바일 화면(`/farmer`, `/stock`, `/notifications`)과 지자체 대시보드(`/dashboard`).

## 실행

```
cd frontend
npm install
npm run dev        # http://localhost:5173
```

## 데이터 모드

기본은 목업 데이터(`src/mocks/`)로 동작합니다. 백엔드 목업 API(`api/`, FastAPI)에 붙이려면 `.env`를 만듭니다.

```
VITE_USE_MOCK=false
VITE_API_BASE=http://localhost:8000
```

백엔드의 CORS 허용 주소가 `http://localhost:5173`이므로 `npm run dev -- --port 5173 --strictPort`로 실행합니다.
모든 API 호출은 `src/api/client.js` 한 파일에 있습니다.

## 시연 (시나리오·시간 슬라이더)

화면 상단 조작줄에서 시나리오를 고르고 슬라이더(또는 ▶)로 시간을 움직이면 어민 화면·대시보드·알림이 같은 시각을 따라 바뀝니다.
시간 `t`는 시간(hour) 단위 0~35입니다. 목업 모드에서는 모든 화면이, 실제 API 모드에서는 상태(`POST /demo/scenario/{name}?t=`)만 서버 값을 따릅니다.

## 아직 API에 없어 프론트가 가정한 항목

`options[].calc`, `kind`/`first_aid`, `GET /areas/shapes`, `stress-map.cause_distribution`, `GET /cages/{id}/notifications`.
없으면 해당 부분은 숨기거나 안내 문구로 대체됩니다. 합의 상황은 #62를 참고합니다.
