import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TMP = Path(tempfile.mkdtemp(prefix="fridge-test-"))
os.environ["FRIDGE_DATA_DIR"] = str(TMP)
os.environ["DETECTOR"] = "demo"
os.environ["DATABASE_URL"] = f"sqlite:///{TMP / 'fridge.db'}"
os.environ["CAMERA_URL"] = ""

from app.config import get_settings
from app.main import app
from app.models import init_db

get_settings.cache_clear()
init_db()


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
