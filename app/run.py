from pathlib import Path

import uvicorn

if __name__ == "__main__":
    env_file = Path(__file__).resolve().parents[1] / ".env"
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        env_file=str(env_file) if env_file.exists() else None,
    )
