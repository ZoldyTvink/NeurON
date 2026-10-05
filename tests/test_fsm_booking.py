import re

from app.db.models import Appointment, Doctor, DoctorSlot, SessionLocal
from app.dialogue.manager import handle_message


def _earliest_available_slot(specialty: str):
    db = SessionLocal()
    try:
        return (
            db.query(DoctorSlot)
            .join(Doctor)
            .filter(Doctor.specialty == specialty, DoctorSlot.is_booked.is_(False))
            .order_by(DoctorSlot.starts_at)
            .first()
        )
    finally:
        db.close()


def test_full_booking_flow_happy_path():
    user = "patient-1"
    reply = handle_message(user, "Хочу записаться к врачу")
    assert "специальность" in reply or "врачу" in reply.lower()

    reply = handle_message(user, "к терапевту")
    assert "дату" in reply

    slot = _earliest_available_slot("терапевт")
    assert slot is not None
    date_text = slot.starts_at.strftime("%d.%m.%Y")

    reply = handle_message(user, date_text)
    assert "свободно время" in reply

    times = re.findall(r"\d{2}:\d{2}", reply)
    assert times
    chosen_time = times[0]

    reply = handle_message(user, chosen_time)
    assert "Записать вас" in reply

    reply = handle_message(user, "да")
    assert "Готово" in reply

    db = SessionLocal()
    try:
        appointment = (
            db.query(Appointment).filter(Appointment.patient_id == user).first()
        )
        assert appointment is not None
        assert appointment.status == "booked"
    finally:
        db.close()


def test_cancel_mid_flow_resets_session():
    user = "patient-2"
    handle_message(user, "хочу записаться к врачу")
    reply = handle_message(user, "отмена")
    assert "отменил" in reply.lower()

    # A fresh booking should start from scratch, not resume the old flow.
    reply = handle_message(user, "к кардиологу")
    assert "дату" in reply or "врачу" in reply.lower()


def test_describe_symptom_infers_specialty():
    user = "patient-3"
    reply = handle_message(user, "у меня болит горло и кашель")
    assert "терапевт" in reply.lower()
