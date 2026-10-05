"""Seeds the mock DB with synthetic doctors and bookable slots.

All data here is fictional (no real patients, doctors or PII) and exists only
to make the prototype demonstrable end-to-end.
"""

import random
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models import Doctor, DoctorSlot

SPECIALTIES_AND_DOCTORS = {
    "терапевт": ["Иванова Анна Сергеевна", "Петров Олег Викторович"],
    "кардиолог": ["Смирнова Елена Павловна", "Кузнецов Дмитрий Игоревич"],
    "невролог": ["Васильева Марина Олеговна", "Фёдоров Сергей Андреевич"],
    "лор": ["Соколова Ирина Викторовна", "Морозов Артём Дмитриевич"],
    "офтальмолог": ["Егорова Наталья Петровна", "Волков Игорь Станиславович"],
    "дерматолог": ["Новикова Ольга Александровна"],
    "гастроэнтеролог": ["Орлов Павел Николаевич"],
    "эндокринолог": ["Григорьева Татьяна Викторовна"],
}

WORK_HOURS = range(9, 18)  # 09:00..17:00
DAYS_AHEAD = 7


def seed_if_empty(db: Session) -> None:
    if db.query(Doctor).first() is not None:
        return  # already seeded

    random.seed(42)  # reproducible demo data
    today = datetime.now().replace(minute=0, second=0, microsecond=0)

    for specialty, names in SPECIALTIES_AND_DOCTORS.items():
        for full_name in names:
            doctor = Doctor(full_name=full_name, specialty=specialty)
            db.add(doctor)
            db.flush()  # get doctor.id

            for day_offset in range(1, DAYS_AHEAD + 1):
                day = today + timedelta(days=day_offset)
                if day.weekday() >= 5:  # skip weekends
                    continue
                for hour in WORK_HOURS:
                    starts_at = day.replace(hour=hour)
                    is_booked = random.random() < 0.2  # ~20% pre-booked
                    db.add(
                        DoctorSlot(
                            doctor_id=doctor.id,
                            starts_at=starts_at,
                            is_booked=is_booked,
                        )
                    )

    db.commit()
