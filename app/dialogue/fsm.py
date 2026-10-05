"""Session persistence + small dataclass wrapper around a patient's dialogue state."""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db.models import DialogSession


@dataclass
class DialogState:
    patient_id: str
    scenario: str | None  # None | "booking" | "medication"
    state: str
    slots: dict = field(default_factory=dict)


def load_state(db: Session, patient_id: str) -> DialogState:
    row = db.get(DialogSession, patient_id)
    if row is None:
        row = DialogSession(
            patient_id=patient_id, scenario=None, state="idle", slots_json="{}"
        )
        db.add(row)
        db.commit()
    return DialogState(
        patient_id=patient_id,
        scenario=row.scenario,
        state=row.state,
        slots=row.get_slots(),
    )


def save_state(db: Session, dialog_state: DialogState) -> None:
    row = db.get(DialogSession, dialog_state.patient_id)
    if row is None:
        row = DialogSession(patient_id=dialog_state.patient_id)
        db.add(row)
    row.scenario = dialog_state.scenario
    row.state = dialog_state.state
    row.set_slots(dialog_state.slots)
    db.commit()


def reset_state(db: Session, patient_id: str) -> DialogState:
    fresh = DialogState(patient_id=patient_id, scenario=None, state="idle", slots={})
    save_state(db, fresh)
    return fresh
