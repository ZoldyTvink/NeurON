"""Additive schema: existing prototype tables and data are retained.

All timestamps in these tables are naive UTC; local dates belong to series.timezone.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.db.models import Base


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Account(Base):
    __tablename__ = "product_accounts"
    id = Column(String, primary_key=True)
    role = Column(String, nullable=False)
    name = Column(String, nullable=False)
    doctor_id = Column(String, nullable=True)
    timezone = Column(String, nullable=False, default="Europe/Moscow")


class LoginSession(Base):
    __tablename__ = "product_sessions"
    token_hash = Column(String, primary_key=True)
    account_id = Column(String, ForeignKey("product_accounts.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)


class Plan(Base):
    __tablename__ = "treatment_plans"
    id = Column(Integer, primary_key=True)
    doctor_id = Column(String, ForeignKey("product_accounts.id"), nullable=False)
    patient_id = Column(
        String, ForeignKey("product_accounts.id"), nullable=False, index=True
    )
    title = Column(String, nullable=False)
    notes = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=utcnow)


class PlanItem(Base):
    __tablename__ = "prescription_items"
    id = Column(Integer, primary_key=True)
    plan_id = Column(
        Integer, ForeignKey("treatment_plans.id"), nullable=False, index=True
    )
    # Original validated prescription; never reconstructed from pharmacy goods.
    data_json = Column(Text, nullable=False)


class Cart(Base):
    __tablename__ = "product_carts"
    id = Column(Integer, primary_key=True)
    plan_id = Column(
        Integer, ForeignKey("treatment_plans.id"), nullable=False, unique=True
    )
    patient_id = Column(String, nullable=False, index=True)
    provider = Column(String, nullable=False, default="demo")
    data_json = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="prepared")
    confirmation_source = Column(String, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)


class CalendarSeries(Base):
    __tablename__ = "calendar_series"
    id = Column(Integer, primary_key=True)
    patient_id = Column(String, nullable=False, index=True)
    prescription_item_id = Column(
        Integer, ForeignKey("prescription_items.id"), nullable=True, unique=True
    )
    title = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    dosage = Column(String, nullable=False, default="")
    notes = Column(Text, nullable=False, default="")
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    times_json = Column(Text, nullable=False)
    weekdays_json = Column(Text, nullable=False)
    timezone = Column(String, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    generated_until = Column(Date, nullable=True)


class Occurrence(Base):
    __tablename__ = "calendar_occurrences"
    __table_args__ = (UniqueConstraint("series_id", "scheduled_at"),)
    id = Column(Integer, primary_key=True)
    series_id = Column(
        Integer, ForeignKey("calendar_series.id"), nullable=False, index=True
    )
    patient_id = Column(String, nullable=False, index=True)
    scheduled_at = Column(DateTime, nullable=False, index=True)
    status = Column(String, nullable=False, default="pending")
    completed_at = Column(DateTime, nullable=True)


class Reminder(Base):
    __tablename__ = "product_reminders"
    id = Column(Integer, primary_key=True)
    occurrence_id = Column(
        Integer, ForeignKey("calendar_occurrences.id"), nullable=False, unique=True
    )
    patient_id = Column(String, nullable=False, index=True)
    due_at = Column(DateTime, nullable=False, index=True)
    acknowledged_at = Column(DateTime, nullable=True)


class Message(Base):
    __tablename__ = "product_messages"
    id = Column(Integer, primary_key=True)
    patient_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class ChatSuggestion(Base):
    __tablename__ = "chat_suggestions"
    message_id = Column(Integer, ForeignKey("product_messages.id"), primary_key=True)
    action = Column(String, nullable=False)


class DoctorQuestion(Base):
    __tablename__ = "doctor_questions"
    id = Column(Integer, primary_key=True)
    patient_id = Column(String, nullable=False, index=True)
    doctor_id = Column(String, nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("treatment_plans.id"), nullable=True)
    text = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    answered_at = Column(DateTime, nullable=True)


def seed_accounts(db):
    for account_id, role, name, doctor_id in [
        ("demo-doctor", "doctor", "Анна Иванова", None),
        ("demo-patient", "patient", "Алексей Смирнов", "demo-doctor"),
    ]:
        if db.get(Account, account_id) is None:
            db.add(Account(id=account_id, role=role, name=name, doctor_id=doctor_id))
    db.commit()


class PatientDocument(Base):
    __tablename__ = "patient_documents"
    id = Column(Integer, primary_key=True)
    patient_id = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    issuer = Column(String, nullable=False, default="")
    issued_on = Column(Date, nullable=False)
    expires_on = Column(Date, nullable=True)
    content = Column(Text, nullable=False, default="")


class Observation(Base):
    __tablename__ = "patient_observations"
    id = Column(Integer, primary_key=True)
    patient_id = Column(String, nullable=False, index=True)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)


class DemoImport(Base):
    __tablename__ = "patient_demo_imports"
    patient_id = Column(String, primary_key=True)
    plan_id = Column(Integer, ForeignKey("treatment_plans.id"), nullable=False)
