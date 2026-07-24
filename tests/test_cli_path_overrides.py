from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

from sforge.cli import _effective_config_dict
from sforge.harness.config import SForgeConfig


def test_global_tasks_dir_cli_override_is_a_path(tmp_path) -> None:
    (tmp_path / "BENCHMARK.yaml").write_text("name: test\nbase_images: {}\n")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "sforge.cli",
            "--tasks-dir",
            str(tmp_path),
            "list",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "test" in completed.stdout


def test_effective_run_config_never_persists_api_key() -> None:
    task_spec = SimpleNamespace(task_id="test-task", internet=True)
    args = SimpleNamespace(
        agent="codex",
        model="gpt-test",
        timeout=60,
        eval_interval=30,
    )
    config = SForgeConfig(
        agent_api_key="secret-api-key",
        agent_api_base_url="http://host.docker.internal:3788/v1",
    )

    result = _effective_config_dict(task_spec, args, config)

    assert result["api_key_configured"] is True
    assert "api_key" not in result
    assert "secret-api-key" not in json.dumps(result)
