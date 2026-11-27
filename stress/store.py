"""stress_score 테이블 저장 (#24, SDD 7장).

행 형식: cage_id, ts, score, level, baseline_status, model_version

- save_jsonl(): 파일로 저장 (Supabase 없이 시연·검증할 때)
- upsert_supabase(): 환경변수 SUPABASE_URL, SUPABASE_SERVICE_KEY가 있으면 PostgREST로 upsert
  (cage_id, ts) 충돌 시 덮어쓴다. 테이블·RLS는 백엔드(#3) 스키마를 따른다.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Iterable, List, Optional

TABLE = "stress_score"


def rows(scores: Iterable) -> List[dict]:
    """저장할 행만 (ready인데 점수가 없는 행은 제외, #68 DB 계약)."""
    return [s.to_row() for s in scores if s.storable]


def save_jsonl(scores: Iterable, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows(scores):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def upsert_supabase(scores: Iterable, url: Optional[str] = None, key: Optional[str] = None,
                    batch: int = 500, timeout: float = 30.0) -> int:
    url = url or os.environ.get("SUPABASE_URL")
    key = key or os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수가 필요합니다")
    data = rows(scores)
    endpoint = f"{url.rstrip('/')}/rest/v1/{TABLE}?on_conflict=cage_id,ts"
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    for i in range(0, len(data), batch):
        req = urllib.request.Request(endpoint, data=json.dumps(data[i:i + batch]).encode(), headers=headers,
                                     method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"Supabase upsert 실패: HTTP {resp.status}")
    return len(data)
