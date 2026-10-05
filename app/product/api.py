import hashlib
import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.db.models import Appointment, Doctor, DoctorSlot, SessionLocal
from app.product import llm
from app.product.calendar import event_out, iso, maintain, make_series, materialize
from app.product.models import (
    Account,
    CalendarSeries,
    Cart,
    ChatSuggestion,
    LoginSession,
    Message,
    Occurrence,
    Plan,
    PlanItem,
    Reminder,
    seed_accounts,
    utcnow,
)
from app.product.schemas import BookingIn, ChatIn, MarkEvent, PersonalEvent

router = APIRouter(prefix="/api")


def display_message_content(role, content):
    """Keep legacy assistant messages aligned with the current reply style."""
    if role != "assistant":
        return content
    return re.sub(
        r"\s+Часовой пояс: [A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*\.",
        "",
        content,
    )


def db_session():
    with SessionLocal() as db:
        yield db


def current_account(request: Request, db: Session = Depends(db_session)):
    token = request.cookies.get("meditron_session", "")
    session = db.get(LoginSession, hashlib.sha256(token.encode()).hexdigest())
    if not session or session.expires_at <= utcnow():
        raise HTTPException(401, "Войдите в кабинет")
    account = db.get(Account, session.account_id)
    if account is None:
        raise HTTPException(401, "Кабинет не найден")
    return account


def patient(account=Depends(current_account)):
    if account.role != "patient":
        raise HTTPException(403, "Это действие доступно пациенту")
    return account


def owned(db, model, key, patient_id):
    row = db.get(model, key)
    if row is None or row.patient_id != patient_id:
        raise HTTPException(404, "Запись не найдена")
    return row


def account_out(account):
    return dict(
        id=account.id, name=account.name, role=account.role, timezone=account.timezone
    )


class DemoLogin(BaseModel):
    role: Literal["patient"] = "patient"


@router.get("/config")
def configuration():
    return dict(
        demo=os.getenv("MEDITRON_DEMO", "1") == "1",
        llm=llm.config(),
        pharmacy="eapteka_redirect",
    )


@router.post("/demo/login")
def demo_login(
    body: DemoLogin, request: Request, response: Response, db=Depends(db_session)
):
    if os.getenv("MEDITRON_DEMO", "1") != "1":
        raise HTTPException(403, "Демонстрационный вход отключён")
    seed_accounts(db)
    old = db.get(
        LoginSession,
        hashlib.sha256(
            request.cookies.get("meditron_session", "").encode()
        ).hexdigest(),
    )
    if old:
        db.delete(old)
    token = secrets.token_urlsafe(32)
    db.add(
        LoginSession(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            account_id=f"demo-{body.role}",
            expires_at=utcnow() + timedelta(days=1),
        )
    )
    db.commit()
    response.set_cookie(
        "meditron_session",
        token,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=86400,
    )
    return account_out(db.get(Account, f"demo-{body.role}"))


@router.post("/logout")
def logout(request: Request, response: Response, db=Depends(db_session)):
    row = db.get(
        LoginSession,
        hashlib.sha256(
            request.cookies.get("meditron_session", "").encode()
        ).hexdigest(),
    )
    if row:
        db.delete(row)
        db.commit()
    response.delete_cookie("meditron_session")
    return {"ok": True}


@router.get("/me")
def me(account=Depends(current_account)):
    return account_out(account)


def plan_out(db, plan):
    items = [
        dict(id=i.id, **json.loads(i.data_json))
        for i in db.query(PlanItem)
        .filter_by(plan_id=plan.id)
        .order_by(PlanItem.id)
        .all()
    ]
    ids = [i["id"] for i in items]
    scheduled = (
        db.query(CalendarSeries)
        .filter(CalendarSeries.prescription_item_id.in_(ids))
        .count()
    )
    cart = db.query(Cart).filter_by(plan_id=plan.id).first()
    author = db.get(Account, plan.doctor_id)
    return dict(
        id=plan.id,
        title=plan.title,
        notes=plan.notes,
        patient_id=plan.patient_id,
        doctor_name=author.name if author else "Врач",
        created_at=iso(plan.created_at),
        items=items,
        scheduled=scheduled == len(items),
        cart_id=cart.id if cart else None,
        purchase_status=cart.status if cart else None,
    )


