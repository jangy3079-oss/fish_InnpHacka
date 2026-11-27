"""Local cause_estimate evidence output; remote DB writes are a separate integration."""

import json
from pathlib import Path


def save_jsonl(results, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for result in results:
            for estimate in result.estimates:
                stream.write(json.dumps(estimate.to_dict(), ensure_ascii=False, allow_nan=False) + "\n")
    return path
