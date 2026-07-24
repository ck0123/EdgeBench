from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "run_codex_only.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _launcher_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "environment.txt"
    _write_executable(fake_bin / "docker", "#!/bin/sh\nexit 0\n")
    _write_executable(fake_bin / "curl", "#!/bin/sh\nexit 0\n")
    fake_python = tmp_path / "python"
    _write_executable(
        fake_python,
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = -c ]; then exit 0; fi\n"
        "env | sort > \"$SFORGE_TEST_CAPTURE\"\n",
    )
    env = dict(os.environ)
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "CODEX_API_KEY",
        "SFORGE_AGENT_API_KEY",
        "SFORGE_AGENT_API_BASE_URL",
    ):
        env.pop(name, None)
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "DOCKER_HOST": "unix:///run/user/1000/docker.sock",
            "SFORGE_PYTHON": str(fake_python),
            "SFORGE_CODEX_RUNTIME_ARCHIVE": "",
            "SFORGE_TEST_CAPTURE": str(capture),
        }
    )
    return env, capture


def test_launcher_maps_host_local_openai_proxy(tmp_path: Path) -> None:
    env, capture = _launcher_environment(tmp_path)
    env.update(
        {
            "OPENAI_API_KEY": "test-local-token",
            "OPENAI_BASE_URL": "http://127.0.0.1:3788/v1",
            "SFORGE_CODEX_AUTH_FILE": str(tmp_path / "missing-auth.json"),
        }
    )

    completed = subprocess.run(
        ["bash", str(LAUNCHER)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    captured = capture.read_text(encoding="utf-8")
    assert "SFORGE_AGENT_API_KEY=test-local-token" in captured
    assert (
        "SFORGE_AGENT_API_BASE_URL=http://host.docker.internal:3788/v1"
        in captured
    )
    assert "Codex authentication: API key" in completed.stdout
    assert "test-local-token" not in completed.stdout


def test_launcher_keeps_oauth_mode_without_api_key(tmp_path: Path) -> None:
    env, _ = _launcher_environment(tmp_path)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"tokens": {}}\n', encoding="utf-8")
    env["SFORGE_CODEX_AUTH_FILE"] = str(auth_file)

    completed = subprocess.run(
        ["bash", str(LAUNCHER)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Codex authentication: OAuth auth.json" in completed.stdout


def test_launcher_allows_disabling_cpu_quotas(tmp_path: Path) -> None:
    env, _ = _launcher_environment(tmp_path)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"tokens": {}}\n', encoding="utf-8")
    env.update(
        {
            "SFORGE_CODEX_AUTH_FILE": str(auth_file),
            "WORK_CPU_LIMIT": "0",
            "JUDGE_CPU_LIMIT": "0",
        }
    )

    completed = subprocess.run(
        ["bash", str(LAUNCHER)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "CPU work/judge: unlimited/unlimited" in completed.stdout
