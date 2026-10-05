import os
import tempfile

_tmp_fd, _tmp_path = tempfile.mkstemp(suffix=".db")
os.close(_tmp_fd)
os.environ["MEDITRON_DB_URL"] = f"sqlite:///{_tmp_path}"

import pytest

from app.db.models import Base, SessionLocal, engine
from app.db.seed import seed_if_empty


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_if_empty(db)
    finally:
        db.close()
    yield
