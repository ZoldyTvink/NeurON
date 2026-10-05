from app.benchmark_llm import score, summarize
from app.product.schemas import ChatAction


def test_fast_wrong_action_is_not_success():
    case = {"actions": ["plans"], "contains_any": [["ЕАПТЕК"], ["демо"]]}
    verdict = score(case, ChatAction(action="booking", reply="ЕАПТЕКА: деморежим"))
    assert verdict["text_checks_ok"] and not verdict["passed"]


def test_summary_counts_errors_and_does_not_hide_slow_responses():
    rows = [
        dict(
            seconds=1,
            valid_response=True,
            action_ok=True,
            passed=True,
            correct_within_5s=True,
        ),
        dict(
            seconds=9,
            valid_response=True,
            action_ok=True,
            passed=True,
            correct_within_5s=False,
        ),
        dict(
            seconds=180,
            valid_response=False,
            action_ok=False,
            passed=False,
            correct_within_5s=False,
        ),
    ]
    result = summarize(rows)
    assert result["requests"] == 3
    assert result["valid_responses"] == 2
    assert result["full_response_p50_seconds"] == 5
    assert result["full_response_p95_seconds"] == 9
    assert result["correct_within_5s"] == 1
