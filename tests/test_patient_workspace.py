from datetime import timedelta

from app.db.models import SessionLocal
from app.product.models import Occurrence, PatientDocument
from tests.test_product import client, get_events, login, personal, today  # noqa: F401


def test_demo_import_is_idempotent_and_calendar_is_opt_in(client):
    login(client)
    first = client.post("/api/demo/prescription", json={})
    assert first.status_code == 200, first.text
    assert (
        client.post("/api/demo/prescription", json={}).json()["id"]
        == first.json()["id"]
    )
    assert len(client.get("/api/documents").json()) == 2
    assert client.get("/api/courses").json() == []
    assert (
        client.post(
            f"/api/plans/{first.json()['id']}/calendar", json={"source": "already_have"}
        ).status_code
        == 200
    )
    courses = client.get("/api/courses").json()
    assert len(courses) == 2
    assert [c["total"] for c in courses] == [14, 14]
    assert all(c["percent"] == 0 for c in courses)
    assert len(client.get("/api/documents").json()) == 2


def test_progress_uses_complete_course_not_materialized_month(client):
    login(client)
    start = today() - timedelta(days=1)
    end = start + timedelta(days=99)
    body = personal(
        start_date=start.isoformat(), end_date=end.isoformat(), times=["08:00", "20:00"]
    )
    sid = client.post("/api/calendar/series", json=body).json()["id"]
    event = get_events(client, start=start.isoformat())[0]
    client.post(f"/api/calendar/events/{event['id']}/status", json={"status": "taken"})
    course = client.get("/api/courses").json()[0]
    assert course["total"] == 200 and course["taken"] == 1
    with SessionLocal() as db:
        assert db.query(Occurrence).count() < 200
    client.post(
        f"/api/calendar/events/{event['id']}/status", json={"status": "pending"}
    )
    assert client.get("/api/courses").json()[0]["taken"] == 0


def test_unbounded_course_has_no_fake_percentage(client):
    login(client)
    client.post("/api/calendar/series", json=personal(end_date=None))
    course = client.get("/api/courses").json()[0]
    assert course["total"] is None and course["percent"] is None


def test_document_expiry_validation_and_patient_isolation(client):
    login(client)
    data = dict(
        title="Анализ",
        kind="analysis",
        issued_on=(today() - timedelta(days=5)).isoformat(),
        expires_on=today().isoformat(),
    )
    result = client.post("/api/documents", json=data)
    assert result.status_code == 201, result.text
    assert result.json()["days_left"] == 0 and result.json()["validity"] == "soon"
    did = result.json()["id"]
    data["expires_on"] = (today() - timedelta(days=1)).isoformat()
    assert (
        client.put(f"/api/documents/{did}", json=data).json()["validity"] == "expired"
    )
    data["expires_on"] = None
    assert (
        client.put(f"/api/documents/{did}", json=data).json()["validity"] == "unknown"
    )
    data["expires_on"] = (today() - timedelta(days=6)).isoformat()
    assert client.put(f"/api/documents/{did}", json=data).status_code == 422
    with SessionLocal() as db:
        db.get(PatientDocument, did).patient_id = "someone-else"
        db.commit()
    assert client.get("/api/documents").json() == []
    data["expires_on"] = None
    assert client.put(f"/api/documents/{did}", json=data).status_code == 404


def test_observation_persists(client):
    login(client)
    assert (
        client.post(
            "/api/observations", json={"text": "Сегодня чувствую себя лучше"}
        ).status_code
        == 201
    )
    assert (
        client.get("/api/observations").json()[0]["text"]
        == "Сегодня чувствую себя лучше"
    )


def test_only_analysis_and_certificates_are_available(client):
    login(client)
    with SessionLocal() as db:
        db.add(
            PatientDocument(
                patient_id="demo-patient",
                title="Старый рецепт",
                kind="prescription",
                issued_on=today(),
                content="Сохранённые данные",
            )
        )
        db.commit()
    assert client.get("/api/documents").json() == []
    assert (
        client.post(
            "/api/documents",
            json={
                "title": "Рецепт",
                "kind": "prescription",
                "issued_on": today().isoformat(),
            },
        ).status_code
        == 422
    )
