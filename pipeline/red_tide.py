"""적조 특보 CSV → DB 적재 (#45, 원인 규칙 R4 입력). 운영자 전용.

    python -m pipeline.red_tide advisories.csv --dry-run     # 검증만
    python -m pipeline.red_tide advisories.csv               # SUPABASE_URL, SUPABASE_SERVICE_KEY 필요

CSV 열: sea_area_id, stage, starts_at, ends_at, source, source_ref
- stage: watch(예비주의보) · advisory(주의보) · warning(경보). 한글 단계명도 받는다
- starts_at·ends_at: 시간대가 있는 ISO 시각 (예: 2026-08-10T14:00:00+09:00). ends_at(해제)은 비워도 된다
- source: 출처 이름 (예: 국립수산과학원 적조속보), source_ref: 속보 번호·문서 ID처럼 원문을 다시 찾을 수 있는 값

수과원 적조 정보는 공개 API로 확인하지 못해, 운영자가 속보를 보고 CSV로 옮겨 넣는 것을 기본 경로로 둔다.
같은 (해역, 단계, 시작 시각)을 다시 넣으면 해제 시각·출처만 갱신된다 (DB 함수 load_red_tide_advisories).
원인 엔진은 config/cause_rules.yaml R4 env.advisory_stages(기본 주의보·경보) 단계만 '특보 발령'으로 본다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List

import httpx

STAGES = {"watch": "watch", "advisory": "advisory", "warning": "warning",
          "예비주의보": "watch", "주의보": "advisory", "경보": "warning"}
COLUMNS = ("sea_area_id", "stage", "starts_at", "ends_at", "source", "source_ref")
BATCH = 1000


def _time(value: str, row: int, name: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{row}행 {name}: ISO 시각이 아닙니다") from None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError(f"{row}행 {name}: 시간대(+09:00 등)가 필요합니다")
    return stamp


def parse(path) -> List[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or set(reader.fieldnames) != set(COLUMNS):
            raise ValueError(f"CSV 열은 {', '.join(COLUMNS)} 이어야 합니다")
        rows = []
        for i, r in enumerate(reader, start=2):
            try:
                area = int(r["sea_area_id"])
            except ValueError:
                raise ValueError(f"{i}행 sea_area_id: 정수가 아닙니다") from None
            if area <= 0:
                raise ValueError(f"{i}행 sea_area_id: 양수여야 합니다")
            stage = STAGES.get(r["stage"].strip())
            if stage is None:
                raise ValueError(f"{i}행 stage: {r['stage']!r} (watch·advisory·warning 또는 예비주의보·주의보·경보)")
            start = _time(r["starts_at"], i, "starts_at")
            end = _time(r["ends_at"], i, "ends_at") if r["ends_at"].strip() else None
            if end is not None and end <= start:
                raise ValueError(f"{i}행: 해제 시각이 발령 시각보다 늦어야 합니다")
            source, ref = r["source"].strip(), r["source_ref"].strip()
            if not source or not ref:
                raise ValueError(f"{i}행: source와 source_ref가 필요합니다")
            rows.append({"sea_area_id": area, "stage": stage, "starts_at": start.isoformat(),
                         "ends_at": None if end is None else end.isoformat(), "source": source, "source_ref": ref})
    if not rows:
        raise ValueError("적재할 행이 없습니다")
    keys = [(r["sea_area_id"], r["stage"], datetime.fromisoformat(r["starts_at"])) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("같은 해역·단계·발령 시각이 두 번 있습니다")
    return rows


def load(rows: List[dict], client: httpx.Client, url: str, key: str) -> int:
    total = 0
    for i in range(0, len(rows), BATCH):
        response = client.post(f"{url.rstrip('/')}/rest/v1/rpc/load_red_tide_advisories",
                               headers={"apikey": key, "Authorization": f"Bearer {key}"},
                               json={"p_rows": rows[i:i + BATCH]})
        if response.status_code != 200:
            raise RuntimeError(f"적재 실패 (HTTP {response.status_code}). 같은 CSV로 다시 실행해도 안전합니다")
        body = response.json()
        if body.get("persisted") is not True or body.get("received_count") != len(rows[i:i + BATCH]):
            raise RuntimeError("적재 응답이 올바르지 않습니다. DB를 확인한 뒤 다시 실행하세요")
        total += body["received_count"]
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description="적조 특보 CSV → DB (원인 규칙 R4 입력)")
    ap.add_argument("csv", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="검증만 하고 적재하지 않음")
    a = ap.parse_args(argv)
    try:
        rows = parse(a.csv)
    except (OSError, ValueError) as e:
        ap.error(str(e))
    if a.dry_run:
        print(json.dumps({"valid_rows": len(rows), "rows": rows}, ensure_ascii=False, indent=1))
        return
    url, key = os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        ap.error("SUPABASE_URL, SUPABASE_SERVICE_KEY 환경변수가 필요합니다 (운영자 전용)")
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        try:
            n = load(rows, client, url, key)
        except (RuntimeError, httpx.HTTPError, ValueError) as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
    print(json.dumps({"persisted": True, "received_count": n}, ensure_ascii=False))


if __name__ == "__main__":
    main()
