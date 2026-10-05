from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.db.models import SessionLocal
from app.product import assistant, llm
from app.product.api import display_message_content
from app.product.assistant import (
    DataQuery,
    ModelTurn,
    fast_request,
    is_social_turn,
    read_data,
)
from app.product.calendar import make_series
from app.product.models import Account, Occurrence, PatientDocument
from app.product.schemas import ChatAction
from tests.test_product import client, login, personal, today  # noqa: F401


def test_legacy_assistant_reply_hides_timezone_without_changing_user_text():
    old = "Приёмы: 8. Часовой пояс: Europe/Moscow.\nРазных препаратов: 5."
    assert (
        display_message_content("assistant", old) == "Приёмы: 8.\nРазных препаратов: 5."
    )
    assert display_message_content("user", old) == old


@pytest.mark.parametrize(
    "message",
    [
        "Не открывай календарь",
        "Открой календарь и запиши к врачу",
        "Что принимать сегодня, если стало хуже?",
        "Мне плохо, хочу записаться к врачу",
        "А завтра?",
        "Когда мне принимать его?",
        "Что принимать сегодня и завтра?",
        "Можно ли принять двойную дозу?",
    ],
)
def test_non_exact_messages_require_contextual_model(message):
    assert fast_request(message, date(2026, 10, 5)) is None


@pytest.mark.parametrize(
    "message",
    [
        "Спасибо",
        "ясно, спасибо",
        "Понятно. Благодарю!",
        "Нет, спасибо",
        "До свидания",
        "Добрый день",
    ],
)
def test_pure_social_turns_bypass_patient_data_planner(message):
    assert is_social_turn(message)


def test_request_with_thanks_is_not_mistaken_for_pure_social_turn():
    assert not is_social_turn("Спасибо, а что мне принимать завтра?")
    assert not is_social_turn("Да")
    assert not is_social_turn("Нет")


def test_social_turn_is_answered_by_model_without_data_list(client, monkeypatch):
    login(client)
    client.post("/api/chat", json={"message": "Что я сегодня уже принял?"})
    calls = []

    def answer(history, context):
        calls.append(context)
        assert context["tools_complete"] is True
        assert "tool_results" not in context
        assert history[0]["content"] == "Что я сегодня уже принял?"
        return ChatAction(action="none", reply="Рад помочь, обращайтесь!")

    monkeypatch.setattr(llm, "respond", answer)
    response = client.post("/api/chat", json={"message": "Ясно, спасибо"})
    assert response.status_code == 200
    assert response.json() == {"action": "none", "reply": "Рад помочь, обращайтесь!"}
    assert len(calls) == 1


def test_stale_list_plan_still_gets_conversational_model_answer(client, monkeypatch):
    login(client)
    calls = []

    def model(history, context):
        calls.append(context)
        if not context.get("tools_complete"):
            return ModelTurn(
                action="none",
                reply="Проверю",
                answer_mode="list",
                queries=[
                    DataQuery(topic="schedule", scope="medications"),
                    DataQuery(topic="appointments"),
                ],
            )
        assert len(context["tool_results"]) == 2
        assert history[-1]["content"] == "Ты мне очень помог, теперь всё понятно"
        return ChatAction(action="none", reply="Рад, что смог помочь!")

    monkeypatch.setattr(llm, "respond", model)
    result = client.post(
        "/api/chat", json={"message": "Ты мне очень помог, теперь всё понятно"}
    )
    assert result.status_code == 200
    assert result.json() == {"action": "none", "reply": "Рад, что смог помочь!"}
    assert len(calls) == 2


@pytest.mark.parametrize(
    "message,status,offset",
    [
        ("Какие препараты мне сегодня принимать?", "all", 0),
        ("Какие мне сегодня препараты принимать?", "all", 0),
        ("Сколько препаратов мне нужно принять сегодня?", "all", 0),
        ("Что мне принимать завтра?", "all", 1),
        ("Что я сегодня уже принял?", "taken", 0),
        ("Что осталось принять вчера?", "remaining", -1),
    ],
)
def test_fast_schedule_is_a_structured_read(message, status, offset):
    query = fast_request(message, date(2026, 10, 5))
    assert query.topic == "schedule"
    assert query.status == status
    assert query.start == date(2026, 10, 5) + timedelta(days=offset)


def test_data_query_cannot_choose_patient_or_execute_write():
    with pytest.raises(ValidationError):
        DataQuery(topic="schedule", patient_id="someone-else")
    with pytest.raises(ValidationError):
        DataQuery(topic="purchase")


