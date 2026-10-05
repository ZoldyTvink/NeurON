from sqlalchemy.orm import Session

from app.db import queries
from app.db.models import DoctorSlot
from app.dialogue.fsm import DialogState
from app.nlu.entities import (
    SPECIALTY_SYNONYMS,
    extract_date,
    infer_specialty_from_symptom,
)

SPECIALTY_LIST = ", ".join(sorted(SPECIALTY_SYNONYMS.keys()))


def _slots_summary(slots: list[DoctorSlot]) -> str:
    return ", ".join(s.starts_at.strftime("%H:%M") for s in slots)


def start(db: Session, state: DialogState, text: str, entities: dict) -> str:
    state.scenario = "booking"
    state.slots = {}

    specialty = entities.get("specialty")
    if specialty:
        state.slots["specialty"] = specialty
        state.state = "ask_date"
        return f"Понял, запишу вас к специалисту «{specialty}». На какую дату хотите записаться?"

    state.state = "ask_specialty"
    return (
        "К какому врачу хотите записаться? Можете назвать специальность "
        f"({SPECIALTY_LIST}) или просто описать жалобу."
    )


def _ask_specialty(db: Session, state: DialogState, text: str, entities: dict) -> str:
    specialty = entities.get("specialty") or infer_specialty_from_symptom(text)
    if not specialty:
        return (
            "Не смог определить специальность. Попробуйте, например: "
            "«хочу к терапевту» или «у меня болит горло»."
        )
    state.slots["specialty"] = specialty
    state.state = "ask_date"
    return f"Понял, это «{specialty}». На какую дату хотите записаться?"


def _ask_date(db: Session, state: DialogState, text: str, entities: dict) -> str:
    date_ = entities.get("date") or extract_date(text)
    if not date_:
        return "Не разобрал дату. Укажите, например: «завтра», «15 октября» или «в понедельник»."

    specialty = state.slots["specialty"]
    available = queries.available_times_for_date(db, specialty, date_)
    if not available:
        return (
            f"На {date_.strftime('%d.%m.%Y')} свободных окон к «{specialty}» нет. "
            "Попробуйте другую дату."
        )
    state.slots["date"] = date_.isoformat()
    state.slots["available_slot_ids"] = [s.id for s in available]
    state.state = "ask_time"
    return f"На {date_.strftime('%d.%m.%Y')} свободно время: {_slots_summary(available)}. Какое время выбираете?"


def _ask_time(db: Session, state: DialogState, text: str, entities: dict) -> str:
    time_ = entities.get("time")
    if not time_:
        return "Не разобрал время. Укажите в формате ЧЧ:ММ, например «14:00»."

    slot_ids = state.slots.get("available_slot_ids", [])
    matching = db.query(DoctorSlot).filter(DoctorSlot.id.in_(slot_ids)).all()
    chosen = next((s for s in matching if s.starts_at.strftime("%H:%M") == time_), None)
    if chosen is None:
        available_str = _slots_summary(matching)
        return f"Такого времени нет в списке. Доступно: {available_str}."

    state.slots["chosen_slot_id"] = chosen.id
    state.slots["time"] = time_
    state.state = "confirm"
    date_str = chosen.starts_at.strftime("%d.%m.%Y")
    return (
        f"Записать вас к «{state.slots['specialty']}» на {date_str} в {time_}? "
        "Ответьте «да» для подтверждения или «нет», чтобы начать заново."
    )


def _confirm(
    db: Session, state: DialogState, text: str, intent: str
) -> tuple[str, bool]:
    if intent == "confirm":
        slot = db.get(DoctorSlot, state.slots["chosen_slot_id"])
        if slot is None or slot.is_booked:
            return (
                "К сожалению, это время уже заняли. Начнём заново — к какому врачу записать?",
                False,
            )
        appointment = queries.book_slot(db, state.patient_id, slot)
        reply = (
            f"Готово! Вы записаны (заявка №{appointment.id}) к «{state.slots['specialty']}» "
            f"на {slot.starts_at.strftime('%d.%m.%Y %H:%M')}."
        )
        return reply, True
    if intent == "deny":
        return "Хорошо, начнём заново. К какому врачу хотите записаться?", False
    return "Пожалуйста, ответьте «да» или «нет».", None


def step(
    db: Session, state: DialogState, text: str, intent: str, entities: dict
) -> str:
    if state.state == "ask_specialty":
        return _ask_specialty(db, state, text, entities)
    if state.state == "ask_date":
        return _ask_date(db, state, text, entities)
    if state.state == "ask_time":
        return _ask_time(db, state, text, entities)
    if state.state == "confirm":
        reply, outcome = _confirm(db, state, text, intent)
        if outcome is True:
            state.scenario = None
            state.state = "idle"
            state.slots = {}
        elif outcome is False:
            state.state = "ask_specialty"
            state.slots = {}
        return reply
    # Unexpected state: reset defensively.
    state.scenario = None
    state.state = "idle"
    state.slots = {}
    return "Что-то пошло не так, давайте начнём заново. Чем могу помочь?"


def cancel_existing_appointment(db: Session, patient_id: str) -> str:
    appointment = queries.cancel_latest_appointment(db, patient_id)
    if appointment is None:
        return "У вас нет активных записей к врачу."
    return f"Запись №{appointment.id} отменена."
