from fastapi.testclient import TestClient

from weave_api.main import create_app
from weave_core.config import StaticCatalog
from weave_core.registry import Registry
from weave_core.runs import RunService
from weave_core.stages import Capability


def static():
    return StaticCatalog({"a@stub": (Capability.llm, True), "b@stub": (Capability.llm, False),
                          "c@stub": (Capability.embeddings, True)})


def test_deployments_filter_ready_and_capability(engine):
    with TestClient(create_app(RunService(engine, Registry(), static()))) as c:
        body = c.get("/deployments", params={"capability": "llm", "ready": "true"}).json()
        everything = c.get("/deployments").json()
        not_ready = c.get("/deployments", params={"ready": "false"}).json()
    assert [i["id"] for i in body["items"]] == ["a@stub"]
    assert body["items"][0] == {"id": "a@stub", "capability": "llm", "ready": True, "reason": None, "is_default": False, "local": False}
    assert everything["total"] == 3
    assert [i["id"] for i in not_ready["items"]] == ["b@stub"]


def test_probe_without_inference_catalog_is_409(engine):
    with TestClient(create_app(RunService(engine, Registry(), static()))) as c:
        r = c.post("/deployments/probe")
    assert r.status_code == 409 and r.json() == {"detail": "no inference catalog configured"}


def test_catalog_readiness_defaults_and_probe(engine, monkeypatch):
    from weave_inference.catalog import Catalog
    from weave_inference.probe import Readiness
    from weave_inference.types import Deployment, Model, Provider

    catalog = Catalog(
        models={"m": Model(id="m", capability=Capability.llm)},
        providers={"p": Provider(id="p", kind="openai", base_url="http://x")},
        deployments={"d1": Deployment(id="d1", model="m", provider="p", local=True), "d2": Deployment(id="d2", model="m", provider="p")},
        defaults={Capability.llm: "d1"},
        readiness={"d1": Readiness(True), "d2": Readiness(False, "unreachable")})
    probed = {"d1": Readiness(False, "down"), "d2": Readiness(True)}
    monkeypatch.setattr("weave_api.routers.deployments.probe", lambda cat: probed)
    with TestClient(create_app(RunService(engine, Registry(), catalog))) as c:
        items = {i["id"]: i for i in c.get("/deployments").json()["items"]}
        assert items["d1"]["is_default"] is True and items["d1"]["local"] is True and items["d1"]["ready"] is True
        assert items["d2"]["reason"] == "unreachable" and items["d2"]["ready"] is False
        after = {i["id"]: i for i in c.post("/deployments/probe").json()["items"]}
        assert after["d1"]["reason"] == "down" and after["d2"]["ready"] is True
        assert c.get("/deployments").json()["items"][0]["reason"] == "down"
