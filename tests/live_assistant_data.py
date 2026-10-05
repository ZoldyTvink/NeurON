import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    with tempfile.TemporaryDirectory(prefix="meditron-assistant-") as directory:
        os.environ.update(
            MEDITRON_DB_URL=f"sqlite:///{directory}/test.db",
            MEDITRON_LLM_PROVIDER="local",
            MEDITRON_LLM_FORMAT="ollama",
            MEDITRON_LLM_URL="http://127.0.0.1:11434/v1",
            MEDITRON_LLM_MODEL="meditron-gigachat",
            MEDITRON_LLM_TIMEOUT="90",
        )
        from datetime import datetime

        from app.db.models import SessionLocal, init_db
        from app.product.assistant import base_context, respond
        from app.product.calendar import make_series
        from app.product.models import Account, Occurrence

        init_db()
        with SessionLocal() as db:
            account = Account(
                id="synthetic",
                name="Тестовый пациент",
                role="patient",
                timezone="Europe/Moscow",
            )
            db.add(account)
            db.flush()
            day = (
                datetime.fromisoformat(base_context(account)["today"])
                .date()
                .isoformat()
            )
            schedule = dict(
                start_date=day,
                end_date=day,
                times=["08:00", "20:00"],
                weekdays=list(range(7)),
                timezone=account.timezone,
            )
            make_series(
                db,
                account.id,
                dict(
                    schedule,
                    title="Тестовый препарат А",
                    kind="medication",
                    dosage="1 тестовая единица",
                ),
            )
            make_series(
                db,
                account.id,
                dict(schedule, title="Тестовый завтрак", kind="food", dosage=""),
            )
            db.query(Occurrence).order_by(Occurrence.id).first().status = "taken"
            db.commit()
            history = []
            for message in [
                "Какие мне сегодня препараты принимать?",
                "Напомни мое расписание таблеток на сегодняшний день",
            ]:
                history.append({"role": "user", "content": message})
                started = time.monotonic()
                answer = respond(history, db, account)
                db.commit()
                print(
                    f"{time.monotonic() - started:.2f}s {message}\n{answer.model_dump_json()}\n",
                    flush=True,
                )
                assert "Тестовый препарат А" in answer.reply
                assert "Тестовый завтрак" not in answer.reply
                assert "08:00" in answer.reply and "20:00" in answer.reply
                assert "1 тестовая единица" in answer.reply
                assert "отмечено как принято" in answer.reply
                history.append({"role": "assistant", "content": answer.reply})
            history.append({"role": "user", "content": "А что я уже принял?"})
            started = time.monotonic()
            answer = respond(history, db, account)
            print(
                f"{time.monotonic() - started:.2f}s follow-up\n{answer.model_dump_json()}",
                flush=True,
            )
            assert "08:00" in answer.reply and "20:00" not in answer.reply
            history.append({"role": "assistant", "content": answer.reply})
            for message in [
                "Ясно, спасибо",
                "Ты мне очень помог, теперь всё понятно",
                "Что ты умеешь?",
            ]:
                conversation = history + [{"role": "user", "content": message}]
                answer = respond(conversation, db, account)
                print(f"{message}\n{answer.model_dump_json()}", flush=True)
                assert answer.action == "none"
                assert "Тестовый препарат А" not in answer.reply
                assert "Сохранённые записи к врачу:" not in answer.reply
                assert "Приёмы препаратов и витаминов" not in answer.reply
            print(
                "PASS: fast facts, semantic data lookup, contextual follow-up. No real patient data used.",
                flush=True,
            )


if __name__ == "__main__":
    main()