@router.get("/plans")
def plans(account=Depends(patient), db=Depends(db_session)):
    query = db.query(Plan)
    query = (
        query.filter_by(patient_id=account.id)
        if account.role == "patient"
        else query.filter_by(doctor_id=account.id)
    )
    return [plan_out(db, p) for p in query.order_by(Plan.id.desc()).all()]


@router.get("/carts/{cart_id}")
def get_cart(cart_id: int, account=Depends(patient), db=Depends(db_session)):
    cart = owned(db, Cart, cart_id, account.id)
    plan = owned(db, Plan, cart.plan_id, account.id)
    return dict(
        id=cart.id,
        plan_id=cart.plan_id,
        status=cart.status,
        confirmation_source=cart.confirmation_source,
        url="https://www.eapteka.ru/",
        lines=plan_out(db, plan)["items"],
        notice="Заказ и доставка оформляются на сайте ЕАПТЕКИ. Перенос корзины и проверка оплаты не подключены.",
    )


@router.post("/carts/{cart_id}/confirm")
def confirm_purchase(cart_id: int, account=Depends(patient), db=Depends(db_session)):
    cart = owned(db, Cart, cart_id, account.id)
    if cart.status != "confirmed":
        cart.status = "confirmed"
        cart.confirmation_source = "patient_reported"
        cart.confirmed_at = utcnow()
        db.commit()
    return {"status": cart.status, "confirmation_source": cart.confirmation_source}


class ActivatePlan(BaseModel):
    source: Literal["purchase", "already_have"]


@router.post("/plans/{plan_id}/calendar")
def activate(
    plan_id: int, body: ActivatePlan, account=Depends(patient), db=Depends(db_session)
):
    plan = owned(db, Plan, plan_id, account.id)
    if body.source == "purchase":
        cart = db.query(Cart).filter_by(plan_id=plan.id).first()
        if not cart or cart.status != "confirmed":
            raise HTTPException(
                409, "Сначала подтвердите покупку или выберите «Уже есть»"
            )
    created = []
    for item in db.query(PlanItem).filter_by(plan_id=plan.id).all():
        series = (
            db.query(CalendarSeries).filter_by(prescription_item_id=item.id).first()
        )
        if series is None:
            series = make_series(db, account.id, json.loads(item.data_json), item.id)
        created.append(series.id)
    db.commit()
    return {"series_ids": created, "scheduled": True}