def test_calendar_answer_complete_scoped_and_offline(client, monkeypatch):
    login(client)
    monkeypatch.setattr(
        llm,
        "respond",
        lambda *args: pytest.fail("Fast factual answer must not call model"),
    )
    # More than the previous 24-row cap, including the whole local day.
    for i in range(5):
        body = personal(
            title=f"Препарат {i}",
            kind="medication",
            dosage="сохранённая доза",
            times=["00:05", "08:00", "12:00", "16:00", "20:00", "23:55"],
        )
        assert client.post("/api/calendar/series", json=body).status_code == 201
    client.post("/api/calendar/series", json=personal(title="Завтрак"))
    with SessionLocal() as db:
        db.add(Account(id="other", name="Другой пациент", role="patient"))
        db.flush()
        make_series(db, "other", personal(title="Чужой препарат", kind="medication"))
        events = (
            db.query(Occurrence)
            .filter_by(patient_id="demo-patient")
            .order_by(Occurrence.scheduled_at)
            .all()
        )
        events[0].status = "taken"
        events[1].status = "skipped"
        events[2].status = "snoozed"
        db.commit()
    result = client.post(
        "/api/chat", json={"message": "Какие препараты мне сегодня принимать?"}
    )
    assert result.status_code == 200, result.text
    answer = result.json()
    assert answer["action"] == "calendar"
    assert "за " + today().isoformat() + ": 30" in answer["reply"]
    assert answer["reply"].count("• ") == 30
    assert "00:05" in answer["reply"] and "23:55" in answer["reply"]
    assert "Часовой пояс" not in answer["reply"]
    assert "Europe/Moscow" not in answer["reply"]
    assert "Чужой препарат" not in answer["reply"] and "Завтрак" not in answer["reply"]
    assert "сохранённая доза" in answer["reply"]
    assert "отмечено как принято" in answer["reply"]
    assert "отмечено как пропущено" in answer["reply"]
    remaining = client.post(
        "/api/chat", json={"message": "Что осталось принять сегодня?"}
    ).json()["reply"]
    assert ": 28." in remaining
    assert "напоминание отложено" in remaining
    assert "отмечено как принято" not in remaining
    assert len(client.get("/api/chat/history").json()) == 4


def test_empty_schedule_is_not_a_claim_of_no_treatment(client):
    login(client)
    result = client.post("/api/chat", json={"message": "Что принимать завтра?"})
    assert result.status_code == 200
    assert "Подходящих записей в календаре нет" in result.json()["reply"]
    assert "не означает" in result.json()["reply"]


def test_boundary_timezone_and_cancelled_events(client):
    login(client)
    with SessionLocal() as db:
        account = db.get(Account, "demo-patient")
        account.timezone = "Asia/Vladivostok"
        series = make_series(
            db,
            account.id,
            personal(
                title="Витамин",
                kind="vitamin",
                times=["00:01", "23:59"],
                timezone=account.timezone,
            ),
        )
        data = read_data(
            db,
            account,
            DataQuery(
                topic="schedule", start=today(), end=today(), scope="medications"
            ),
        )
        assert data["total"] == 2
        assert data["items"][0]["display_time"].endswith("00:01")
        assert data["items"][1]["display_time"].endswith("23:59")
        db.query(Occurrence).filter_by(series_id=series.id).first().status = "cancelled"
        db.flush()
        assert (
            read_data(
                db, account, DataQuery(topic="schedule", start=today(), end=today())
            )["total"]
            == 1
        )


def test_model_can_read_actual_data_and_retains_followup_history(client, monkeypatch):
    login(client)
    client.post(
        "/api/calendar/series",
        json=personal(title="Витамин Д", kind="vitamin", dosage="из записи"),
    )
    calls = []

    def fake(history, context):
        calls.append((history, context))
        if not context.get("tools_complete"):
            assert (
                "plans" not in context
            )  # No invented empty patient record or bulk data dump.
            return ModelTurn(
                action="none",
                reply="Проверю.",
                queries=[DataQuery(topic="schedule", scope="medications")],
            )
        data = context["tool_results"][0]
        assert data["items"][0]["title"] == "Витамин Д"
        assert data["items"][0]["dosage"] == "из записи"
        return ModelTurn(
            action="calendar",
            reply="В календаре: Витамин Д, 09:00, дозировка: из записи.",
        )

    monkeypatch.setattr(llm, "respond", fake)
    response = client.post(
        "/api/chat", json={"message": "Напомни, что у меня запланировано из витаминов?"}
    )
    assert response.status_code == 200 and "Витамин Д" in response.json()["reply"]
    client.post("/api/chat", json={"message": "А что из этого я уже принял?"})
    assert any("витаминов" in row["content"] for row in calls[-1][0])
    saved = client.get("/api/chat/history").json()
    assert len(saved) == 4
    assert all("Проверю." != row["content"] for row in saved)
    with SessionLocal() as db:
        assert all(row.status == "pending" for row in db.query(Occurrence).all())


