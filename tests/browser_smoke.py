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
    with tempfile.TemporaryDirectory(prefix="meditron-ui-") as tmp:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "MEDITRON_DB_URL": f"sqlite:///{tmp}/test.db",
            "MEDITRON_SCHEDULER": "0",
            "MEDITRON_DEMO": "1",
            "MEDITRON_LLM_URL": "",
            "MEDITRON_LLM_PRELOAD": "0",
        }
        with open(Path(tmp) / "server.log", "w+") as log:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
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
                for _ in range(80):
                    try:
                        with urlopen(url + "/health", timeout=1):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    log.seek(0)
                    raise RuntimeError(log.read())
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
                    page = browser.new_page(viewport={"width": 1440, "height": 1024})
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.goto(url)
                    page.get_by_role("button", name="Открыть демоверсию").click()
                    expect(
                        page.get_by_role("heading", name="Сегодня", exact=True)
                    ).to_be_visible()
                    expect(page.get_by_text("Кабинет врача", exact=True)).to_have_count(
                        0
                    )
                    page.get_by_role("button", name="Получить демоназначение").click()
                    expect(
                        page.get_by_role("heading", name="План лечения", exact=True)
                    ).to_be_visible()
                    page.get_by_role("button", name="Купить в ЕАПТЕКЕ ↗").click()
                    expect(
                        page.get_by_role("link", name="Перейти в ЕАПТЕКУ ↗")
                    ).to_have_attribute("href", "https://www.eapteka.ru/")
                    page.get_by_role(
                        "button", name="Подтвердить покупку", exact=True
                    ).click()
                    page.get_by_role(
                        "button", name="Добавить курс в календарь", exact=True
                    ).click()
                    expect(page.locator(".course-card")).to_have_count(2)
                    page.get_by_role(
                        "button", name="＋ Добавить препарат", exact=True
                    ).click()
                    page.locator("#personal-form [name=title]").fill("Мой витамин")
                    page.locator("#personal-form [name=times]").fill("09:00")
                    start = page.locator(
                        "#personal-form [name=start_date]"
                    ).input_value()
                    page.locator("#personal-form [name=end_date]").fill(start)
                    page.get_by_role(
                        "button", name="Добавить в календарь", exact=True
                    ).click()
                    expect(page.locator(".course-card")).to_have_count(3)
                    page.screenshot(
                        path=str(root / ".runtime/redesign-medications.png"),
                        full_page=True,
                    )
                    page.get_by_role(
                        "button", name="Календарь", exact=True
                    ).first.click()
                    expect(page.locator(".week-column")).to_have_count(7)
                    page.screenshot(
                        path=str(root / ".runtime/redesign-calendar.png"),
                        full_page=True,
                    )
                    page.get_by_role("button", name="Документы", exact=True).click()
                    expect(page.locator(".document-card")).to_have_count(2)
                    page.get_by_role("button", name="Анализы", exact=True).click()
                    expect(page.locator(".document-card")).to_have_count(1)
                    expect(page.locator(".document-card")).to_contain_text(
                        "Осталось 2 дн."
                    )
                    page.get_by_role("button", name="Изменить срок и данные").click()
                    page.locator("#document-form [name=expires_on]").fill("")
                    page.get_by_role("button", name="Сохранить документ").click()
                    expect(page.locator(".document-card")).to_contain_text(
                        "Срок не указан"
                    )
                    page.get_by_role("button", name="Все", exact=True).click()
                    expect(page.locator(".document-card")).to_have_count(2)
                    page.locator("#toasts").evaluate("(e)=>e.replaceChildren()")
                    page.screenshot(
                        path=str(root / ".runtime/redesign-documents.png"),
                        full_page=True,
                    )
                    page.get_by_role(
                        "button", name="История здоровья", exact=True
                    ).click()
                    page.get_by_role("button", name="＋ Добавить наблюдение").click()
                    page.locator("#observation-form textarea").fill(
                        "Чувствую себя хорошо"
                    )
                    page.get_by_role("button", name="Сохранить наблюдение").click()
                    expect(
                        page.get_by_text("Чувствую себя хорошо", exact=True)
                    ).to_be_visible()
                    page.get_by_role("button", name="Чат", exact=True).click()
                    expect(page.locator("#chat-form")).to_be_visible()
                    expect(page.locator(".chat-hero")).to_be_visible()
                    expect(page.locator("#messages .bubble")).to_have_count(0)
                    page.locator("#toasts").evaluate("(e)=>e.replaceChildren()")
                    page.screenshot(
                        path=str(root / ".runtime/chat-welcome.png"), full_page=True
                    )
                    page.get_by_role(
                        "button", name="Настроить календарь", exact=False
                    ).click()
                    expect(page.locator("#chat-form textarea")).to_have_value(
                        "Как добавить свои витамины в календарь?"
                    )
                    # Failed inference must restore the draft and empty welcome screen.
                    page.route(
                        "**/api/chat",
                        lambda route: route.fulfill(
                            status=500,
                            body="Internal Server Error",
                            content_type="text/plain",
                        ),
                    )
                    page.locator("#chat-form textarea").press("Enter")
                    expect(page.locator("#chat-form textarea")).to_be_enabled()
                    expect(
                        page.get_by_text(
                            "Сервис не смог обработать запрос. Текст сообщения сохранён — попробуйте отправить его ещё раз.",
                            exact=True,
                        )
                    ).to_be_visible()
                    expect(page.locator("#chat-form textarea")).to_have_value(
                        "Как добавить свои витамины в календарь?"
                    )
                    expect(page.locator(".chat-hero")).to_be_visible()
                    page.unroute("**/api/chat")
                    chat_history = []
                    page.route(
                        "**/api/chat/history",
                        lambda route: route.fulfill(json=chat_history),
                    )

                    def reply(route):
                        message = route.request.post_data_json["message"]
                        answer = "Вы можете добавить витамины или любое личное событие в календарь. Нажмите кнопку ниже и укажите удобное время."
                        chat_history.extend(
                            [
                                {"role": "user", "content": message},
                                {
                                    "role": "assistant",
                                    "content": answer,
                                    "action": "personal_event",
                                },
                            ]
                        )
                        route.fulfill(
                            json={"reply": answer, "action": "personal_event"}
                        )

                    page.route("**/api/chat", reply)
                    page.locator("#chat-form textarea").press("Enter")
                    expect(page.locator("#messages .chat-turn")).to_have_count(2)
                    expect(page.locator(".chat-hero")).not_to_be_visible()
                    expect(
                        page.locator("#messages").get_by_role(
                            "button", name="Добавить своё событие"
                        )
                    ).to_be_visible()
                    user = page.locator(".from-user").bounding_box()
                    assistant = page.locator(".from-assistant").bounding_box()
                    assert user["x"] > assistant["x"], (
                        "Patient messages must align right"
                    )
                    assert (
                        assistant["width"]
                        < page.locator("#messages").bounding_box()["width"] * 0.9
                    )
                    page.locator("#toasts").evaluate("(e)=>e.replaceChildren()")
                    page.screenshot(
                        path=str(root / ".runtime/chat-conversation.png"),
                        full_page=True,
                    )
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate(
                        "document.documentElement.scrollWidth<=window.innerWidth"
                    )
                    page.screenshot(
                        path=str(root / ".runtime/chat-mobile.png"), full_page=True
                    )
                    page.set_viewport_size({"width": 1440, "height": 1024})
                    page.get_by_role("button", name="Сегодня", exact=True).click()
                    expect(
                        page.get_by_role("heading", name="Сегодня", exact=True)
                    ).to_be_visible()
                    page.locator("#toasts").evaluate("(e)=>e.replaceChildren()")
                    page.screenshot(
                        path=str(root / ".runtime/redesign-home.png"), full_page=True
                    )
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate(
                        "document.documentElement.scrollWidth<=window.innerWidth"
                    ), "Mobile page overflows"
                    page.screenshot(
                        path=str(root / ".runtime/redesign-mobile.png"), full_page=True
                    )
                    assert not errors, errors
                    browser.close()
                    print(
                        "PASS: demo prescription → Eapteka → confirmation → calendar; personal course; documents + expiry; observations; chat UI; mobile. No JS errors."
                    )
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == "__main__":
    run()
