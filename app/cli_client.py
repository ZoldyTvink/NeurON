import sys

from app.db.models import SessionLocal, init_db
from app.db.seed import seed_if_empty
from app.dialogue.manager import handle_message


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        seed_if_empty(db)
    finally:
        db.close()

    user_id = sys.argv[1] if len(sys.argv) > 1 else "demo-user"
    print(f"Сессия пользователя: {user_id}. Ctrl+C или 'выход' для выхода.\n")
    while True:
        try:
            text = input("Вы: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text.lower() in {"выход", "exit", "quit"}:
            break
        if not text:
            continue
        reply = handle_message(user_id, text)
        print(f"Бот: {reply}\n")


if __name__ == "__main__":
    main()