def test_document_expiry_and_private_data(client):
    login(client)
    with SessionLocal() as db:
        for pid, title in [("demo-patient", "Моя справка"), ("other", "Чужая справка")]:
            db.add(
                PatientDocument(
                    patient_id=pid,
                    title=title,
                    kind="certificate",
                    issued_on=today() - timedelta(days=10),
                    expires_on=today() - timedelta(days=1),
                )
            )
        db.commit()
    response = client.post(
        "/api/chat", json={"message": "Какие у меня документы?"}
    ).json()
    assert response["action"] == "documents"
    assert "Моя справка" in response["reply"] and "срок истёк" in response["reply"]
    assert "Чужая" not in response["reply"]


def test_tools_are_bounded_and_invalid_period_is_explicit(client, monkeypatch):
    login(client)
    calls = []

    def fake(history, context):
        calls.append(context)
        return ModelTurn(
            action="none",
            reply="Читаю",
            queries=[DataQuery(topic="schedule", start=date(2000, 1, 1))],
        )

    monkeypatch.setattr(llm, "respond", fake)
    response = client.post(
        "/api/chat", json={"message": "Покажи очень старое расписание"}
    )
    assert response.status_code == 200
    assert len(calls) == 2
    assert "error" in calls[-1]["tool_results"][0]
    assert "Укажите" in response.json()["reply"]


def test_model_input_truncation_is_explicit():
    result = assistant.bounded_result(
        {
            "topic": "documents",
            "items": [{"content": "a" * 20000}],
            "total": 1,
            "truncated": False,
        }
    )
    assert result["truncated"] is True
    assert result["total"] == 1 and result["items"] == []


def test_semantic_list_is_answered_by_model_with_server_facts(client, monkeypatch):
    login(client)
    client.post(
        "/api/calendar/series",
        json=personal(title="Витамин", kind="vitamin", dosage="сохранённая дозировка"),
    )
    calls = []

    def plan(history, context):
        calls.append(context)
        if context.get("tools_complete"):
            assert len(context["tool_results"]) == 1
            assert (
                context["tool_results"][0]["items"][0]["dosage"]
                == "сохранённая дозировка"
            )
            return ChatAction(
                action="calendar",
                reply="• Витамин, сохранённая дозировка; приём не отмечен.",
            )
        assert not context.get("tools_complete")
        return ModelTurn(
            action="none",
            reply="Проверю",
            answer_mode="list",
            queries=[
                DataQuery(topic="schedule", scope="medications"),
                DataQuery(topic="schedule", scope="medications", status="taken"),
                DataQuery(topic="schedule", scope="medications"),
            ],
        )

    monkeypatch.setattr(llm, "respond", plan)
    response = client.post(
        "/api/chat", json={"message": "Напомни расписание витаминов, пожалуйста"}
    )
    assert response.status_code == 200
    assert len(calls) == 2
    assert response.json()["action"] == "calendar"
    assert response.json()["reply"].count("• ") == 1
    assert "сохранённая дозировка" in response.json()["reply"]
    assert "приём не отмечен" in response.json()["reply"]


def test_followup_keeps_primary_status_selection_for_same_day(client, monkeypatch):
    login(client)
    client.post(
        "/api/calendar/series",
        json=personal(title="Препарат", kind="medication", times=["08:00", "20:00"]),
    )
    client.post("/api/calendar/series", json=personal(title="Еда", kind="food"))
    with SessionLocal() as db:
        db.query(Occurrence).order_by(Occurrence.id).first().status = "taken"
        db.commit()

    def model(history, context):
        if context.get("tools_complete"):
            data = context["tool_results"][0]
            assert data["total"] == 1
            assert data["items"][0]["title"] == "Препарат"
            return assistant.factual_reply(data)
        return ModelTurn(
            action="none",
            reply="Проверю",
            answer_mode="list",
            queries=[
                DataQuery(
                    topic="schedule", scope="medications", status="taken", start=today()
                ),
                DataQuery(topic="schedule", scope="medications"),
                DataQuery(topic="schedule", scope="all"),
            ],
        )

    monkeypatch.setattr(llm, "respond", model)
    response = client.post("/api/chat", json={"message": "А что я уже принял?"}).json()
    assert response["action"] == "calendar"
    assert response["reply"].count("• ") == 1
    assert "08:00" in response["reply"] and "20:00" not in response["reply"]
    assert "Еда" not in response["reply"]
