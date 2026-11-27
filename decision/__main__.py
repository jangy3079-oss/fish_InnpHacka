"""python -m decision --input request.json [--config-dir config] [--output result.json]."""

import argparse
import json
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from common.config_loader import ConfigError, ConfigLoader, DEFAULT_DIRECTORY
from .calculator import LossCalculator


def main(argv=None):
    parser = argparse.ArgumentParser(description="그룹별 손익 계산과 설정된 긴급방류 요건 확인")
    parser.add_argument("--input", required=True)
    parser.add_argument("--config-dir", default=str(DEFAULT_DIRECTORY))
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        request = json.loads(Path(args.input).read_text(encoding="utf-8-sig"), parse_float=Decimal)
        result = LossCalculator(ConfigLoader(args.config_dir)).compare(request)
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            Path(args.output).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
    except (OSError, ValueError, ConfigError, ValidationError):
        parser.exit(2, "손익 입력 또는 설정을 확인하세요. 결과를 생성하지 않았습니다.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
