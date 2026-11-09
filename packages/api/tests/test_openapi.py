import json
from pathlib import Path


from weave_api.main import create_app

SNAPSHOT = Path(__file__).resolve().parents[3] / "web" / "openapi.json"


def test_every_list_route_pages():
    paths = create_app(service=None).openapi()["paths"]
    checked = 0
    for path, item in paths.items():
        if "get" not in item:
            continue
        op = item["get"]
        schema = op["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema.get("type") != "array", f"GET {path} returns a bare list; return Page[...]"
        if schema.get("$ref", "").rsplit("/", 1)[-1].startswith("Page_"):
            names = {p["name"] for p in op.get("parameters", [])}
            assert {"offset", "limit"} <= names, f"GET {path} is a Page but does not accept offset and limit"
            checked += 1
    assert checked >= 6


def test_openapi_snapshot():
    expected = json.loads(SNAPSHOT.read_text())
    assert create_app(service=None).openapi() == expected, "OpenAPI changed; run: uv run python -m weave_api.openapi > web/openapi.json"
