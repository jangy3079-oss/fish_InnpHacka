"""VC-08 확인: 질병 계열(R5·R8) 응답에 약품명·투약량이 없고, 1차 조치와 진단 요청이 나오는지 센다.

    python -m scripts.check_vc08                      # R5·R8 공통 1차 조치·진단 요청 문구 검사
    python -m scripts.check_vc08 --scan 알림문장.txt   # 알림 문장 파일(한 줄에 한 문장)도 같이 검사
    python -m scripts.check_vc08 --notifications --report data/out/vc08.json

검사 방법은 응답 생성 코드의 방어(`decision.first_aid.ensure_safe`)와 별개로 한 번 더 한다.
방어가 빠뜨린 단어를 이 검사가 잡을 수 있게 하기 위해서다. 사전(config/first_aid.yaml의
blocked_terms)과, 사전에 없는 숫자+단위 투약량 패턴을 둘 다 쓴다.
--notifications는 실제 notify 생성기를 합성 입력과 로컬 선택기로 실행한다. 외부 LLM/발송은 없다.
종료 코드: 0 = 기준 충족, 1 = 약품명·투약량이 나왔거나 1차 조치·진단 요청이 비어 있음.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unicodedata

from common.config_loader import ConfigLoader
from decision.first_aid import ensure_safe, reviewed_guide, UnsafeFirstAid

# 사전에 없는 단어를 잡기 위한 투약량 패턴 (숫자 + 단위)
DOSE = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|g|kg|ml|l|ℓ|ppm|μg|µg|㎎|㎏|㎖|밀리그램|그램|킬로그램|리터|피피엠|캡슐|정|알|회분)", re.I)
RULES = {"R5": "질병 의심 (disease_suspect)", "R8": "기생충 의심 (parasite_suspect)"}


def norm(text):
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", "".join(c for c in text if unicodedata.category(c) != "Cf")).casefold()


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from strings(v)


def scan(texts, terms):
    """각 문장에서 사전 단어·투약량 패턴이 나온 위치를 모은다. 단어 자체는 반환하지 않고 종류만 센다."""
    hits = []
    for t in texts:
        compact = norm(t)
        found = [kind for kind, words in terms.items() if any(norm(w) in compact for w in words)]
        if DOSE.search(unicodedata.normalize("NFKC", t)):
            found.append("dose_pattern")
        if found:
            hits.append((t[:30], found))
    return hits


def notification_cases(snapshot):
    """Exercise every reviewed advice candidate and fallback locally, without a provider."""
    from notify import NotificationGenerator

    def unavailable(candidates):
        raise RuntimeError("offline provider failure fixture")

    rows = []
    for rule in RULES:
        actions = reviewed_guide(rule, snapshot)["actions"]
        routes = [("template", None), ("provider_error", unavailable),
                  ("invalid_reply", lambda candidates: "invalid-json")]
        routes += [("candidate_" + str(i), lambda candidates, i=i: json.dumps(candidates[i]))
                   for i in range(len(actions))]
        for level in ("주의", "경고", "위험"):
            # Synthetic display fixtures, not a replay or a score-to-level accuracy claim.
            context = {"cage_id": 3, "ts": "2026-11-28T00:00:00+09:00",
                       "baseline_status": "ready", "level": level, "stress_score": 64.0,
                       "rule_id": rule, "evidence": {"temp_surface": 26.4, "activity_change": -.28}}
            for route, complete in routes:
                generator = NotificationGenerator(loader=SimpleNamespace(snapshot=lambda: snapshot),
                    llm=None if complete is None else SimpleNamespace(complete=complete))
                result = generator.generate(context)
                expected = "llm" if route.startswith("candidate_") else "template"
                if result.mode != expected or result.dispatch_status != "not_dispatched":
                    raise ValueError("notification validation route did not execute as expected")
                if route.startswith("candidate_"):
                    index = int(route.split("_")[1])
                    if [c.id for c in result.citations] != [actions[index]["id"]]:
                        raise ValueError("reviewed notification candidate was omitted or reordered")
                rows.append({"rule_id": rule, "level": level, "route": route,
                             "context": context, **result.model_dump(mode="json")})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="VC-08 약품명·투약량 확인")
    ap.add_argument("--scan", help="추가로 검사할 알림 문장 파일 (한 줄에 한 문장)")
    ap.add_argument("--notifications", action="store_true", help="R5/R8 알림·후보 선택·실패 fallback을 오프라인 검사")
    ap.add_argument("--report", type=Path, help="검사 결과 JSON (합성 입력과 실제 생성 문구 포함)")
    args = ap.parse_args(argv)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text('{"status":"processing"}\n', encoding="utf-8")

    snap = ConfigLoader().snapshot()
    cfg = snap["first_aid"]
    terms = {"category": list(cfg["blocked_terms"]["categories"]),
             "ingredient": list(cfg["blocked_terms"]["ingredients"])}
    print(f"설정 revision: {snap.revision}")
    failed = False
    total_texts = total_hits = 0
    guide_rows = []
    print("\n| 규칙 | 확인한 문장 수 | 약품명·투약량 | 1차 조치 | 보류 | 진단 요청 문구 | 방어(ensure_safe) |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for rid in RULES:
        guide = reviewed_guide(rid, snap)
        texts = list(strings(guide))
        hits = scan(texts, terms)
        try:
            ensure_safe(guide, snap)
            guard = "통과"
        except UnsafeFirstAid:
            guard = "차단"
        n_actions, n_withheld = len(guide["actions"]), len(guide["withheld_actions"])
        has_request = bool(guide.get("message"))
        total_texts += len(texts)
        total_hits += len(hits)
        if hits or n_actions == 0 or not has_request or guard != "통과":
            failed = True
        guide_rows.append({"rule_id": rid, "strings_checked": len(texts), "hits": len(hits),
                           "actions": n_actions, "withheld": n_withheld,
                           "diagnosis_request": has_request, "guard_passed": guard == "통과"})
        print(f"| {rid} {RULES[rid]} | {len(texts)} | {len(hits)} | {n_actions} | {n_withheld} | {'있음' if has_request else '없음'} | {guard} |")
        for where, kinds in hits:
            print(f"    ! 문장 \"{where}...\": {', '.join(kinds)}")

    notifications = notification_cases(snap) if args.notifications else []
    if notifications:
        hits = scan([r["message"] for r in notifications], terms)
        total_texts += len(notifications)
        total_hits += len(hits)
        failed = failed or bool(hits)
        print(f"\n오프라인 알림: {len(notifications)}개, 약품명·투약량 {len(hits)}건 (외부 LLM/발송 없음)")
    scan_count = scan_hits = 0
    if args.scan:
        with open(args.scan, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        hits = scan(lines, terms)
        scan_count, scan_hits = len(lines), len(hits)
        total_texts += len(lines)
        total_hits += len(hits)
        failed = failed or bool(hits)
        print(f"\n알림 문장 파일 {args.scan}: {len(lines)}문장, 약품명·투약량 {len(hits)}건")
        for where, kinds in hits:
            print(f"    ! 문장 \"{where}...\": {', '.join(kinds)}")
    elif not args.notifications:
        print("\n알림 문장은 검사하지 않았다 (--scan 없음). 알림 생성(#34)이 생기면 문장을 파일로 모아 --scan으로 검사한다.")

    print(f"\n합계: 확인한 문장 {total_texts}개, 약품명·투약량 {total_hits}건 → {'미달' if failed else '기준 충족'}")
    if args.report:
        report = {"status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                  "check_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "config_revision": snap.revision, "passed": not failed,
                  "counting_unit": "guide string fields plus complete notification messages and nonempty scan lines",
                  "guide_results": guide_rows, "notifications": notifications,
                  "scan_lines": scan_count, "scan_hits": scan_hits,
                  "total_texts": total_texts, "total_hits": total_hits,
                  "scope": "synthetic offline generation; no live LLM, dispatch, human study or field validation"}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
