import json
import time
import uuid

import httpx
import pytest

from app.product import gigachat, llm


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("MEDITRON_LLM_PROVIDER", "gigachat")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "test-authorization-key")
    monkeypatch.setenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    monkeypatch.setenv("GIGACHAT_MODEL", "GigaChat-2")
    monkeypatch.delenv("GIGACHAT_CA_BUNDLE", raising=False)
    monkeypatch.setattr(gigachat, "_cached_identity", None)
    monkeypatch.setattr(gigachat, "_cached_token", "")
    monkeypatch.setattr(gigachat, "_expires_at", 0)
    original = httpx.Client

    def install(handler):
        def client(**kwargs):
            assert kwargs["verify"].check_hostname
            assert kwargs["follow_redirects"] is False
            return original(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(gigachat.httpx, "Client", client)

    return install


def answer(reason="stop", action="plans"):
    return {
        "choices": [
            {
                "finish_reason": reason,
                "message": {
                    "content": json.dumps(
                        {"action": action, "reply": "Откройте назначения."}
                    )
                },
            }
        ]
    }


def invoke():
    return llm.respond(
        [{"role": "user", "content": "Где купить назначенный препарат?"}],
        {"tools_complete": True, "pharmacy": {"name": "ЕАПТЕКА", "mode": "demo"}},
    )


@pytest.mark.parametrize("milliseconds", [True, False])
def test_auth_cache_and_structured_schema(cloud, milliseconds):
    calls = []

    def handler(request):
        calls.append(request)
        if str(request.url) == gigachat.AUTH_URL:
            assert request.headers["authorization"] == "Basic test-authorization-key"
            assert uuid.UUID(request.headers["rquid"]).version == 4
            assert request.content == b"scope=GIGACHAT_API_PERS"
            expires = (time.time() + 1800) * (1000 if milliseconds else 1)
            return httpx.Response(
                200, json={"access_token": "test-access-token", "expires_at": expires}
            )
        assert str(request.url) == gigachat.API_URL
        assert request.headers["authorization"] == "Bearer test-access-token"
        payload = json.loads(request.content)
        assert payload["model"] == "GigaChat-2"
        assert payload["response_format"]["schema"]["properties"]["action"]["enum"]
        assert payload["response_format"]["strict"] is True
        assert len([m for m in payload["messages"] if m["role"] == "system"]) == 1
        assert "ЕАПТЕКА" in payload["messages"][0]["content"]
        assert payload["messages"][-1]["content"] == "Где купить назначенный препарат?"
        return httpx.Response(200, json=answer())

    cloud(handler)
    assert invoke().action == "plans"
    assert invoke().action == "plans"
    assert len(calls) == 3  # One OAuth request, two completions.
    assert "key" not in json.dumps(llm.config())
    assert "test-access-token" not in json.dumps(llm.config())


def test_expired_token_refreshed_once(cloud):
    oauth_calls = 0
    chat_calls = 0

    def handler(request):
        nonlocal oauth_calls, chat_calls
        if str(request.url) == gigachat.AUTH_URL:
            oauth_calls += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"token-{oauth_calls}",
                    "expires_at": (time.time() + 1800) * 1000,
                },
            )
        chat_calls += 1
        if chat_calls == 1:
            return httpx.Response(401)
        assert request.headers["authorization"] == "Bearer token-2"
        return httpx.Response(200, json=answer())

    cloud(handler)
    assert invoke().action == "plans"
    assert (oauth_calls, chat_calls) == (2, 2)


@pytest.mark.parametrize("status", [401, 429, 500])
def test_errors_do_not_leak_secrets_or_fall_back(cloud, status):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if str(request.url) == gigachat.AUTH_URL:
            return httpx.Response(
                200,
                json={
                    "access_token": "secret-token",
                    "expires_at": (time.time() + 1800) * 1000,
                },
            )
        return httpx.Response(status, text="private upstream body secret-token")

    cloud(handler)
    with pytest.raises(llm.ModelUnavailable) as error:
        invoke()
    assert "secret-token" not in str(error.value)
    assert "private upstream body" not in str(error.value)
    assert all(url in {gigachat.AUTH_URL, gigachat.API_URL} for url in calls)
    assert len(calls) == (4 if status == 401 else 2)


def test_missing_credentials_never_calls_cloud(cloud, monkeypatch):
    monkeypatch.delenv("GIGACHAT_AUTH_KEY")
    cloud(lambda request: pytest.fail("Unexpected network request"))
    assert llm.config()["configured"] is False
    with pytest.raises(llm.ModelUnavailable, match="GIGACHAT_AUTH_KEY"):
        invoke()


@pytest.mark.parametrize(
    "reason,action", [("length", "plans"), ("blacklist", "none"), ("stop", "buy_now")]
)
def test_rejects_filtered_truncated_and_invalid_response(cloud, reason, action):
    def handler(request):
        if str(request.url) == gigachat.AUTH_URL:
            return httpx.Response(
                200,
                json={
                    "access_token": "token",
                    "expires_at": (time.time() + 1800) * 1000,
                },
            )
        return httpx.Response(200, json=answer(reason, action))

    cloud(handler)
    with pytest.raises(llm.ModelUnavailable):
        invoke()
