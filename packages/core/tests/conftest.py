import uuid
import pytest
from pg import database
from weave_core.db import migrate

@pytest.fixture(scope="session")
def template_database():
    with database("weave_template") as url:
        migrate(url)
        yield "weave_template"

@pytest.fixture
def database_url(template_database):
    with database("weave_test_" + uuid.uuid4().hex[:8], template_database) as url:
        yield url
