"""Background job that turns Medication schedules into due Notifications.

Runs every minute, matches the current HH:MM against each active medication's
configured times, and (idempotently, once per day per slot) creates a
MedicationLog + a Notification. A real deployment would push the Notification
to the Telegram bot; here it's just queued for GET /notifications/due to pick up.
"""

from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.db.models import Medication, MedicationLog, Notification, SessionLocal


def _check_due_medications() -> None:
    db = SessionLocal()
    try:
        now = datetime.now()
        current_hhmm = now.strftime("%H:%M")
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        for med in db.query(Medication).filter(Medication.active.is_(True)).all():
            if current_hhmm not in med.time_list():
                continue

            scheduled_at = now.replace(second=0, microsecond=0)
            already_logged = (
                db.query(MedicationLog)
                .filter(
                    MedicationLog.medication_id == med.id,
                    MedicationLog.scheduled_at >= today_start,
                    MedicationLog.scheduled_at == scheduled_at,
                )
                .first()
            )
            if already_logged:
                continue

            db.add(
                MedicationLog(
                    medication_id=med.id, scheduled_at=scheduled_at, taken=False
                )
            )
            db.add(
                Notification(
                    patient_id=med.patient_id,
                    payload=f"Напоминание: пора принять «{med.name}» ({med.dosage}).",
                    due_at=now,
                    delivered=False,
                )
            )
        db.commit()
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _check_due_medications, "interval", minutes=1, id="medication_reminders"
    )
    scheduler.start()
    return scheduler
