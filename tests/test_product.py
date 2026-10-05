import json
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.db.models import Appointment, DoctorSlot, SessionLocal
from app.main import app
from app.product import llm
from app.product.calendar import maintain
from app.product.models import (
    Account,
    CalendarSeries,
    Message,
    Occurrence,
    Reminder,
    utcnow,
)
from app.product.schemas import ChatAction

HEADERS = {"X-Meditron-Request": "1"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDITRON_LLM_PROVIDER", "local")
    monkeypatch.setenv("MEDITRON_CHAT_MODE", "generative")
    monkeypatch.setenv("MEDITRON_SCHEDULER", "0")
    monkeypatch.setenv("MEDITRON_DEMO", "1")
    monkeypatch.setenv("MEDITRON_LLM_PRELOAD", "0")
    monkeypatch.setenv("MEDITRON_LLM_FORMAT", "openai")
    monkeypatch.setenv("MEDITRON_VAPID_KEY", str(tmp_path / "vapid.pem"))
    monkeypatch.delenv("MEDITRON_LLM_URL", raising=False)
    with TestClient(app, headers=HEADERS) as c:
        yield c


def login(client, role="patient"):
    response = client.post("/api/demo/login", json={"role": role})
    assert response.status_code == 200, response.text


def today():
    return (
        utcnow()
        .replace(tzinfo=timezone.utc)
        .astimezone(ZoneInfo("Europe/Moscow"))
        .date()
    )


def prescription(**changes):
    return dict(
        name="Парацетамол",
        form="таблетки",
        strength="500 мг",
        dosage="0,5 таблетки",
        units_per_intake="0.5",
        start_date=today().isoformat(),
        end_date=(today() + timedelta(days=20)).isoformat(),
        times=["09:00", "21:00"],
        weekdays=list(range(7)),
        timezone="Europe/Moscow",
        **changes,
    )


def publish(client, items=None):
    # Simulate an external clinic import directly; there is no doctor cabinet.
    from app.product.api import plan_out
    from app.product.models import Cart, Plan, PlanItem

    login(client)
    with SessionLocal() as db:
        plan = Plan(
            patient_id="demo-patient",
            doctor_id="demo-doctor",
            title="Тестовый курс",
            notes="",
        )
        db.add(plan)
        db.flush()
        for item in items or [prescription()]:
            db.add(PlanItem(plan_id=plan.id, data_json=json.dumps(item)))
        db.add(
            Cart(
                plan_id=plan.id,
                patient_id="demo-patient",
                provider="eapteka_redirect",
                data_json="{}",
            )
        )
        db.commit()
        return plan_out(db, plan)


def personal(**changes):
    data = dict(
        title="Мой завтрак",
        kind="food",
        start_date=today().isoformat(),
        end_date=today().isoformat(),
        times=["09:00"],
        weekdays=list(range(7)),
        timezone="Europe/Moscow",
    )
    data.update(changes)
    return data


def get_events(client, start=None, end=None):
    day = start or today().isoformat()
    response = client.get("/api/calendar", params={"start": day, "end": end or day})
    assert response.status_code == 200, response.text
    return response.json()


def test_external_purchase_calendar_path_is_idempotent(client):
    plan = publish(client)
    cart = client.get(f"/api/carts/{plan['cart_id']}").json()
    assert cart["url"] == "https://www.eapteka.ru/"
    assert "price_kopecks" not in str(cart)
    assert (
        client.post(
            f"/api/plans/{plan['id']}/calendar", json={"source": "purchase"}
        ).status_code
        == 409
    )
    url = f"/api/carts/{plan['cart_id']}/confirm"
    assert client.post(url, json={}).json()["confirmation_source"] == "patient_reported"
    assert client.post(url, json={}).status_code == 200
    url = f"/api/plans/{plan['id']}/calendar"
    first = client.post(url, json={"source": "purchase"}).json()
    assert client.post(url, json={"source": "purchase"}).json() == first
    with SessionLocal() as db:
        assert db.query(CalendarSeries).count() == 1
        assert db.query(Occurrence).count() == 42
        assert db.query(CalendarSeries).one().dosage == "0,5 таблетки"
    assert len(get_events(client)) == 2


def test_unknown_drug_not_substituted_and_no_purchase_required(client):
    item = prescription()
    item["name"] = "Препарат вне каталога"
    item["dosage"] = "2,5 мг"
    plan = publish(client, [item])
    cart = client.get(f"/api/carts/{plan['cart_id']}").json()
    assert cart["lines"][0]["name"] == "Препарат вне каталога"
    assert (
        client.post(
            f"/api/plans/{plan['id']}/calendar", json={"source": "already_have"}
        ).status_code
        == 200
    )
    assert get_events(client)[0]["dosage"] == "2,5 мг"


def test_personal_food_needs_no_dosage_or_catalog(client):
    login(client)
    response = client.post("/api/calendar/series", json=personal())
    assert response.status_code == 201, response.text
    event = get_events(client)[0]
    assert (
        event["title"] == "Мой завтрак"
        and event["source"] == "personal"
        and event["dosage"] == ""
    )


def test_unrelated_patient_cannot_read_or_change_data(client):
    plan = publish(client)
    client.post(f"/api/plans/{plan['id']}/calendar", json={"source": "already_have"})
    event = get_events(client)[0]
    with SessionLocal() as db:
        row = db.get(Account, "demo-patient")
        row.id = "other-patient"
        db.flush()
        db.add(
            Account(
                id="demo-patient",
                role="patient",
                name="Другой пациент",
                doctor_id="demo-doctor",
                timezone="Europe/Moscow",
            )
        )
        db.query(CalendarSeries).update({"patient_id": "other-patient"})
        db.query(Occurrence).update({"patient_id": "other-patient"})
        from app.product.models import Cart, Plan

        db.query(Plan).update({"patient_id": "other-patient"})
        db.query(Cart).update({"patient_id": "other-patient"})
        db.commit()
    login(client)
    assert client.get("/api/plans").json() == []
    assert client.get(f"/api/carts/{plan['cart_id']}").status_code == 404
    assert (
        client.post(
            f"/api/calendar/events/{event['id']}/status", json={"status": "taken"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/plans/{plan['id']}/calendar", json={"source": "already_have"}
        ).status_code
        == 404
    )


def test_roles_and_csrf_are_enforced(client):
    assert client.get("/api/plans").status_code == 401
    login(client)
    assert client.get("/api/doctor/patients").status_code == 404
    assert client.post("/api/demo/login", json={"role": "doctor"}).status_code == 422
    assert (
        client.post("/api/logout", headers={"X-Meditron-Request": ""}).status_code
        == 403
    )


def test_doctor_messaging_removed(client):
    login(client)
    assert client.get("/api/questions").status_code == 404
    assert (
        client.post("/api/questions", json={"text": "Вопрос врачу"}).status_code == 404
    )


def due_event(client, title="Личное событие"):
    local = (
        (utcnow() - timedelta(minutes=2))
        .replace(tzinfo=timezone.utc)
        .astimezone(ZoneInfo("Europe/Moscow"))
    )
    body = personal(
        title=title,
        start_date=local.date().isoformat(),
        end_date=local.date().isoformat(),
        times=[local.strftime("%H:%M")],
    )
    assert client.post("/api/calendar/series", json=body).status_code == 201
    return get_events(client, start=local.date().isoformat())[-1]


def test_reminders_recover_and_marks_target_specific_event(client):
    login(client)
    first = due_event(client, "Первый препарат")
    second = due_event(client, "Второй препарат")
    reminders = client.get("/api/reminders").json()
    assert len(reminders) == 2
    assert len(client.get("/api/reminders").json()) == 2
    assert (
        client.post(
            f"/api/calendar/events/{first['id']}/status", json={"status": "taken"}
        ).status_code
        == 200
    )
    remaining = client.get("/api/reminders").json()
    assert len(remaining) == 1 and remaining[0]["event"]["id"] == second["id"]
    client.post(
        f"/api/calendar/events/{second['id']}/status", json={"status": "snoozed"}
    )
    assert client.get("/api/reminders").json() == []
    with SessionLocal() as db:
        assert db.query(Reminder).count() == 2
        r = db.query(Reminder).filter_by(occurrence_id=second["id"]).one()
        r.due_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert len(client.get("/api/reminders").json()) == 1
    client.post(
        f"/api/calendar/events/{second['id']}/status", json={"status": "skipped"}
    )
    with SessionLocal() as db:
        assert db.get(Occurrence, first["id"]).status == "taken"
        assert db.get(Occurrence, second["id"]).status == "skipped"
        assert db.get(Occurrence, second["id"]).completed_at is None


def test_future_events_not_marked_taken(client):
    login(client)
    tomorrow = (today() + timedelta(days=1)).isoformat()
    client.post(
        "/api/calendar/series", json=personal(start_date=tomorrow, end_date=tomorrow)
    )
    event = get_events(client, start=tomorrow)[0]
    assert (
        client.post(
            f"/api/calendar/events/{event['id']}/status", json={"status": "taken"}
        ).status_code
        == 409
    )


def test_end_date_and_stop_preserve_history(client):
    login(client)
    event = due_event(client)
    client.post(f"/api/calendar/events/{event['id']}/status", json={"status": "taken"})
    assert (
        client.delete(f"/api/calendar/series/{event['series_id']}").status_code == 200
    )
    with SessionLocal() as db:
        maintain(db)
        db.commit()
        assert db.get(Occurrence, event["id"]).status == "taken"
        assert db.query(CalendarSeries).one().active is False
    assert get_events(client, start=(today() + timedelta(days=1)).isoformat()) == []


@pytest.mark.parametrize(
    "change",
    [
        {"times": ["25:00"]},
        {"timezone": "No/SuchZone"},
        {"weekdays": []},
        {"end_date": "2001-01-01"},
        {"title": "  "},
    ],
)
def test_invalid_personal_schedule(client, change):
    login(client)
    assert (
        client.post("/api/calendar/series", json=personal(**change)).status_code == 422
    )


def test_no_llm_is_explicit_and_does_not_fallback(client):
    login(client)
    response = client.post("/api/chat", json={"message": "Запиши меня завтра"})
    assert response.status_code == 503
    with SessionLocal() as db:
        assert db.query(Appointment).count() == 0
        assert db.query(Message).count() == 0


def test_llm_has_context_and_only_proposes_actions(client, monkeypatch):
    plan = publish(client)
    captured = []

    def fake(history, context):
        captured.append((history, context))
        return ChatAction(
            reply="Выберите подходящее время в расписании.", action="booking"
        )

    monkeypatch.setattr(llm, "respond", fake)
    assert (
        client.post("/api/chat", json={"message": "Хочу к врачу завтра"}).status_code
        == 200
    )
    client.post("/api/chat", json={"message": "А лучше послезавтра"})
    assert (
        "plans" not in captured[0][1]
    )  # Patient data are read on demand through tools.
    assert "queries" in captured[0][1]["limits"]
    assert any(m["content"] == "Хочу к врачу завтра" for m in captured[1][0])
    assert captured[0][1]["pharmacy"]["name"] == "ЕАПТЕКА"
    assert captured[0][1]["pharmacy"]["real_checkout_available"] is False
    saved = client.get("/api/chat/history").json()
    assert [m["action"] for m in saved if m["role"] == "assistant"][-2:] == [
        "booking",
        "booking",
    ]
    assert client.post("/api/demo/login", json={"role": "doctor"}).status_code == 422
    with SessionLocal() as db:
        assert db.query(Appointment).count() == 0


def test_llm_rejects_invalid_structured_response(client, monkeypatch):
    import httpx

    monkeypatch.setenv("MEDITRON_LLM_URL", "http://localhost:8001/v1")

    def fake(*args, **kwargs):
        assert kwargs["json"]["response_format"]["type"] == "json_schema"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"reply":"done","action":"delete_all"}'}}
                ]
            },
            request=httpx.Request("POST", "http://localhost"),
        )

    monkeypatch.setattr(llm.httpx, "post", fake)
    login(client)
    assert client.post("/api/chat", json={"message": "привет"}).status_code == 503


