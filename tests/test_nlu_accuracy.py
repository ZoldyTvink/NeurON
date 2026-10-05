"""NLU quality checks: intent classifier accuracy + rule-based entity extraction."""

from app.nlu.entities import (
    extract_all_times,
    extract_date,
    extract_dosage,
    extract_specialty,
)
from app.nlu.intent_classifier import classify

INTENT_CASES = [
    ("привет", "greet"),
    ("хочу записаться к терапевту", "book_appointment"),
    ("добавь лекарство парацетамол", "add_medication"),
    ("принял лекарство", "confirm_intake"),
    ("покажи мои лекарства", "show_schedule"),
    ("да", "confirm"),
    ("нет", "deny"),
    ("нужна справка", "request_certificate"),
]


def test_intent_classifier_known_examples():
    correct = sum(1 for text, expected in INTENT_CASES if classify(text)[0] == expected)
    accuracy = correct / len(INTENT_CASES)
    assert accuracy >= 0.75, f"Intent accuracy too low: {accuracy:.2f}"


def test_entity_extraction_specialty_and_dosage():
    assert extract_specialty("запишите меня к терапевту") == "терапевт"
    assert extract_dosage("пить по 500 мг") == "500 мг"


def test_entity_extraction_multiple_times():
    assert extract_all_times("в 9:00 и в 21:00") == ["09:00", "21:00"]


def test_entity_extraction_relative_date():
    assert extract_date("завтра") is not None
