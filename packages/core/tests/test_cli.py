import json

import pytest
from typer.testing import CliRunner

from fakes import SPECS
from weave_core.stages import Stage


@pytest.fixture
def cli(monkeypatch, database_url, tmp_path):
    from weave_core.cli import app
    from weave_core.registry import Registry

    monkeypatch.setenv('WEAVE_DATABASE_URL', database_url)
    deployments = tmp_path / 'deployments.yaml'
    deployments.write_text(json.dumps({'good-extractor': ['extraction', True]}))
    monkeypatch.setenv('WEAVE_DEPLOYMENTS_FILE', str(deployments))
    def load(self, group='weave.adapters'):
        for cls in SPECS:
            self.register(cls)
    monkeypatch.setattr(Registry, 'load_entry_points', load)
    runner = CliRunner()
    return lambda args: runner.invoke(app, args)


@pytest.fixture
def config_file(tmp_path, good_config):
    path = tmp_path / 'config.yaml'
    path.write_text(good_config.model_dump_json())
    return path


@pytest.fixture
def bad_config_file(tmp_path, good_config):
    del good_config.stages[Stage.cluster]
    path = tmp_path / 'bad.yaml'
    path.write_text(good_config.model_dump_json())
    return path


def created_id(result):
    return result.stdout.splitlines()[0].split()[1]


def test_create_prints_hash_and_no_estimate(cli, config_file):
    r = cli(['run', 'create', str(config_file)])
    assert r.exit_code == 0 and 'hash ' in r.stdout and 'estimate: none yet' in r.stdout


def test_create_invalid_exits_2_with_problems(cli, bad_config_file):
    r = cli(['run', 'create', str(bad_config_file)])
    assert r.exit_code == 2 and 'missing stage: cluster' in r.stderr


def test_status_lines(cli, config_file):
    run_id = created_id(cli(['run', 'create', str(config_file)]))
    cli(['run', 'start', run_id])
    out = cli(['run', 'status', run_id]).stdout
    assert 'running' in out and 'provide: pending=1 claimed=0 done=0 failed=0' in out


def test_resume_from_created_exits_1(cli, config_file):
    run_id = created_id(cli(['run', 'create', str(config_file)]))
    assert cli(['run', 'resume', run_id]).exit_code == 1
