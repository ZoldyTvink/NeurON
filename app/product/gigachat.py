import hashlib
import math
import os
import ssl
import threading
import time
import uuid

import httpx

AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
API_URL = "https://api.giga.chat/v1/chat/completions"
DEFAULT_MODEL = "GigaChat-2"
_lock = threading.Lock()
_cached_identity = None
_cached_token = ""
_expires_at = 0.0


class GigaChatError(Exception):
    pass


def config():
    return {
        "provider": "gigachat",
        "mode": "hybrid",
        "configured": bool(os.getenv("GIGACHAT_AUTH_KEY", "").strip()),
        "model": os.getenv("GIGACHAT_MODEL", DEFAULT_MODEL),
    }


def tls_context():
    context = ssl.create_default_context()
    if bundle := os.getenv("GIGACHAT_CA_BUNDLE"):
        context.load_verify_locations(cafile=bundle)
    return context


def access_token(client, rejected_token=None):
    global _cached_identity, _cached_token, _expires_at
    key = os.getenv("GIGACHAT_AUTH_KEY", "").strip()
    if not key:
        raise GigaChatError("Не задан GIGACHAT_AUTH_KEY в локальном .env.")
    scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    identity = (
        hashlib.sha256(key.encode()).hexdigest(),
        scope,
        os.getenv("GIGACHAT_CA_BUNDLE"),
    )
    with _lock:
        if identity == _cached_identity and rejected_token == _cached_token:
            _cached_token = ""
        if (
            identity == _cached_identity
            and _cached_token
            and time.time() < _expires_at - 60
        ):
            return _cached_token
        response = client.post(
            AUTH_URL,
            headers={
                "Authorization": "Basic " + key,
                "RqUID": str(uuid.uuid4()),
                "Accept": "application/json",
            },
            data={"scope": scope},
        )
        response.raise_for_status()
        data = response.json()
        token = data["access_token"]
        expiry = float(data["expires_at"])
        # Providers/SDKs expose Unix timestamps in either milliseconds or seconds.
        if expiry > 100_000_000_000:
            expiry /= 1000
        if (
            not isinstance(token, str)
            or not token
            or not math.isfinite(expiry)
            or expiry <= time.time()
        ):
            raise ValueError("Invalid token response")
        _cached_identity, _cached_token, _expires_at = identity, token, expiry
        return token


def complete(messages, schema, max_tokens=1200):
    if not config()["configured"]:
        raise GigaChatError(
            "GigaChat API ещё не подключён. Добавьте GIGACHAT_AUTH_KEY в локальный .env."
        )
    # GigaChat expects a single initial system message; preserve the same knowledge and examples.
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    conversation = [{"role": "system", "content": system}] + [
        dict(m) for m in messages if m["role"] != "system"
    ]
    payload = {
        "model": config()["model"],
        "messages": conversation,
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {"type": "json_schema", "schema": schema, "strict": True},
    }
    try:
        timeout = httpx.Timeout(
            float(os.getenv("MEDITRON_LLM_TIMEOUT", "180")), connect=10
        )
        with httpx.Client(
            verify=tls_context(), timeout=timeout, follow_redirects=False
        ) as client:
            token = access_token(client)
            response = client.post(
                API_URL, headers={"Authorization": "Bearer " + token}, json=payload
            )
            if response.status_code == 401:
                token = access_token(client, rejected_token=token)
                response = client.post(
                    API_URL, headers={"Authorization": "Bearer " + token}, json=payload
                )
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException as exc:
        raise GigaChatError(
            "GigaChat API не успел ответить. Повторите запрос позже."
        ) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        messages = {
            401: "GigaChat API отклонил ключ авторизации. Проверьте ключ и scope.",
            403: "Нет доступа к выбранной модели GigaChat API.",
            402: "Проверьте доступный баланс GigaChat API.",
            404: "GigaChat API не нашёл выбранную модель.",
            422: "GigaChat API не принял формат запроса или размер контекста.",
            429: "Достигнут лимит запросов GigaChat API. Попробуйте позже.",
        }
        raise GigaChatError(
            messages.get(status, "GigaChat API вернул ошибку. Попробуйте позже.")
        ) from exc
    except httpx.ConnectError as exc:
        raise GigaChatError(
            "Нет соединения с GigaChat API. Проверьте сеть и доверенные сертификаты (GIGACHAT_CA_BUNDLE)."
        ) from exc
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
        raise GigaChatError(
            "Не удалось обработать ответ GigaChat API. Проверьте настройки подключения и сертификаты."
        ) from exc
