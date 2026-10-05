import base64
import json
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from pywebpush import WebPushException, webpush
from requests import RequestException
from requests import Session as HttpSession
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.db.models import Base
from app.product.api import db_session, patient
from app.product.models import CalendarSeries, Occurrence, Reminder, utcnow

router = APIRouter(prefix="/api/push")


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id = Column(Integer, primary_key=True)
    patient_id = Column(String, nullable=False, index=True)
    endpoint = Column(Text, unique=True, nullable=False)
    data_json = Column(Text, nullable=False)
    active = Column(Boolean, nullable=False, default=True)


class PushDelivery(Base):
    __tablename__ = "push_deliveries"
    __table_args__ = (UniqueConstraint("reminder_id", "subscription_id", "due_at"),)
    id = Column(Integer, primary_key=True)
    reminder_id = Column(Integer, nullable=False)
    subscription_id = Column(Integer, nullable=False)
    due_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    sent_at = Column(DateTime, nullable=True)
    next_attempt_at = Column(DateTime, nullable=True)


def key_path():
    return Path(
        os.getenv(
            "MEDITRON_VAPID_KEY",
            str(Path(__file__).resolve().parents[2] / ".runtime" / "vapid.pem"),
        )
    )


def public_key():
    path = key_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        key = ec.generate_private_key(ec.SECP256R1())
        data = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    raw = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class Subscribe(BaseModel):
    endpoint: str = Field(max_length=2000)
    keys: dict[str, str]
    expirationTime: float | None = None

    @field_validator("endpoint")
    @classmethod
    def allowed_endpoint(cls, value):
        url = urlsplit(value)
        allowed = {
            "fcm.googleapis.com",
            "updates.push.services.mozilla.com",
            "web.push.apple.com",
        }
        if (
            url.scheme != "https"
            or url.hostname not in allowed
            or url.port not in (None, 443)
            or url.username
            or url.password
            or url.fragment
        ):
            raise ValueError("Push-провайдер этого браузера не поддерживается")
        return value

    @field_validator("keys")
    @classmethod
    def valid_keys(cls, values):
        if set(values) != {"auth", "p256dh"}:
            raise ValueError("Неверные ключи подписки")
        try:
            decoded = {
                k: base64.b64decode(
                    v + "=" * (-len(v) % 4), altchars=b"-_", validate=True
                )
                for k, v in values.items()
            }
            if len(decoded["auth"]) != 16 or len(decoded["p256dh"]) != 65:
                raise ValueError()
            ec.EllipticCurvePublicKey.from_encoded_point(
                ec.SECP256R1(), decoded["p256dh"]
            )
        except (ValueError, TypeError):
            raise ValueError("Неверные ключи подписки")
        return values


@router.get("/key")
def get_key(account=Depends(patient)):
    return {"public_key": public_key()}


@router.post("/subscribe")
def subscribe(body: Subscribe, account=Depends(patient), db=Depends(db_session)):
    row = db.query(PushSubscription).filter_by(endpoint=body.endpoint).first()
    if row and row.patient_id != account.id:
        raise HTTPException(
            409, "Сначала отключите уведомления предыдущего кабинета в этом браузере"
        )
    if row is None:
        if (
            db.query(PushSubscription)
            .filter_by(patient_id=account.id, active=True)
            .count()
            >= 5
        ):
            raise HTTPException(409, "Подключено максимальное количество устройств")
        row = PushSubscription(patient_id=account.id, endpoint=body.endpoint)
        db.add(row)
    row.data_json = body.model_dump_json()
    row.active = True
    db.commit()
    return {"ok": True}


class Unsubscribe(BaseModel):
    endpoint: str = Field(max_length=2000)


@router.post("/unsubscribe")
def unsubscribe(body: Unsubscribe, account=Depends(patient), db=Depends(db_session)):
    row = (
        db.query(PushSubscription)
        .filter_by(endpoint=body.endpoint, patient_id=account.id)
        .first()
    )
    if row:
        row.active = False
        db.commit()
    return {"ok": True}


class NoRedirectSession(HttpSession):
    def request(self, *args, **kwargs):
        kwargs["allow_redirects"] = False
        return super().request(*args, **kwargs)


def send_due(db, now=None):
    now = now or utcnow()
    if not key_path().exists():
        return
    due = (
        db.query(Reminder)
        .join(Occurrence, Reminder.occurrence_id == Occurrence.id)
        .join(CalendarSeries, Occurrence.series_id == CalendarSeries.id)
        .filter(
            Reminder.due_at <= now,
            Reminder.due_at >= now - timedelta(hours=6),
            Reminder.acknowledged_at.is_(None),
            Occurrence.status == "pending",
            CalendarSeries.active.is_(True),
        )
        .all()
    )
    for reminder in due:
        for sub in (
            db.query(PushSubscription)
            .filter_by(patient_id=reminder.patient_id, active=True)
            .all()
        ):
            delivery = (
                db.query(PushDelivery)
                .filter_by(
                    reminder_id=reminder.id,
                    subscription_id=sub.id,
                    due_at=reminder.due_at,
                )
                .first()
            )
            if delivery is None:
                delivery = PushDelivery(
                    reminder_id=reminder.id,
                    subscription_id=sub.id,
                    due_at=reminder.due_at,
                )
                db.add(delivery)
                db.flush()
            if (
                delivery.sent_at
                or delivery.attempts >= 5
                or (delivery.next_attempt_at and delivery.next_attempt_at > now)
            ):
                continue
            delivery.attempts += 1
            # Commit attempt before network I/O; at-least-once delivery, stable browser tag.
            delivery.next_attempt_at = now + timedelta(minutes=2**delivery.attempts)
            db.commit()
            try:
                with NoRedirectSession() as http:
                    result = webpush(
                        json.loads(sub.data_json),
                        data=json.dumps(
                            {
                                "title": "Meditron · напоминание",
                                "body": "У вас запланировано событие. Откройте календарь.",
                                "event_id": reminder.occurrence_id,
                                "tag": f"meditron-{reminder.id}",
                            }
                        ),
                        vapid_private_key=str(key_path()),
                        vapid_claims={
                            "sub": os.getenv(
                                "MEDITRON_VAPID_SUBJECT", "mailto:demo@example.org"
                            )
                        },
                        ttl=3600,
                        timeout=10,
                        requests_session=http,
                    )
                if 200 <= result.status_code < 300:
                    delivery.sent_at = (
                        now  # accepted by push service, not proof of display
                    )
            except WebPushException as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in (404, 410):
                    sub.active = False
            except (RequestException, ValueError):
                pass  # Persisted backoff; don't leak endpoints, keys or medical text to logs.
            db.commit()
