from datetime import datetime

from sqlalchemy.orm import Session

from app.db import queries
from app.dialogue.fsm import DialogState
from app.nlu.entities import extract_all_times


def start(db: Session, state: DialogState, text: str, entities: dict) -> str:
    state.scenario = "medication"
    state.slots = {}

    drug = entities.get("drug")
    if drug:
        state.slots["drug"] = drug
        state.state = "ask_dosage"
        return f"Добавляю «{drug}». Какая дозировка? (например, «500 мг»)"

    state.state = "ask_name"
    return "Какое лекарство нужно добавить в расписание приёма?"


def _ask_name(state: DialogState, text: str, entities: dict) -> str:
    drug = entities.get("drug") or (
        text.strip().split("\n")[0].strip() if text.strip() else None
    )
    if not drug:
        return "Не расслышал название лекарства. Введите его ещё раз."
    state.slots["drug"] = drug.capitalize() if not entities.get("drug") else drug
    state.state = "ask_dosage"
    return f"Хорошо, «{state.slots['drug']}». Какая дозировка? (например, «500 мг»)"


def _ask_dosage(state: DialogState, text: str, entities: dict) -> str:
    dosage = entities.get("dosage")
    if not dosage:
        return "Не разобрал дозировку. Укажите в формате «число + мг/мл/таблетка», например «1 таблетка»."
    state.slots["dosage"] = dosage
    state.state = "ask_time"
    return "В какое время принимать? Можно несколько, например «в 9:00 и в 21:00»."


def _ask_time(state: DialogState, text: str, entities: dict) -> str:
    times = entities.get("times") or extract_all_times(text)
    if not times:
        return "Не разобрал время приёма. Укажите, например, «в 9:00» или «утром и вечером»."
    state.slots["times"] = times
    state.state = "confirm"
    times_str = ", ".join(times)
    return (
        f"Добавить «{state.slots['drug']}», дозировка {state.slots['dosage']}, "
        f"приём в {times_str}? Ответьте «да» или «нет»."
    )


def _confirm(db: Session, state: DialogState, intent: str) -> tuple[str, bool | None]:
    if intent == "confirm":
        med = queries.create_medication(
            db,
            state.patient_id,
            state.slots["drug"],
            state.slots["dosage"],
            state.slots["times"],
        )
        return (
            f"Готово! «{med.name}» добавлен в расписание (ID {med.id}). Напоминания будут приходить по графику.",
            True,
        )
    if intent == "deny":
        return "Хорошо, начнём заново. Какое лекарство добавить?", False
    return "Пожалуйста, ответьте «да» или «нет».", None


def step(
    db: Session, state: DialogState, text: str, intent: str, entities: dict
) -> str:
    if state.state == "ask_name":
        return _ask_name(state, text, entities)
    if state.state == "ask_dosage":
        return _ask_dosage(state, text, entities)
    if state.state == "ask_time":
        return _ask_time(state, text, entities)
    if state.state == "confirm":
        reply, outcome = _confirm(db, state, intent)
        if outcome is True:
            state.scenario = None
            state.state = "idle"
            state.slots = {}
        elif outcome is False:
            state.state = "ask_name"
            state.slots = {}
        return reply
    state.scenario = None
    state.state = "idle"
    state.slots = {}
    return "Что-то пошло не так, давайте начнём заново. Чем могу помочь?"


def confirm_intake(db: Session, patient_id: str) -> str:
    log = queries.find_pending_log_for_patient(db, patient_id)
    if log is None:
        return "Не вижу лекарств, которые нужно отметить прямо сейчас."
    log.taken = True
    log.taken_at = datetime.now()
    db.commit()
    return "Отметил приём лекарства. Спасибо!"


def show_schedule(db: Session, patient_id: str) -> str:
    meds = queries.active_medications(db, patient_id)
    if not meds:
        return "У вас пока нет активных лекарств в расписании."
    lines = [
        f"- {m.name}, {m.dosage}, приём в {', '.join(m.time_list())}" for m in meds
    ]
    return "Ваше текущее расписание приёма лекарств:\n" + "\n".join(lines)