@router.get("/calendar")
def calendar(start: date, end: date, account=Depends(patient), db=Depends(db_session)):
    if not 0 <= (end - start).days <= 62:
        raise HTTPException(422, "Выберите период до 63 дней")
    if end > utcnow().date() + timedelta(
        days=366
    ) or start < utcnow().date() - timedelta(days=366):
        raise HTTPException(
            422, "Календарь прототипа доступен в пределах года от текущей даты"
        )
    for series in (
        db.query(CalendarSeries).filter_by(patient_id=account.id, active=True).all()
    ):
        materialize(db, series, end + timedelta(days=1))
    db.commit()
    zone = ZoneInfo(account.timezone)
    start_utc = (
        datetime.combine(start, datetime.min.time(), tzinfo=zone)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    end_utc = (
        datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=zone)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    rows = (
        db.query(Occurrence, CalendarSeries)
        .join(CalendarSeries)
        .filter(
            Occurrence.patient_id == account.id,
            Occurrence.scheduled_at >= start_utc,
            Occurrence.scheduled_at < end_utc,
        )
        .order_by(Occurrence.scheduled_at, Occurrence.id)
        .all()
    )
    return [event_out(event, series) for event, series in rows]


@router.post("/calendar/series", status_code=201)
def create_personal(
    body: PersonalEvent, account=Depends(patient), db=Depends(db_session)
):
    series = make_series(db, account.id, body.model_dump(mode="json"))
    db.commit()
    return {"id": series.id}


@router.delete("/calendar/series/{series_id}")
def stop_personal(series_id: int, account=Depends(patient), db=Depends(db_session)):
    series = owned(db, CalendarSeries, series_id, account.id)
    if series.prescription_item_id:
        raise HTTPException(409, "Изменение назначения обсудите с врачом")
    series.active = False
    db.query(Occurrence).filter(
        Occurrence.series_id == series.id,
        Occurrence.status == "pending",
        Occurrence.scheduled_at >= utcnow(),
    ).update({"status": "cancelled"})
    db.commit()
    return {"ok": True}


@router.post("/calendar/events/{event_id}/status")
def mark(
    event_id: int, body: MarkEvent, account=Depends(patient), db=Depends(db_session)
):
    event = owned(db, Occurrence, event_id, account.id)
    if event.status == "cancelled":
        raise HTTPException(409, "Событие отменено")
    if event.scheduled_at > utcnow() + timedelta(minutes=5):
        raise HTTPException(409, "Это событие ещё не наступило")
    reminder = db.query(Reminder).filter_by(occurrence_id=event.id).first()
    if body.status == "snoozed":
        if event.status != "pending":
            raise HTTPException(409, "Событие уже отмечено")
        if reminder is None:
            reminder = Reminder(occurrence_id=event.id, patient_id=account.id)
            db.add(reminder)
        reminder.due_at = utcnow() + timedelta(minutes=10)
        reminder.acknowledged_at = None
    else:
        event.status = body.status
        event.completed_at = utcnow() if body.status == "taken" else None
        if reminder:
            reminder.acknowledged_at = utcnow()
    db.commit()
    return {"id": event.id, "status": event.status}


@router.get("/reminders")
def reminders(account=Depends(patient), db=Depends(db_session)):
    maintain(db)
    db.commit()
    rows = (
        db.query(Reminder, Occurrence, CalendarSeries)
        .join(Occurrence, Reminder.occurrence_id == Occurrence.id)
        .join(CalendarSeries)
        .filter(
            Reminder.patient_id == account.id,
            Reminder.acknowledged_at.is_(None),
            Reminder.due_at <= utcnow(),
            Reminder.due_at >= utcnow() - timedelta(hours=6),
            Occurrence.status == "pending",
            CalendarSeries.active.is_(True),
        )
        .order_by(Reminder.due_at)
        .all()
    )
    return [
        dict(id=r.id, due_at=iso(r.due_at), event=event_out(e, s)) for r, e, s in rows
    ]


@router.post("/reminders/{reminder_id}/ack")
def ack(reminder_id: int, account=Depends(patient), db=Depends(db_session)):
    reminder = owned(db, Reminder, reminder_id, account.id)
    if reminder.due_at > utcnow():
        raise HTTPException(409, "Напоминание ещё не наступило")
    reminder.acknowledged_at = utcnow()
    db.commit()
    return {"ok": True}


@router.get("/chat/history")
def history(account=Depends(patient), db=Depends(db_session)):
    rows = (
        db.query(Message, ChatSuggestion.action)
        .outerjoin(ChatSuggestion, ChatSuggestion.message_id == Message.id)
        .filter(Message.patient_id == account.id)
        .order_by(Message.id.desc())
        .limit(80)
        .all()
    )
    return [
        dict(
            id=m.id,
            role=m.role,
            content=display_message_content(m.role, m.content),
            action=action or "none",
        )
        for m, action in reversed(rows)
    ]


@router.post("/chat")
def chat(body: ChatIn, account=Depends(patient), db=Depends(db_session)):
    previous = (
        db.query(Message)
        .filter_by(patient_id=account.id)
        .order_by(Message.id.desc())
        .limit(24)
        .all()
    )
    selected, remaining = [], 8000 - len(body.message)
    for m in previous:
        if len(m.content) > remaining:
            break
        selected.append(m)
        remaining -= len(m.content)
    messages = [
        {"role": m.role, "content": display_message_content(m.role, m.content)}
        for m in reversed(selected)
    ]
    suggestions = dict(
        db.query(ChatSuggestion.message_id, ChatSuggestion.action)
        .filter(ChatSuggestion.message_id.in_([m.id for m in selected]))
        .all()
    )
    for message, saved in zip(messages, reversed(selected)):
        if saved.role == "assistant" and saved.id in suggestions:
            message["content"] = json.dumps(
                {"action": suggestions[saved.id], "reply": message["content"]},
                ensure_ascii=False,
            )
    messages.append({"role": "user", "content": body.message})
    try:
        from app.product.assistant import respond

        result = respond(messages, db, account)
    except llm.ModelUnavailable as exc:
        raise HTTPException(503, str(exc))
    answer = Message(patient_id=account.id, role="assistant", content=result.reply)
    db.add_all(
        [Message(patient_id=account.id, role="user", content=body.message), answer]
    )
    db.flush()
    db.add(ChatSuggestion(message_id=answer.id, action=result.action))
    db.commit()
    return result


@router.get("/booking/slots")
def booking_slots(account=Depends(patient), db=Depends(db_session)):
    # Legacy doctor slots use server-local wall time, default deployment is Moscow.
    rows = (
        db.query(DoctorSlot, Doctor)
        .join(Doctor)
        .filter(DoctorSlot.is_booked.is_(False), DoctorSlot.starts_at > datetime.now())
        .order_by(DoctorSlot.starts_at)
        .limit(200)
        .all()
    )
    return [
        dict(
            id=s.id,
            doctor=d.full_name,
            specialty=d.specialty,
            starts_at=s.starts_at.isoformat(),
        )
        for s, d in rows
    ]


@router.post("/booking", status_code=201)
def book(body: BookingIn, account=Depends(patient), db=Depends(db_session)):
    slot = db.get(DoctorSlot, body.slot_id)
    if not slot or slot.starts_at <= datetime.now():
        raise HTTPException(409, "Время недоступно")
    claimed = db.execute(
        update(DoctorSlot)
        .where(DoctorSlot.id == slot.id, DoctorSlot.is_booked.is_(False))
        .values(is_booked=True)
    )
    if claimed.rowcount != 1:
        raise HTTPException(409, "Это время уже занято")
    appointment = Appointment(
        patient_id=account.id, doctor_id=slot.doctor_id, slot_id=slot.id
    )
    db.add(appointment)
    db.commit()
    return {"id": appointment.id, "status": "booked"}


@router.get("/booking")
def bookings(account=Depends(patient), db=Depends(db_session)):
    rows = (
        db.query(Appointment, DoctorSlot, Doctor)
        .join(DoctorSlot, Appointment.slot_id == DoctorSlot.id)
        .join(Doctor, Appointment.doctor_id == Doctor.id)
        .filter(Appointment.patient_id == account.id)
        .order_by(DoctorSlot.starts_at)
        .all()
    )
    return [
        dict(
            id=a.id,
            status=a.status,
            doctor=d.full_name,
            specialty=d.specialty,
            starts_at=s.starts_at.isoformat(),
        )
        for a, s, d in rows
    ]


@router.delete("/booking/{appointment_id}")
def cancel_booking(
    appointment_id: int, account=Depends(patient), db=Depends(db_session)
):
    row = owned(db, Appointment, appointment_id, account.id)
    if row.status == "booked":
        row.status = "cancelled"
        db.get(DoctorSlot, row.slot_id).is_booked = False
        db.commit()
    return {"ok": True}
