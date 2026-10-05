from datetime import datetime, timedelta

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.db.models import Appointment, Doctor, DoctorSlot, Medication, MedicationLog


def find_doctors_by_specialty(db: Session, specialty: str) -> list[Doctor]:
    return db.query(Doctor).filter(Doctor.specialty == specialty).all()


def available_times_for_date(
    db: Session, specialty: str, date_: datetime
) -> list[DoctorSlot]:
    day_start = date_.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    return (
        db.query(DoctorSlot)
        .join(Doctor)
        .filter(
            Doctor.specialty == specialty,
            DoctorSlot.is_booked.is_(False),
            and_(DoctorSlot.starts_at >= day_start, DoctorSlot.starts_at < day_end),
        )
        .order_by(DoctorSlot.starts_at)
        .all()
    )


def book_slot(db: Session, patient_id: str, slot: DoctorSlot) -> Appointment:
    slot.is_booked = True
    appointment = Appointment(
        patient_id=patient_id,
        doctor_id=slot.doctor_id,
        slot_id=slot.id,
        status="booked",
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    return appointment


def cancel_latest_appointment(db: Session, patient_id: str) -> Appointment | None:
    appointment = (
        db.query(Appointment)
        .filter(Appointment.patient_id == patient_id, Appointment.status == "booked")
        .order_by(Appointment.created_at.desc())
        .first()
    )
    if appointment is None:
        return None
    appointment.status = "cancelled"
    slot = db.get(DoctorSlot, appointment.slot_id)
    if slot is not None:
        slot.is_booked = False
    db.commit()
    return appointment


def create_medication(
    db: Session, patient_id: str, name: str, dosage: str, times: list[str]
) -> Medication:
    med = Medication(
        patient_id=patient_id,
        name=name,
        dosage=dosage,
        times=",".join(times),
        active=True,
    )
    db.add(med)
    db.commit()
    db.refresh(med)
    return med


def active_medications(db: Session, patient_id: str) -> list[Medication]:
    return (
        db.query(Medication)
        .filter(Medication.patient_id == patient_id, Medication.active.is_(True))
        .all()
    )


def find_pending_log_for_patient(db: Session, patient_id: str) -> MedicationLog | None:
    """Earliest not-yet-taken dose, scheduled within the last few hours, for this patient."""
    cutoff = datetime.now() - timedelta(hours=4)
    return (
        db.query(MedicationLog)
        .join(Medication)
        .filter(
            Medication.patient_id == patient_id,
            MedicationLog.taken.is_(False),
            MedicationLog.scheduled_at >= cutoff,
            MedicationLog.scheduled_at <= datetime.now() + timedelta(minutes=1),
        )
        .order_by(MedicationLog.scheduled_at.asc())
        .first()
    )
