import json
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import Field, model_validator
from sqlalchemy import func

from app.product.api import db_session, owned, patient, plan_out
from app.product.calendar import iso
from app.product.models import (
    CalendarSeries,
    Cart,
    DemoImport,
    Observation,
    Occurrence,
    PatientDocument,
    Plan,
    PlanItem,
    utcnow,
)
from app.product.schemas import Prescription, StrictModel

router = APIRouter(prefix="/api")


def local_today(account):
    return (
        utcnow()
        .replace(tzinfo=timezone.utc)
        .astimezone(ZoneInfo(account.timezone))
        .date()
    )


def scheduled_count(start, end, times, weekdays, zone):
    if end is None:
        return None
    result = 0
    day = start
    while day <= end:
        if day.weekday() in weekdays:
            for time in times:
                local = datetime.fromisoformat(f"{day}T{time}").replace(
                    tzinfo=ZoneInfo(zone)
                )
                if local.astimezone(timezone.utc).astimezone(ZoneInfo(zone)).replace(
                    tzinfo=None
                ) == local.replace(tzinfo=None):
                    result += 1
        day += timedelta(days=1)
    return result


@router.get("/courses")
def courses(account=Depends(patient), db=Depends(db_session)):
    rows = (
        db.query(CalendarSeries)
        .filter_by(patient_id=account.id)
        .order_by(CalendarSeries.id.desc())
        .all()
    )
    counts = {
        (sid, status): count
        for sid, status, count in db.query(
            Occurrence.series_id, Occurrence.status, func.count(Occurrence.id)
        )
        .filter(Occurrence.patient_id == account.id)
        .group_by(Occurrence.series_id, Occurrence.status)
        .all()
    }
    result = []
    for row in rows:
        total = scheduled_count(
            row.start_date,
            row.end_date,
            json.loads(row.times_json),
            json.loads(row.weekdays_json),
            row.timezone,
        )
        taken = counts.get((row.id, "taken"), 0)
        result.append(
            dict(
                id=row.id,
                title=row.title,
                dosage=row.dosage,
                notes=row.notes,
                kind=row.kind,
                start_date=row.start_date,
                end_date=row.end_date,
                times=json.loads(row.times_json),
                source="doctor" if row.prescription_item_id else "personal",
                active=row.active,
                taken=taken,
                skipped=counts.get((row.id, "skipped"), 0),
                total=total,
                percent=round(taken / total * 100) if total else None,
            )
        )
    return result


class DocumentIn(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    kind: Literal["analysis", "certificate"]
    issuer: str = Field(default="", max_length=160)
    issued_on: date
    expires_on: date | None = None
    content: str = Field(default="", max_length=12000)

    @model_validator(mode="after")
    def dates(self):
        if self.expires_on and self.expires_on < self.issued_on:
            raise ValueError("Срок действия не может закончиться раньше даты документа")
        return self


def document_out(row, today):
    left = (row.expires_on - today).days if row.expires_on else None
    return dict(
        id=row.id,
        title=row.title,
        kind=row.kind,
        issuer=row.issuer,
        issued_on=row.issued_on,
        expires_on=row.expires_on,
        content=row.content,
        days_left=left,
        validity="unknown"
        if left is None
        else "expired"
        if left < 0
        else "soon"
        if left <= 3
        else "valid",
    )


@router.get("/documents")
def documents(account=Depends(patient), db=Depends(db_session)):
    return [
        document_out(row, local_today(account))
        for row in db.query(PatientDocument)
        .filter_by(patient_id=account.id)
        .filter(PatientDocument.kind.in_(["analysis", "certificate"]))
        .order_by(PatientDocument.issued_on.desc(), PatientDocument.id.desc())
        .all()
    ]


@router.post("/documents", status_code=201)
def add_document(body: DocumentIn, account=Depends(patient), db=Depends(db_session)):
    row = PatientDocument(patient_id=account.id, **body.model_dump())
    db.add(row)
    db.commit()
    return document_out(row, local_today(account))


@router.put("/documents/{document_id}")
def edit_document(
    document_id: int, body: DocumentIn, account=Depends(patient), db=Depends(db_session)
):
    row = owned(db, PatientDocument, document_id, account.id)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    db.commit()
    return document_out(row, local_today(account))


class ObservationIn(StrictModel):
    text: str = Field(min_length=1, max_length=2000)


@router.get("/observations")
def observations(account=Depends(patient), db=Depends(db_session)):
    return [
        dict(id=row.id, text=row.text, created_at=iso(row.created_at))
        for row in db.query(Observation)
        .filter_by(patient_id=account.id)
        .order_by(Observation.id.desc())
        .limit(100)
        .all()
    ]


@router.post("/observations", status_code=201)
def add_observation(
    body: ObservationIn, account=Depends(patient), db=Depends(db_session)
):
    row = Observation(patient_id=account.id, text=body.text)
    db.add(row)
    db.commit()
    return dict(id=row.id, text=row.text, created_at=iso(row.created_at))


@router.post("/demo/prescription")
def demo_prescription(account=Depends(patient), db=Depends(db_session)):
    import os

    from fastapi import HTTPException

    if os.getenv("MEDITRON_DEMO", "1") != "1":
        raise HTTPException(403, "Симуляция отключена")
    previous = db.get(DemoImport, account.id)
    if previous:
        return plan_out(db, db.get(Plan, previous.plan_id))
    today = local_today(account)
    plan = Plan(
        patient_id=account.id,
        doctor_id=account.doctor_id or "demo-doctor",
        title="Назначение после приёма · демонстрация",
        notes="Заранее подготовленная симуляция внешнего назначения. Не является медицинской рекомендацией.",
    )
    db.add(plan)
    db.flush()
    items = []
    for name, strength, days, times in [
        ("Амоксициллин", "500 мг", 7, ["08:00", "20:00"]),
        ("Омепразол", "20 мг", 14, ["08:30"]),
    ]:
        data = Prescription(
            name=name,
            strength=strength,
            form="капсулы",
            dosage=strength,
            units_per_intake=1,
            start_date=today,
            end_date=today + timedelta(days=days - 1),
            times=times,
            timezone=account.timezone,
            notes="Пример из демонстрационного назначения.",
        )
        item = PlanItem(plan_id=plan.id, data_json=data.model_dump_json())
        db.add(item)
        db.flush()
        items.append(dict(id=item.id, **data.model_dump(mode="json")))
    db.add(
        Cart(
            plan_id=plan.id,
            patient_id=account.id,
            provider="eapteka_redirect",
            data_json=json.dumps(
                {"lines": items, "url": "https://www.eapteka.ru/"}, ensure_ascii=False
            ),
        )
    )
    db.add(DemoImport(patient_id=account.id, plan_id=plan.id))
    for title, kind, expiry in [
        ("Справка о посещении", "certificate", 6),
        ("Анализ крови", "analysis", 2),
    ]:
        db.add(
            PatientDocument(
                patient_id=account.id,
                title=title,
                kind=kind,
                issuer="Демонстрационная клиника",
                issued_on=today,
                expires_on=today + timedelta(days=expiry),
                content=plan.notes
                + "\n"
                + (
                    "\n".join(
                        f"{i['name']} · {i['dosage']} · {', '.join(i['times'])}"
                        for i in items
                    )
                    if kind == "plan"
                    else "Тестовый документ. Срок задан для демонстрации таймера."
                ),
            )
        )
    db.commit()
    return plan_out(db, plan)
