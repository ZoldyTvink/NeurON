import json

import httpx
import pytest

from app.product import llm
from app.product.chat_prompt import compact_context


@pytest.fixture(autouse=True)
def local_provider(monkeypatch):
    monkeypatch.setenv("MEDITRON_LLM_PROVIDER", "local")
    monkeypatch.setenv("MEDITRON_CHAT_MODE", "generative")


def test_context_compaction_preserves_prescribed_schedule():
    item = dict(
        name="Тест",
        strength="500 мг",
        dosage="0,5 таблетки",
        times=["09:00", "21:00"],
        start_date="2026-10-04",
        end_date="2026-10-20",
        weekdays=[0, 2, 4],
        timezone="Europe/Moscow",
        notes="После еды",
    )
    context = {
        "today": "2026-10-04",
        "plans": [{"id": 1, "patient_id": "private-id", "items": [item]}],
    }
    result = compact_context(context)
    assert result["plans"][0]["items"][0] == item
    assert "patient_id" not in result["plans"][0]
    assert context["plans"][0]["patient_id"] == "private-id"


@pytest.mark.parametrize("mode", ["openai", "ollama"])
def test_local_transport_keeps_user_request_last(monkeypatch, mode):
    monkeypatch.setenv("MEDITRON_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("MEDITRON_LLM_FORMAT", mode)
    monkeypatch.setenv("MEDITRON_LLM_KEEP_ALIVE", "30m")
    history = [{"role": "user", "content": "где купить ношпу"}]

    def fake(url, **kwargs):
        body = kwargs["json"]
        assert body["messages"][-1] == history[-1]
        assert "ЕАПТЕК" in body["messages"][0]["content"]
        content = json.dumps(
            {"reply": "Корзина ЕАПТЕКИ пока демонстрационная.", "action": "plans"}
        )
        if mode == "ollama":
            assert url.endswith("/api/chat")
            assert body["keep_alive"] == "30m"
            assert body["format"]["properties"]["action"]["enum"]
            data = {"message": {"content": content}, "done_reason": "stop"}
        else:
            assert url.endswith("/v1/chat/completions")
            data = {
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}]
            }
        return httpx.Response(200, json=data, request=httpx.Request("POST", url))

    monkeypatch.setattr(llm.httpx, "post", fake)
    assert llm.respond(history, {"tools_complete": True}).action == "plans"
    assert history == [{"role": "user", "content": "где купить ношпу"}]


@pytest.mark.parametrize(
    "reason,content",
    [
        ("length", '{"reply":"incomplete", "action":"none"}'),
        ("stop", '{"reply":"done", "action":"purchase_without_confirmation"}'),
    ],
)
def test_native_rejects_truncation_and_unsupported_actions(
    monkeypatch, reason, content
):
    monkeypatch.setenv("MEDITRON_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("MEDITRON_LLM_FORMAT", "ollama")
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda url, **kwargs: httpx.Response(
            200,
            json={"message": {"content": content}, "done_reason": reason},
            request=httpx.Request("POST", url),
        ),
    )
    with pytest.raises(llm.ModelUnavailable):
        llm.respond([{"role": "user", "content": "привет"}], {"tools_complete": True})


def test_read_planning_is_separate_from_answer_generation(monkeypatch):
    monkeypatch.setenv("MEDITRON_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("MEDITRON_LLM_FORMAT", "ollama")
    calls = []

    def fake(url, **kwargs):
        payload = kwargs["json"]
        calls.append(payload)
        assert "queries" in payload["format"]["properties"]
        assert "action" not in payload["format"]["properties"]
        request = json.loads(payload["messages"][-1]["content"])
        assert request["current_message"] == "А завтра?"
        assert request["history"][0]["content"] == "Какие препараты сегодня?"
        result = {
            "answer_mode": "list",
            "queries": [{"topic": "medications", "start": "2026-10-06", "end": None}],
        }
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps(result)}, "done_reason": "stop"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(llm.httpx, "post", fake)
    result = llm.respond(
        [
            {"role": "user", "content": "Какие препараты сегодня?"},
            {"role": "user", "content": "А завтра?"},
        ],
        {"today": "2026-10-05", "timezone": "Europe/Moscow"},
    )
    assert result.queries[0].start.isoformat() == "2026-10-06"
    assert result.answer_mode == "list" and len(calls) == 1


def test_conversation_after_planning_preserves_history_and_does_not_invent_data(
    monkeypatch,
):
    monkeypatch.setenv("MEDITRON_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("MEDITRON_LLM_FORMAT", "ollama")
    calls = []

    def fake(url, **kwargs):
        payload = kwargs["json"]
        calls.append(payload)
        if len(calls) == 1:
            result = {"answer_mode": "explain", "queries": []}
        else:
            assert "queries" not in payload["format"]["properties"]
            assert payload["messages"][-1]["content"] == "Что я писал выше?"
            context = payload["messages"][1]["content"]
            assert '"calendar_events":[]' not in context
            result = {"action": "none", "reply": "Вы писали, что болит горло."}
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps(result)}, "done_reason": "stop"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(llm.httpx, "post", fake)
    result = llm.respond(
        [
            {"role": "user", "content": "Болит горло"},
            {"role": "user", "content": "Что я писал выше?"},
        ],
        {},
    )
    assert len(calls) == 2 and "горло" in result.reply
