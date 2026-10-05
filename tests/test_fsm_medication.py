from app.db.models import Medication, SessionLocal
from app.dialogue.manager import handle_message


def test_add_medication_and_show_schedule():
    user = "patient-med-1"
    reply = handle_message(user, "хочу добавить лекарство")
    assert "лекарство" in reply.lower()

    reply = handle_message(user, "Парацетамол")
    assert "дозировка" in reply.lower()

    reply = handle_message(user, "500 мг")
    assert "время" in reply.lower()

    reply = handle_message(user, "в 9:00 и в 21:00")
    assert "Добавить" in reply

    reply = handle_message(user, "да")
    assert "Готово" in reply

    db = SessionLocal()
    try:
        med = db.query(Medication).filter(Medication.patient_id == user).first()
        assert med is not None
        assert med.time_list() == ["09:00", "21:00"]
    finally:
        db.close()

    reply = handle_message(user, "покажи мои лекарства")
    assert "парацетамол" in reply.lower()


def test_confirm_intake_without_pending_dose():
    user = "patient-med-2"
    reply = handle_message(user, "принял лекарство")
    assert "не вижу" in reply.lower()


def test_deny_restarts_medication_flow():
    user = "patient-med-3"
    handle_message(user, "добавь лекарство Ибупрофен")
    handle_message(user, "200 мг")
    handle_message(user, "в 8:00")
    reply = handle_message(user, "нет")
    assert "заново" in reply.lower()
