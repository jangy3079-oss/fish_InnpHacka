"""Render a local context: python -m notify context.json [--llm]. No DB writes or dispatch."""

import argparse
import json
import os
import sys
from pathlib import Path

from .generator import NotificationGenerator
from .llm import ChatCompletionClient
from .sources import ROOT, load_policy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("context", type=Path)
    parser.add_argument("--recommendation", type=Path, help="existing LossCalculator JSON result")
    parser.add_argument("--policy", type=Path, default=ROOT / "config/notify.yaml")
    parser.add_argument("--llm", action="store_true", help="explicitly enable the configured HTTP provider")
    args = parser.parse_args()
    try:
        llm = None
        if args.llm:
            policy, _ = load_policy(args.policy)
            llm = ChatCompletionClient(os.getenv("NOTIFY_LLM_URL"), os.getenv("NOTIFY_LLM_MODEL"),
                os.getenv("NOTIFY_LLM_API_KEY"), timeout=policy.llm_timeout_seconds,
                max_tokens=policy.llm_max_completion_tokens)
        context = json.loads(args.context.read_text(encoding="utf-8"))
        recommendation = json.loads(args.recommendation.read_text(encoding="utf-8")) if args.recommendation else None
        result = NotificationGenerator(policy_path=args.policy, llm=llm).generate(context, recommendation=recommendation)
        print(result.model_dump_json())
    except (OSError, ValueError) as exc:
        # Input and config errors report no raw provider output or credentials.
        print(f"notify: invalid input or configuration ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
