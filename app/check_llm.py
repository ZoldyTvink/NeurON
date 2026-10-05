import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from app.product.llm import ModelUnavailable, config, respond


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    print(f"Модель: {config()['model']}", flush=True)
    started = time.monotonic()
    try:
        answer = respond(
            [
                {
                    "role": "user",
                    "content": "Как мне записаться к врачу через этот сервис?",
                }
            ],
            {"today": datetime.now(ZoneInfo("Europe/Moscow")).isoformat(), "plans": []},
        )
    except ModelUnavailable as exc:
        print(f"Ошибка: {exc}")
        return 1
    print(json.dumps(answer.model_dump(), ensure_ascii=False, indent=2))
    print(f"Ответ получен за {time.monotonic() - started:.1f} с.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
