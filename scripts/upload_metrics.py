"""Upload simulator records.jsonl or the shared JSON fixture without rewriting it."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import TypeAdapter, ValidationError

from common import MetricRecord


class UploadError(Exception):
    """Safe progress/error message without upstream bodies or credentials."""


def read_records(path: Path) -> list[MetricRecord]:
    text = path.read_text(encoding="utf-8-sig")
    payload = [json.loads(line) for line in text.splitlines() if line.strip()] if path.suffix.lower() == ".jsonl" else json.loads(text)
    records = TypeAdapter(list[MetricRecord]).validate_python(payload)
    if not records:
        raise ValueError("No records in input file")
    return records


def upload(records: list[MetricRecord], client: httpx.Client, base_url: str, token: str,
           batch_size: int = 500) -> int:
    inserted = 0
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        try:
            response = client.post(base_url.rstrip("/") + "/ingest/metrics",
                headers={"Authorization": f"Bearer {token}"},
                json=[r.model_dump(mode="json") for r in batch])
        except httpx.RequestError:
            raise UploadError(f"Upload interrupted after {inserted} confirmed records; current batch outcome is unknown. Check storage before retrying.") from None
        if response.status_code != 201:
            raise UploadError(f"Upload stopped: HTTP {response.status_code}, {inserted} records previously confirmed. No automatic retry.")
        try:
            receipt = response.json()
            if (receipt.get("persisted") is not True or receipt.get("received_count") != len(batch)
                    or receipt.get("inserted_count") != len(batch)):
                raise ValueError
        except (ValueError, AttributeError):
            raise UploadError("Unexpected upload acknowledgement; check storage before retrying.") from None
        inserted += len(batch)
    return inserted


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true", help="Validate locally without network access")
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.batch_size <= 1000:
            raise ValueError("batch-size must be between 1 and 1000")
        records = read_records(args.file)
        if args.dry_run:
            print(json.dumps({"validated_count": len(records), "uploaded": False}))
            return 0
        parsed = urlsplit(args.base_url)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.scheme not in ("http", "https")
                or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))):
            raise ValueError("Use HTTPS for remote API URLs, or HTTP on localhost")
        token = os.getenv("INGEST_ACCESS_TOKEN", "")
        if not token:
            raise ValueError("Set INGEST_ACCESS_TOKEN to a dedicated edge account access token")
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            count = upload(records, client, args.base_url, token, args.batch_size)
        print(json.dumps({"inserted_count": count}))
        return 0
    except UploadError as exc:
        print(str(exc))
        return 1
    except (OSError, ValueError, ValidationError):
        # Do not echo record contents, access tokens, URLs or upstream bodies.
        print("Upload/validation failed. Check input, permissions and prior batch persistence; credentials and records were not printed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
