"""국립수산과학원 실시간 수온 연동 (#43, FR-MEA-08, A-04, DR-04).

확인한 제공 방식 (2026-11-27, nifs.go.kr OpenAPI 안내 페이지)
  실시간 해양수산환경 관측 정보  https://www.nifs.go.kr/OpenAPI_json?id=risaList&key=<인증키>
    30분 갱신. 필드: sta_cde(관측소코드), sta_nam_kor, obs_dat(관측일자), obs_tim(관측시각),
    obs_lay(1 표층, 2 중층, 3 저층), wtr_tmp(수온), repaire_gbn(1 정상, 2 점검), rpr_yn(N 정상, Y 점검)
  관측소 목록  https://www.nifs.go.kr/OpenAPI_json?id=risaCode&key=<인증키>&gru_nam=E|W|S
  - 인증키는 수과원 누리집에서 발급 (환경변수 NIFS_API_KEY)
  - DO는 이 API에 없음 (정선관측 sooList의 dox는 격월 관측이라 실시간 용도 아님)
  - 고수온 특보는 API로 제공되지 않음 → 확장 (수과원 예보·속보 게시판)

변환 규칙
  obs_lay 1 → temp_surface, 2 → temp_mid (저층은 MetricRecord 지표에 없어 버림)
  점검 중(repaire_gbn=2 또는 rpr_yn=Y) 값은 버림 (0이나 가짜 값을 만들지 않음)
  시각은 KST로 보고 UTC로 바꿈. source=env, confidence=1.0
  sea_area.nifs_station_id → 그 해역 가두리들에 같은 값을 붙임 (station_cages 매핑)

인증키가 없으면 과거 자료(CSV)를 읽어 시나리오 수온 키프레임으로 쓴다 (to_keyframes).
"""

from __future__ import annotations

import csv
import json
import os
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from common import MetricRecord

BASE = "https://www.nifs.go.kr/OpenAPI_json"
KST = timezone(timedelta(hours=9))
LAYER_METRIC = {"1": "temp_surface", "2": "temp_mid"}


def fetch(service: str = "risaList", key: Optional[str] = None, timeout: float = 20.0, **params) -> dict:
    key = key or os.environ.get("NIFS_API_KEY")
    if not key:
        raise RuntimeError("NIFS_API_KEY 환경변수(수과원 OpenAPI 인증키)가 필요합니다")
    q = urllib.parse.urlencode({"id": service, "key": key, **params})
    with urllib.request.urlopen(f"{BASE}?{q}", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _items(obj) -> List[dict]:
    """응답 JSON에서 관측 행 목록 찾기 (header/body/Item 중첩이 달라도 동작)."""
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj) and any("wtr_tmp" in x or "sta_cde" in x for x in obj):
            return obj
        for x in obj:
            got = _items(x)
            if got:
                return got
    elif isinstance(obj, dict):
        if "sta_cde" in obj and ("wtr_tmp" in obj or "sta_nam_kor" in obj):
            return [obj]
        for k in ("item", "Item", "items", "body", "Body", "response"):
            if k in obj:
                got = _items(obj[k])
                if got:
                    return got
        for v in obj.values():
            got = _items(v)
            if got:
                return got
    return []


def parse_obs_time(obs_dat: str, obs_tim: str = "") -> datetime:
    """'2026-08-12' / '20260812' + '05:30' / '0530' / '05:30:00' → UTC datetime (입력은 KST)."""
    d = str(obs_dat).strip().replace("-", "").replace(".", "")[:8]
    t = str(obs_tim or "").strip()
    if " " in t:                       # obs_tim에 날짜가 같이 붙어 오는 경우
        t = t.split()[-1]
    t = t.replace(":", "")[:4].ljust(4, "0")
    return datetime.strptime(d + t, "%Y%m%d%H%M").replace(tzinfo=KST).astimezone(timezone.utc)


def is_valid(row: Mapping) -> bool:
    if str(row.get("repaire_gbn", "1")).strip() == "2" or str(row.get("rpr_yn", "N")).strip().upper() == "Y":
        return False
    try:
        v = float(row.get("wtr_tmp"))
    except (TypeError, ValueError):
        return False
    return -2.0 < v < 40.0


def to_records(rows: Iterable[Mapping], station_cages: Mapping[str, Sequence[str]]) -> List[MetricRecord]:
    """관측 행 → env MetricRecord. station_cages: {관측소코드: [cage_id, ...]}"""
    out: List[MetricRecord] = []
    for row in rows:
        metric = LAYER_METRIC.get(str(row.get("obs_lay", "")).strip())
        cages = station_cages.get(str(row.get("sta_cde", "")).strip())
        if not metric or not cages or not is_valid(row):
            continue
        ts = parse_obs_time(row.get("obs_dat", ""), row.get("obs_tim", ""))
        for cid in cages:
            out.append(MetricRecord(cage_id=cid, ts=ts, source="env", metric=metric,
                                    value=float(row["wtr_tmp"]), confidence=1.0))
    return out


def realtime_records(station_cages: Mapping[str, Sequence[str]], key: Optional[str] = None) -> List[MetricRecord]:
    """수과원 실시간 수온 → env MetricRecord (인증키 필요)."""
    return to_records(_items(fetch("risaList", key)), station_cages)


# ---------------------------------------------------------------- 과거 자료 → 시나리오

def load_csv(path, encoding: Optional[str] = None) -> List[dict]:
    """과거 자료 CSV (risaList와 같은 열 이름, 또는 data.go.kr '실시간수온정보' 파일)."""
    raw = Path(path).read_bytes()
    for enc in ([encoding] if encoding else ["utf-8-sig", "cp949"]):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    rows = list(csv.DictReader(text.splitlines()))
    alias = {"관측소코드": "sta_cde", "관측소명": "sta_nam_kor", "관측일자": "obs_dat", "관측시각": "obs_tim",
             "관측일시": "obs_tim", "관측층": "obs_lay", "관측계층": "obs_lay", "수온": "wtr_tmp"}
    return [{alias.get(k.strip(), k.strip()): v for k, v in r.items()} for r in rows]


def to_keyframes(rows: Iterable[Mapping], station: str, start: datetime, step_h: float = 3.0) -> Dict[str, list]:
    """한 관측소 과거 자료 → 시나리오 YAML env 키프레임 {temp_surface: [[h, ℃], ...], temp_mid: ...}.

    step_h 시간 평균으로 줄여 YAML에 넣기 좋은 크기로 만든다. start는 시나리오 시작(KST/UTC 무관).
    """
    start = start.astimezone(timezone.utc)
    acc: Dict[str, Dict[int, list]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        metric = LAYER_METRIC.get(str(row.get("obs_lay", "")).strip())
        if str(row.get("sta_cde", "")).strip() != station or not metric or not is_valid(row):
            continue
        h = (parse_obs_time(row.get("obs_dat", ""), row.get("obs_tim", "")) - start).total_seconds() / 3600
        if h < 0:
            continue
        acc[metric][int(h // step_h)].append(float(row["wtr_tmp"]))
    return {m: [[round(k * step_h, 2), round(sum(v) / len(v), 2)] for k, v in sorted(b.items())]
            for m, b in acc.items()}
