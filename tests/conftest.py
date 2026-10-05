"""测试环境：独立临时数据库与上传目录（必须在导入 app 之前设置环境变量）。"""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="rental-test-")
os.environ["RENTAL_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"
os.environ["RENTAL_UPLOAD_DIR"] = os.path.join(_tmp, "uploads")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    seed_if_empty()
    yield


@pytest.fixture
def client():
    return TestClient(app)
