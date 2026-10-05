import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.sqlite import insert

from app.product.models import CalendarSeries, Occurrence, Reminder, utcnow


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def make_series(db, patient_id, data, prescription_item_id=None):
    row = CalendarSeries(
        patient_id=patient_id,
        prescription_item_id=prescription_item_id,
        title=data.get("title", data.get("name")),
        kind=data.get("kind", "medication"),
        dosage=data.get("dosage", ""),
        notes=data.get("notes", ""),
        start_date=date.fromisoformat(data["start_date"]),
        end_date=date.fromisoformat(data["end_date"]) if data.get("end_date") else None,
        times_json=json.dumps(data["times"]),
        weekdays_json=json.dumps(data["weekdays"]),
        timezone=data["timezone"],
    )
    db.add(row)
    db.flush()
    materialize(db, row)
    return row


def materialize(db, series, through=None):
    if not series.active:
        return
    today = (
        utcnow()
        .replace(tzinfo=timezone.utc)
        .astimezone(ZoneInfo(series.timezone))
        .date()
    )
    horizon = through or today + timedelta(days=31)
    end = min(horizon, series.end_date) if series.end_date else horizon
    start = (
        max(series.start_date, series.generated_until + timedelta(days=1))
        if series.generated_until
        else series.start_date
    )
    weekdays = json.loads(series.weekdays_json)
    times = json.loads(series.times_json)
    rows = []
    day = start
    while day <= end:
        if day.weekday() in weekdays:
            for time in times:
                local = datetime.fromisoformat(f"{day.isoformat()}T{time}").replace(
                    tzinfo=ZoneInfo(series.timezone)
                )
                scheduled = local.astimezone(timezone.utc).replace(tzinfo=None)
                # A nonexistent local time (DST transition) is skipped, not shifted silently.
                if scheduled.replace(tzinfo=timezone.utc).astimezone(
                    ZoneInfo(series.timezone)
                ).replace(tzinfo=None) != local.replace(tzinfo=None):
                    continue
                rows.append(
                    dict(
                        series_id=series.id,
                        patient_id=series.patient_id,
                        scheduled_at=scheduled,
                        status="pending",
                    )
                )
        day += timedelta(days=1)
    for offset in range(0, len(rows), 100):
        db.execute(
            insert(Occurrence)
            .values(rows[offset : offset + 100])
            .on_conflict_do_nothing(index_elements=["series_id", "scheduled_at"])
        )
    if end >= start:
        series.generated_until = end
    db.flush()


def maintain(db, now=None):
    """Recover after downtime. No flood of notifications older than six hours."""
    now = now or utcnow()
    for series in db.query(CalendarSeries).filter_by(active=True).all():
        materialize(db, series)
    due = (
        db.query(Occurrence)
        .join(CalendarSeries)
        .filter(
            CalendarSeries.active.is_(True),
            Occurrence.status == "pending",
            Occurrence.scheduled_at <= now,
            Occurrence.scheduled_at >= now - timedelta(hours=6),
        )
        .all()
    )
    for occurrence in due:
        db.execute(
            insert(Reminder)
            .values(
                occurrence_id=occurrence.id,
                patient_id=occurrence.patient_id,
                due_at=occurrence.scheduled_at,
            )
            .on_conflict_do_nothing(index_elements=["occurrence_id"])
        )
    db.flush()


def event_out(event, series):
    return dict(
        id=event.id,
        series_id=series.id,
        title=series.title,
        kind=series.kind,
        dosage=series.dosage,
        notes=series.notes,
        scheduled_at=iso(event.scheduled_at),
        local_time=event.scheduled_at.replace(tzinfo=timezone.utc)
        .astimezone(ZoneInfo(series.timezone))
        .isoformat(),
        timezone=series.timezone,
        status=event.status,
        completed_at=iso(event.completed_at),
        source="doctor" if series.prescription_item_id else "personal",
    )
