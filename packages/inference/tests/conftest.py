import os
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core" / "tests"))
from pg import database  # noqa: E402
from weave_core.db import make_engine, migrate  # noqa: E402


@pytest.fixture
def engine():
    with database("weave_inf_" + uuid.uuid4().hex[:8]) as url:
        migrate(url)
        eng = make_engine(url)
        try:
            yield eng
        finally:
            eng.dispose()
