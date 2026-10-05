import json
import os
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

# Overridable so tests can point at an isolated temp database.
DB_URL = os.environ.get("MEDITRON_DB_URL", "sqlite:///./meditron.db")

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True)
    full_name = Column(String, nullable=False)
    specialty = Column(String, nullable=False, index=True)

    slots = relationship("DoctorSlot", back_populates="doctor")


class DoctorSlot(Base):
    __tablename__ = "doctor_slots"

    id = Column(Integer, primary_key=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    starts_at = Column(DateTime, nullable=False, index=True)
    is_booked = Column(Boolean, default=False, nullable=False)

    doctor = relationship("Doctor", back_populates="slots")


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True)
    patient_id = Column(String, nullable=False, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    slot_id = Column(Integer, ForeignKey("doctor_slots.id"), nullable=False)
    status = Column(String, default="booked", nullable=False)  # booked | cancelled
    created_at = Column(DateTime, default=datetime.now)


class Medication(Base):
    __tablename__ = "medications"

    id = Column(Integer, primary_key=True)
    patient_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    dosage = Column(String, nullable=False)
    # Comma-separated "HH:MM" strings, e.g. "09:00,21:00".
    times = Column(String, nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now)

    def time_list(self):
        return [t.strip() for t in self.times.split(",") if t.strip()]


class MedicationLog(Base):
    __tablename__ = "medication_logs"

    id = Column(Integer, primary_key=True)
    medication_id = Column(Integer, ForeignKey("medications.id"), nullable=False)
    scheduled_at = Column(DateTime, nullable=False, index=True)
    taken = Column(Boolean, default=False, nullable=False)
    taken_at = Column(DateTime, nullable=True)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    patient_id = Column(String, nullable=False, index=True)
    payload = Column(Text, nullable=False)
    due_at = Column(DateTime, nullable=False)
    delivered = Column(Boolean, default=False, nullable=False)


class DialogSession(Base):
    """Persisted FSM state so a patient can resume a conversation across requests."""

    __tablename__ = "dialog_sessions"

    patient_id = Column(String, primary_key=True)
    scenario = Column(String, nullable=True)  # None | booking | medication
    state = Column(String, nullable=False, default="idle")
    slots_json = Column(Text, nullable=False, default="{}")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def get_slots(self) -> dict:
        return json.loads(self.slots_json or "{}")

    def set_slots(self, slots: dict) -> None:
        self.slots_json = json.dumps(slots, ensure_ascii=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
