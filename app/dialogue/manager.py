from sqlalchemy.orm import Session

from app.db.models import SessionLocal
from app.dialogue import booking_flow, medication_flow
from app.dialogue.fsm import DialogState, load_state, save_state
from app.nlu.entities import extract_entities, is_cancel_phrase
from app.nlu.intent_classifier import classify

HELP_TEXT = (
    "Я помогу вам:\n"
    "1) записаться к врачу — например, «хочу записаться к терапевту»;\n"
    "2) управлять приёмом лекарств — например, «добавь лекарство парацетамол»;\n"
    "3) отметить приём лекарства — «принял лекарство»;\n"
    "4) посмотреть расписание — «покажи мои лекарства»;\n"
    "5) отменить запись к врачу — «отмените мою запись».\n"
    "В любой момент можно написать «отмена»."
)
GREETING = "Здравствуйте! Я ИИ-помощник пациента. " + HELP_TEXT
CERTIFICATE_STUB_REPLY = (
    "Заявка на справку принята (демо-режим). В реальной системе она будет направлена "
    "в регистратуру, ожидаемый срок оформления — 1-3 рабочих дня."
)


def _handle_idle(
    db: Session, state: DialogState, text: str, intent: str, entities: dict
) -> str:
    if intent == "greet":
        return GREETING
    if intent == "help":
        return HELP_TEXT
    if intent == "book_appointment":
        return booking_flow.start(db, state, text, entities)
    if intent == "describe_symptom":
        return booking_flow.start(db, state, text, entities)
    if intent == "cancel_appointment":
        return booking_flow.cancel_existing_appointment(db, state.patient_id)
    if intent == "add_medication":
        return medication_flow.start(db, state, text, entities)
    if intent == "confirm_intake":
        return medication_flow.confirm_intake(db, state.patient_id)
    if intent == "show_schedule":
        return medication_flow.show_schedule(db, state.patient_id)
    if intent == "request_certificate":
        return CERTIFICATE_STUB_REPLY
    return "Не совсем понял запрос.\n\n" + HELP_TEXT


def handle_message(patient_id: str, text: str) -> str:
    db = SessionLocal()
    try:
        state = load_state(db, patient_id)
        intent, _confidence = classify(text)
        entities = extract_entities(text)

        if is_cancel_phrase(text) and state.scenario is not None:
            state.scenario = None
            state.state = "idle"
            state.slots = {}
            reply = "Хорошо, отменил текущую операцию. Чем ещё могу помочь?"
        elif state.scenario == "booking":
            reply = booking_flow.step(db, state, text, intent, entities)
        elif state.scenario == "medication":
            reply = medication_flow.step(db, state, text, intent, entities)
        else:
            reply = _handle_idle(db, state, text, intent, entities)

        save_state(db, state)
        return reply
    finally:
        db.close()