def test_booking_conflict_and_owned_cancellation(client):
    login(client)
    slot = client.get("/api/booking/slots").json()[0]
    first = client.post("/api/booking", json={"slot_id": slot["id"]})
    assert first.status_code == 201
    assert client.post("/api/booking", json={"slot_id": slot["id"]}).status_code == 409
    assert client.delete("/api/booking/" + str(first.json()["id"])).status_code == 200
    with SessionLocal() as db:
        assert db.get(DoctorSlot, slot["id"]).is_booked is False


def test_push_blocks_arbitrary_endpoints(client):
    login(client)
    assert (
        client.post(
            "/api/push/subscribe",
            json={"endpoint": "http://127.0.0.1:1234/secrets", "keys": {}},
        ).status_code
        == 422
    )
    key = client.get("/api/push/key")
    assert key.status_code == 200 and len(key.json()["public_key"]) == 87


def test_push_retry_and_idempotence(client, monkeypatch):
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from requests import ConnectionError

    from app.product import push

    login(client)
    client.get("/api/push/key")
    public = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    encode = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    subscription = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/test",
        "keys": {"auth": encode(b"1234567890123456"), "p256dh": encode(public)},
    }
    assert client.post("/api/push/subscribe", json=subscription).status_code == 200
    due_event(client)
    client.get("/api/reminders")
    calls = []

    def fake(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ConnectionError("offline")
        return type("Response", (), {"status_code": 201})()

    monkeypatch.setattr(push, "webpush", fake)
    with SessionLocal() as db:
        push.send_due(db)
        row = db.query(push.PushDelivery).one()
        assert row.attempts == 1 and row.sent_at is None
        push.send_due(db)
        assert len(calls) == 1
        row.next_attempt_at = utcnow() - timedelta(seconds=1)
        db.commit()
        push.send_due(db)
        assert db.query(push.PushDelivery).one().sent_at is not None
        push.send_due(db)
        assert len(calls) == 2


def test_unexpected_chat_error_returns_json_without_saving_message(client, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic server failure")

    monkeypatch.setattr(llm, "respond", fail)
    with TestClient(app, headers=HEADERS, raise_server_exceptions=False) as browser:
        login(browser)
        response = browser.post("/api/chat", json={"message": "Тестовый вопрос"})
        assert response.status_code == 500
        assert response.headers["content-type"].startswith("application/json")
        assert "попробуйте ещё раз" in response.json()["detail"]
        assert "synthetic" not in response.text
        assert browser.get("/api/chat/history").json() == []


def test_sqlite_write_lock_returns_actionable_json(client, monkeypatch):
    import sqlite3

    from app.db.models import engine

    monkeypatch.setattr(
        llm,
        "respond",
        lambda *args, **kwargs: ChatAction(action="none", reply="Тестовый ответ"),
    )
    login(client)
    with sqlite3.connect(engine.url.database) as editor:
        editor.execute("BEGIN IMMEDIATE")
        try:
            response = client.post("/api/chat", json={"message": "хочу справку"})
            assert response.status_code == 503
            assert "База данных занята" in response.json()["detail"]
            assert client.get("/api/chat/history").json() == []
        finally:
            editor.rollback()
    response = client.post("/api/chat", json={"message": "хочу справку"})
    assert response.status_code == 200
    assert len(client.get("/api/chat/history").json()) == 2
