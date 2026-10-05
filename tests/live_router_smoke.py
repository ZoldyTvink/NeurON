"""Opt-in local NLU evaluation. Uses synthetic text only, no database or cloud."""

import hashlib
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

from app.product.llm import ModelUnavailable
from app.product.router_nlu import ROUTER_EXAMPLES, ROUTER_PROMPT, classify


def main():
    load_dotenv(ROOT / ".env")
    if os.getenv("MEDITRON_LLM_PROVIDER", "local") != "local":
        raise SystemExit("Эта проверка предназначена только для локальной Ollama.")
    cases = json.loads((ROOT / "app/evals/router_cases.json").read_text())
    rows = []
    for case in cases:
        start = time.perf_counter()
        try:
            decision = classify(
                case.get("history", []) + [{"role": "user", "content": case["message"]}]
            )
            row = {
                **case,
                "route": decision.route,
                "passed": decision.route in case["expected"],
            }
        except ModelUnavailable as exc:
            row = {**case, "passed": False, "error": str(exc)}
        row["seconds"] = round(time.perf_counter() - start, 3)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    output = ROOT / ".runtime/router-quality.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    timings = sorted(row["seconds"] for row in rows)
    summary = {
        "checked_at": datetime.now(ZoneInfo("Europe/Moscow")).isoformat(),
        "model": os.getenv("MEDITRON_LLM_MODEL", "meditron-gigachat"),
        "mode": "router",
        "synthetic_only": True,
        "requests": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "median_seconds": statistics.median(timings),
        "p95_seconds": timings[math.ceil(0.95 * len(timings)) - 1],
        "maximum_seconds": max(timings),
        "prompt_and_cases_sha256": hashlib.sha256(
            json.dumps(
                [ROUTER_PROMPT, ROUTER_EXAMPLES, cases],
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest(),
        "note": "Known regression cases; prompts tuned against failures. Not an independent accuracy estimate. First request included; model may already be warm.",
    }
    output.with_name("router-quality-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Passed {sum(r['passed'] for r in rows)}/{len(rows)}. Report: {output}")
    return 0 if all(r["passed"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
