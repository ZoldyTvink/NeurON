"""Opt-in comparison on synthetic fixtures. No database imports or patient data."""

import argparse
import hashlib
import json
import math
import os
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from app.product import llm
from app.product.chat_prompt import EXAMPLES, SYSTEM_PROMPT
from app.product.router_nlu import ROUTER_EXAMPLES, ROUTER_PROMPT
from app.product.schemas import ChatAction

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "app/evals/chat_cases.json"
CONTEXT = {
    "today": "2026-10-04T12:00:00+03:00",
    "plans": [],
    "calendar_events": [],
    "appointments": [],
    "pharmacy": {"name": "ЕАПТЕКА", "mode": "demo", "real_checkout_available": False},
    "limits": "Синтетический тест. Назначений, цен и записей в контексте нет.",
}


def score(case, answer):
    text = answer.reply.casefold()
    action_ok = answer.action in case["actions"]
    checks_ok = all(
        any(phrase.casefold() in text for phrase in group)
        for group in case.get("contains_any", [])
    )
    return {
        "action_ok": action_ok,
        "text_checks_ok": checks_ok,
        "passed": action_ok and checks_ok,
    }


def run_case(job):
    repeat, case = job
    started = time.perf_counter()
    row = {"case": case["id"], "repeat": repeat, "expected_actions": case["actions"]}
    try:
        answer = llm.respond(
            case.get("history", []) + [{"role": "user", "content": case["message"]}],
            CONTEXT,
        )
        row.update(
            valid_response=True, answer=answer.model_dump(), **score(case, answer)
        )
    except llm.ModelUnavailable as exc:
        row.update(
            valid_response=False,
            action_ok=False,
            text_checks_ok=False,
            passed=False,
            error=str(exc),
        )
    row["seconds"] = round(time.perf_counter() - started, 3)
    row["correct_within_5s"] = row["passed"] and row["seconds"] <= 5
    print(
        f"{row['case']} #{repeat}: {row['seconds']:.2f}s; {'PASS' if row['passed'] else 'FAIL'}",
        flush=True,
    )
    return row


def summarize(rows):
    timings = sorted(r["seconds"] for r in rows if r["valid_response"])
    count = len(rows)
    return {
        "requests": count,
        "valid_responses": len(timings),
        "action_correct": sum(r["action_ok"] for r in rows),
        "checks_passed": sum(r["passed"] for r in rows),
        "correct_within_5s": sum(r["correct_within_5s"] for r in rows),
        "full_response_p50_seconds": statistics.median(timings) if timings else None,
        "full_response_p95_seconds": timings[math.ceil(0.95 * len(timings)) - 1]
        if timings
        else None,
        "latency_sample": "valid responses only; nearest-rank p95; failures counted separately",
        "quality_scope": "action + keyword smoke checks; manual review of answers still required",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider", choices=["local", "gigachat", "both"], default="local"
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".runtime/llm-comparison.json"
    )
    args = parser.parse_args(argv)
    if not 1 <= args.repeats <= 50 or not 1 <= args.concurrency <= 8:
        parser.error("repeats: 1..50; concurrency: 1..8")
    load_dotenv(ROOT / ".env")
    cases = json.loads(CASES_PATH.read_text())
    digest = hashlib.sha256(
        json.dumps(
            [
                SYSTEM_PROMPT,
                EXAMPLES,
                ROUTER_PROMPT,
                ROUTER_EXAMPLES,
                ChatAction.model_json_schema(),
                cases,
                CONTEXT,
            ],
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    report = {
        "created_at": datetime.now(ZoneInfo("Europe/Moscow")).isoformat(),
        "synthetic_only": True,
        "fixture_and_prompt_sha256": digest,
        "repeats": args.repeats,
        "concurrency": args.concurrency,
        "note": "Full validated response, not first token. No warmup; first request included. Small samples are exploratory.",
        "providers": {},
    }
    providers = ["local", "gigachat"] if args.provider == "both" else [args.provider]
    jobs = [(repeat + 1, case) for repeat in range(args.repeats) for case in cases]
    random.Random(42).shuffle(jobs)
    previous = os.environ.get("MEDITRON_LLM_PROVIDER")
    incomplete = False
    failed = False
    try:
        for provider in providers:
            os.environ["MEDITRON_LLM_PROVIDER"] = provider
            configuration = llm.config()
            if not configuration["configured"]:
                report["providers"][provider] = {
                    **configuration,
                    "status": "not_configured",
                }
                incomplete = True
                print(f"{provider}: not configured; skipped", flush=True)
                continue
            print(
                f"{provider}: {configuration['model']}, {len(jobs)} requests, concurrency={args.concurrency}",
                flush=True,
            )
            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                rows = list(pool.map(run_case, jobs))
            report["providers"][provider] = {
                **configuration,
                "status": "measured",
                "wall_seconds": round(time.perf_counter() - start, 3),
                "summary": summarize(rows),
                "results": rows,
            }
            failed |= any(not row["passed"] for row in rows)
            print(
                json.dumps(
                    report["providers"][provider]["summary"],
                    ensure_ascii=False,
                    indent=2,
                ),
                flush=True,
            )
    finally:
        if previous is None:
            os.environ.pop("MEDITRON_LLM_PROVIDER", None)
        else:
            os.environ["MEDITRON_LLM_PROVIDER"] = previous
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Report: {args.output}")
    return 2 if incomplete else 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
