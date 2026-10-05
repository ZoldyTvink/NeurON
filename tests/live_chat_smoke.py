import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright


def run():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="meditron-live-chat-") as tmp:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "MEDITRON_DB_URL": f"sqlite:///{tmp}/test.db",
            "MEDITRON_DEMO": "1",
            "MEDITRON_SCHEDULER": "0",
            "MEDITRON_LLM_PRELOAD": "0",
        }
        with open(Path(tmp) / "server.log", "w+") as log:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--env-file",
                    str(root / ".env"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=root,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                for _ in range(100):
                    try:
                        with urlopen(url + "/health", timeout=1):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    log.seek(0)
                    raise RuntimeError(log.read())
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    page = browser.new_page(viewport={"width": 1280, "height": 900})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(url)
                    page.locator("[data-action=login-patient]").click()
                    page.locator("[data-action=nav-chat]").click()
                    expect(page.locator("#chat-form")).to_be_visible()
                    assert page.request.get(url + "/api/config").json()["llm"][
                        "configured"
                    ]
                    cases = [
                        (
                            "Хочу записаться к терапевту. Как выбрать время?",
                            "booking",
                            "Выбрать время приёма",
                        ),
                        (
                            "Нет, записываться пока не буду. Лучше добавлю завтрак по будням.",
                            "personal_event",
                            "Добавить своё событие",
                        ),
                        ("мне плохо", "booking", "Выбрать время приёма"),
                        ("где купить ношпу", "plans", "Открыть назначения"),
                        ("Спасибо, больше ничего не нужно", "none", None),
                    ]
                    for message, action, label in cases:
                        page.locator("#chat-form textarea").fill(message)
                        start = time.monotonic()
                        with page.expect_response(
                            lambda r: (
                                r.url == url + "/api/chat"
                                and r.request.method == "POST"
                            ),
                            timeout=200000,
                        ) as pending:
                            page.locator("#chat-form button").click()
                            expect(
                                page.locator('#messages [role="status"]')
                            ).to_contain_text("Модель готовит ответ")
                            expect(page.locator("#chat-form textarea")).to_be_disabled()
                        response = pending.value
                        assert response.status == 200, response.text()
                        result = response.json()
                        assert result["action"] == action, result
                        expect(page.locator("#messages")).to_contain_text(
                            result["reply"]
                        )
                        if label:
                            expect(
                                page.locator("#messages")
                                .get_by_role("button", name=label)
                                .last
                            ).to_be_visible()
                        else:
                            assert "?" not in result["reply"], result
                        expect(page.locator("#chat-form textarea")).to_be_enabled()
                        print(
                            f"{action}: {time.monotonic() - start:.1f}s — {result['reply']}",
                            flush=True,
                        )
                    history = page.request.get(url + "/api/chat/history").json()
                    assert [m["role"] for m in history] == ["user", "assistant"] * len(
                        cases
                    )
                    assert [
                        m["action"] for m in history if m["role"] == "assistant"
                    ] == [c[1] for c in cases]
                    page.reload()
                    page.locator("[data-action=nav-chat]").click()
                    expect(
                        page.locator("#messages").get_by_role(
                            "button", name="Открыть назначения"
                        )
                    ).to_be_visible()
                    page.screenshot(path="/tmp/meditron-live-chat.png", full_page=True)
                    assert not errors, errors
                    browser.close()
                    print(
                        "Live browser chat passed; the working database was not used."
                    )
            finally:
                server.terminate()
                server.wait(timeout=15)


if __name__ == "__main__":
    run()
