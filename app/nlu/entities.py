"""Rule-based slot extraction (NER): dates, times, specialties, drugs, dosage.

A dictionary/regex approach is used here instead of a trained NER model because it is
100% deterministic and auditable for a medical-adjacent scenario (no hallucinated
entities), and because labelled Russian NER data for this exact domain is not
available off-the-shelf. This module is the designated extension point: swap
`extract_specialty`/`extract_drug_name` for a trained model (e.g. fine-tuned on
RuMedBench/RuMedSymptomRec) without touching the dialogue flows.
"""

import re
from datetime import datetime
from typing import Optional

import dateparser

SPECIALTY_SYNONYMS: dict[str, list[str]] = {
    "терапевт": ["терапевт", "терапевту", "терапевта", "общий врач", "участковый врач"],
    "кардиолог": ["кардиолог", "кардиологу", "кардиолога", "сердце"],
    "невролог": ["невролог", "неврологу", "невролога", "невропатолог"],
    "лор": ["лор", "лору", "лора", "отоларинголог", "ухо горло нос"],
    "офтальмолог": ["офтальмолог", "офтальмологу", "окулист", "окулисту"],
    "дерматолог": ["дерматолог", "дерматологу", "дерматолога", "кожник"],
    "гастроэнтеролог": ["гастроэнтеролог", "гастроэнтерологу", "гастроэнтеролога"],
    "эндокринолог": ["эндокринолог", "эндокринологу", "эндокринолога"],
}

# Lightweight symptom -> specialty heuristic (baseline). Replace with a model trained
# on RuMedSymptomRec for production-grade triage accuracy — see module docstring.
SYMPTOM_TO_SPECIALTY: list[tuple[list[str], str]] = [
    (
        ["голова болит", "болит голова", "головная боль", "мигрень", "головокружение"],
        "невролог",
    ),
    (
        [
            "горло болит",
            "болит горло",
            "кашель",
            "насморк",
            "заложен нос",
            "температура",
        ],
        "терапевт",
    ),
    (["ухо болит", "болит ухо", "не слышу", "заложило ухо"], "лор"),
    (
        ["живот болит", "болит живот", "тошнит", "изжога", "понос", "запор"],
        "гастроэнтеролог",
    ),
    (["сердце болит", "болит сердце", "давление", "тахикардия", "одышка"], "кардиолог"),
    (["сыпь", "зуд", "чешется кожа", "прыщи", "аллергия на коже"], "дерматолог"),
    (["глаза болят", "плохо вижу", "зрение упало", "режет глаза"], "офтальмолог"),
    (["сахар в крови", "щитовидка", "лишний вес", "жажда постоянная"], "эндокринолог"),
]

# Small reference list of common drug names (generic prototype data, not a medical DB).
KNOWN_DRUGS = [
    "парацетамол",
    "ибупрофен",
    "амоксициллин",
    "аспирин",
    "но-шпа",
    "ношпа",
    "каптоприл",
    "метформин",
    "лозартан",
    "омепразол",
    "цитрамон",
    "нурофен",
    "эналаприл",
    "анальгин",
    "супрастин",
]

TIME_WORD_MAP = {
    "утром": "09:00",
    "с утра": "09:00",
    "днём": "13:00",
    "днем": "13:00",
    "в обед": "13:00",
    "вечером": "19:00",
    "на ночь": "22:00",
    "ночью": "22:00",
}

_DOSAGE_RE = re.compile(r"(\d+)\s?(мг|мл|г|таблетк\w*|капсул\w*)", re.IGNORECASE)
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3])[:.\s]([0-5]\d)\b")
_HOUR_ONLY_RE = re.compile(r"\bв\s+(\d{1,2})(?:\s*час\w*)?\b")

# Interrupting an active flow is safety-critical, so it must not depend on a fuzzy
# ML confidence score: match explicit keywords instead of trusting the intent classifier.
CANCEL_KEYWORDS = [
    "отмена",
    "отмени",
    "отменить",
    "стоп",
    "не хочу",
    "забудь",
    "хватит",
    "прекрати",
    "передумал",
    "передумала",
    "остановись",
    "выход",
    "cancel",
]


def is_cancel_phrase(text: str) -> bool:
    lowered = text.lower()
    return any(kw in lowered for kw in CANCEL_KEYWORDS)


def extract_specialty(text: str) -> Optional[str]:
    lowered = text.lower()
    for specialty, synonyms in SPECIALTY_SYNONYMS.items():
        if any(syn in lowered for syn in synonyms):
            return specialty
    return None


def infer_specialty_from_symptom(text: str) -> Optional[str]:
    lowered = text.lower()
    for keywords, specialty in SYMPTOM_TO_SPECIALTY:
        if any(kw in lowered for kw in keywords):
            return specialty
    return None


def extract_date(text: str) -> Optional[datetime]:
    parsed = dateparser.parse(
        text,
        languages=["ru"],
        settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": datetime.now()},
    )
    if parsed is None:
        return None
    # Guard against dateparser picking up a bare time-of-day as "today".
    if parsed.date() < datetime.now().date():
        return None
    return parsed


def extract_time(text: str) -> Optional[str]:
    lowered = text.lower()
    match = _TIME_RE.search(lowered)
    if match:
        hour, minute = match.groups()
        return f"{int(hour):02d}:{minute}"
    hour_match = _HOUR_ONLY_RE.search(lowered)
    if hour_match:
        return f"{int(hour_match.group(1)):02d}:00"
    for word, value in TIME_WORD_MAP.items():
        if word in lowered:
            return value
    return None


def extract_all_times(text: str) -> list[str]:
    """Returns every distinct time mention, e.g. 'в 9 и в 21' -> ['09:00', '21:00']."""
    lowered = text.lower()
    times = []
    for match in _TIME_RE.finditer(lowered):
        hour, minute = match.groups()
        times.append(f"{int(hour):02d}:{minute}")
    for match in _HOUR_ONLY_RE.finditer(lowered):
        times.append(f"{int(match.group(1)):02d}:00")
    if not times:
        for word, value in TIME_WORD_MAP.items():
            if word in lowered:
                times.append(value)
    # de-duplicate while preserving order
    seen = set()
    unique = []
    for t in times:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def extract_drug_name(text: str) -> Optional[str]:
    lowered = text.lower()
    for drug in KNOWN_DRUGS:
        if drug in lowered:
            return drug.replace("ношпа", "но-шпа").capitalize()
    return None


def extract_dosage(text: str) -> Optional[str]:
    match = _DOSAGE_RE.search(text.lower())
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return None


def extract_entities(text: str) -> dict:
    """Runs every extractor and returns only the entities that were found."""
    entities = {}
    specialty = extract_specialty(text) or infer_specialty_from_symptom(text)
    if specialty:
        entities["specialty"] = specialty
    date_ = extract_date(text)
    if date_:
        entities["date"] = date_
    time_ = extract_time(text)
    if time_:
        entities["time"] = time_
    all_times = extract_all_times(text)
    if all_times:
        entities["times"] = all_times
    drug = extract_drug_name(text)
    if drug:
        entities["drug"] = drug
    dosage = extract_dosage(text)
    if dosage:
        entities["dosage"] = dosage
    return entities
