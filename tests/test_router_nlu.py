import json

import httpx
import pytest

from app.product import llm, router_nlu


@pytest.mark.parametrize(
    "route,action,fragment",
    [
        ("wellbeing", "booking", "Короткий ответ"),
        ("pharmacy", "plans", "ЕАПТЕКИ"),
        ("purchased", "plans", "Уже есть"),
        ("personal", "personal_event", "каталоге"),
        ("emergency", "none", "112"),
        ("clarify", "none", "Уточните"),
    ],
)
def test_app_owns_text_and_action(route, action, fragment):
    result = router_nlu.render_route(
        router_nlu.RouteDecision(route=route, reply="Короткий ответ модели."),
        {"plans": [{"id": 1}]},
    )
    assert result.action == action
    assert fragment in result.reply


def test_no_plan_does_not_claim_course_exists():
    result = router_nlu.render_route(
        router_nlu.RouteDecision(route="purchased", reply="Добавьте курс."),
        {"plans": []},
    )
    assert "нужен план" in result.reply


def test_closing_does_not_restart_dialogue_or_offer_action():
    result = router_nlu.render_route(
        router_nlu.RouteDecision(route="closing", reply="До свидания!"), {}
    )
    assert result.action == "none"
    assert "?" not in result.reply


def test_history_uses_saved_reply_and_keeps_input_unchanged():
    content = json.dumps(
        {"action": "booking", "reply": "Хотите выбрать время приёма?"},
        ensure_ascii=False,
    )
    history = [
        {"role": "assistant", "content": content},
        {"role": "user", "content": "да"},
    ]
    messages = router_nlu.messages_for(history)
    data = json.loads(messages[-1]["content"])
    assert data["history"][0]["content"] == "Хотите выбрать время приёма?"
    assert data["current_message"] == "да"
    assert history[0]["content"] == content


def test_recent_context_sent_to_local_model(monkeypatch):
    monkeypatch.setenv("MEDITRON_LLM_PROVIDER", "local")
    monkeypatch.setenv("MEDITRON_CHAT_MODE", "router")
    monkeypatch.setenv("MEDITRON_LLM_FORMAT", "ollama")
    monkeypatch.setenv("MEDITRON_LLM_URL", "http://localhost:11434/v1")
    history = [
        {"role": "user", "content": f"старое сообщение {i}"} for i in range(8)
    ] + [
        {"role": "user", "content": "мне плохо"},
        {"role": "assistant", "content": "Что беспокоит?"},
        {"role": "user", "content": "болит горло"},
        {"role": "assistant", "content": "Давно болит?"},
        {"role": "user", "content": "что я писал выше?"},
    ]

    def fake(url, **kwargs):
        payload = kwargs["json"]
        assert url.endswith("/api/chat")
        data = json.loads(payload["messages"][-1]["content"])
        assert len(data["history"]) == 12
        assert data["history"][-1]["content"] == "Давно болит?"
        assert data["current_message"] == "что я писал выше?"
        assert "patient-private-note" not in json.dumps(payload)
        assert payload["options"]["num_predict"] == 112
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": '{"route":"recall","reply":"Вы написали, что у вас болит горло."}'
                },
                "done_reason": "stop",
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(router_nlu.httpx, "post", fake)
    result = router_nlu.respond(history, {"plans": [{"notes": "patient-private-note"}]})
    assert result.action == "none"
    assert "болит горло" in result.reply


def test_wellbeing_uses_contextual_model_reply():
    result = router_nlu.render_route(
        router_nlu.RouteDecision(
            route="wellbeing", reply="Понимаю, горло болит с утра. Есть ли температура?"
        ),
        {},
    )
    assert result.action == "booking"
    assert "горло болит с утра" in result.reply


def test_recall_summarizes_recent_user_messages_and_skips_keyboard_noise():
    history = [
        {"role": "user", "content": "мне плохо"},
        {"role": "assistant", "content": "Что беспокоит?"},
        {"role": "user", "content": "горло болит"},
        {"role": "assistant", "content": "Есть температура?"},
        {"role": "user", "content": "aaaaaaaй"},
        {"role": "assistant", "content": "Уточните сообщение."},
        {"role": "user", "content": "что я писал выше?"},
    ]
    result = router_nlu.render_route(
        router_nlu.RouteDecision(route="recall", reply="generic response"), {}, history
    )
    assert "мне плохо" in result.reply
    assert "горло болит" in result.reply
    assert "aaaaaaa" not in result.reply


@pytest.mark.parametrize(
    "content,reason",
    [
        (' {"route":"buy_without_permission"}', "stop"),
        ('{"route":"booking"}', "length"),
    ],
)
def test_invalid_route_cannot_trigger_action(monkeypatch, content, reason):
    monkeypatch.setenv("MEDITRON_LLM_FORMAT", "ollama")
    monkeypatch.setenv("MEDITRON_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(
        router_nlu.httpx,
        "post",
        lambda url, **kwargs: httpx.Response(
            200,
            json={"message": {"content": content}, "done_reason": reason},
            request=httpx.Request("POST", url),
        ),
    )
    with pytest.raises(llm.ModelUnavailable):
        router_nlu.classify([{"role": "user", "content": "test"}])
