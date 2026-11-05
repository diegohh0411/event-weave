from dataclasses import replace
from hashlib import sha256
import json

import pytest
from conftest import doc_unit
from fakes import SPECS
from weave_core.adapters import StageResult
from weave_core.cache import cache_key, lookup
from weave_core.db import session_scope
from weave_core.queue import Queue
from weave_core.stages import Capability, Stage


@pytest.fixture
def spec_v1():
    return SPECS[0].spec


@pytest.fixture
def spec_v2(spec_v1):
    return replace(spec_v1, version="2")


def test_same_inputs_same_key(spec_v1):
    assert cache_key("h", spec_v1, {"a": 1}, {}) == cache_key("h", spec_v1, {"a": 1}, {})


@pytest.mark.parametrize("change", ["input", "version", "settings", "deployment"])
def test_any_change_new_key(spec_v1, spec_v2, change):
    base = cache_key("h", spec_v1, {"a": 1}, {Capability.llm: "m1"})
    other = {
        "input": cache_key("h2", spec_v1, {"a": 1}, {Capability.llm: "m1"}),
        "version": cache_key("h", spec_v2, {"a": 1}, {Capability.llm: "m1"}),
        "settings": cache_key("h", spec_v1, {"a": 2}, {Capability.llm: "m1"}),
        "deployment": cache_key("h", spec_v1, {"a": 1}, {Capability.llm: "m2"}),
    }[change]
    assert base != other


def test_key_canonical_json(spec_v1):
    key = cache_key('h', spec_v1, {'b': 2, 'a': 1}, {Capability.llm: 'm1'})
    payload = {'input': 'h', 'adapter': spec_v1.name, 'version': spec_v1.version, 'settings': {'a': 1, 'b': 2}, 'deployments': {'llm': 'm1'}}
    assert key == sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert key == cache_key('h', spec_v1, {'a': 1, 'b': 2}, {Capability.llm: 'm1'})


@pytest.mark.parametrize('emits', [None, [], [doc_unit('d1', 0, 't1')]])
def test_lookup_roundtrip(service, started_run, emits):
    queue = Queue(service.engine)
    [job] = queue.claim(Stage.provide, 1, 'w')
    assert queue.complete(job, StageResult({'n': 1}, emits), 'k1', spec=SPECS[0].spec)
    with session_scope(service.engine) as session:
        hit = lookup(session, 'k1')
        assert lookup(session, 'missing') is None
    assert hit.output == {'n': 1}
    assert hit.emits == emits
    assert hit.documents == []
