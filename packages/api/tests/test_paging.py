import json
import uuid
from datetime import datetime, timezone
from typing import Annotated

import sqlalchemy as sa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from weave_api.main import create_app
from weave_api.paging import Page, Paging, paginate
from weave_core.schema import runs


def seed_runs(engine, n):
    with engine.begin() as c:
        for i in range(n):
            c.execute(sa.insert(runs).values(id=uuid.uuid4(), config={"i": i}, config_hash=f"h{i:02d}", state="created",
                                             created_at=datetime.now(timezone.utc)))


def test_paginate_counts_and_slices(engine):
    seed_runs(engine, 25)
    query = sa.select(runs.c.config_hash).order_by(runs.c.config_hash)
    with engine.connect() as conn:
        page = paginate(conn, query, Paging(offset=20, limit=10), lambda r: r.config_hash)
    assert isinstance(page, Page)
    assert page.total == 25 and page.offset == 20 and page.limit == 10
    assert page.items == [f"h{i:02d}" for i in range(20, 25)]


def test_limit_over_200_is_422():
    app = FastAPI()

    @app.get("/things")
    def things(paging: Annotated[Paging, Depends()]):
        return {"offset": paging.offset, "limit": paging.limit}

    client = TestClient(app)
    assert client.get("/things?limit=201").status_code == 422
    assert client.get("/things?limit=0").status_code == 422
    assert client.get("/things?offset=-1").status_code == 422
    assert client.get("/things").json() == {"offset": 0, "limit": 50}
    assert client.get("/things?limit=200").status_code == 200


def test_error_handlers(service):
    from sqlalchemy.exc import NoResultFound
    from weave_core.config import ConfigError
    from weave_core.runs import RunStateError

    app = create_app(service)

    @app.get("/boom/{kind}")
    def boom(kind: str):
        raise {"config": ConfigError(["a", "b"]), "state": RunStateError("bad state"), "none": NoResultFound()}[kind]

    with TestClient(app) as client:
        r = client.get("/boom/config")
        assert r.status_code == 422 and r.json() == {"problems": ["a", "b"]}
        r = client.get("/boom/state")
        assert r.status_code == 409 and r.json() == {"detail": "bad state"}
        assert client.get("/boom/none").status_code == 404
        assert client.get("/health").json()["status"] == "ok"


def test_openapi_export_does_not_enter_lifespan(capsys):
    from weave_api.openapi import main

    main()
    schema = json.loads(capsys.readouterr().out)
    assert "/health" in schema["paths"]
