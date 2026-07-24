from __future__ import annotations

import inspect
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from sforge.harness.backend import ExecResult
from sforge.harness.container_runtime import ensure_task_runtime
from sforge.harness import run_agent, run_evaluation


class FakeBackend:
    def __init__(self, results: list[ExecResult]) -> None:
        self.results = list(results)
        self.commands = []
        self.copies = []

    def exec_run(self, handle, command, *, user=None) -> ExecResult:
        self.commands.append((command, user))
        return self.results.pop(0)

    def copy_to_container(self, handle, source, destination) -> None:
        self.copies.append((source, str(destination)))


def _task(base_image: str):
    return SimpleNamespace(base_image=base_image)


def test_non_rust_task_skips_runtime_setup() -> None:
    backend = FakeBackend([])

    ensure_task_runtime(
        backend, object(), _task("python310"), logging.getLogger(__name__)
    )

    assert backend.commands == []
    assert backend.copies == []


def test_existing_pinned_rust_runtime_skips_archive() -> None:
    backend = FakeBackend([ExecResult(output="rustc 1.88.0", exit_code=0)])

    ensure_task_runtime(
        backend, object(), _task("rust"), logging.getLogger(__name__)
    )

    assert len(backend.commands) == 1
    assert backend.copies == []


def test_missing_rust_runtime_installs_host_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "rust-runtime.tar.xz"
    archive.write_bytes(b"pinned rust runtime")
    monkeypatch.setenv("SFORGE_RUST_RUNTIME_ARCHIVE", str(archive))
    backend = FakeBackend(
        [
            ExecResult(output="cargo: not found", exit_code=1),
            ExecResult(output="installed", exit_code=0),
            ExecResult(output="rustc 1.88.0", exit_code=0),
        ]
    )

    ensure_task_runtime(
        backend, object(), _task("rust"), logging.getLogger(__name__)
    )

    assert backend.copies == [
        (archive.resolve(), "/tmp/sforge-rust-runtime.tar.xz")
    ]
    install_command, user = backend.commands[1]
    assert user == "root"
    assert "--without=rust-docs" in install_command[-1]
    assert "/opt/sforge-rust" in install_command[-1]


def test_missing_override_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing.tar.xz"
    monkeypatch.setenv("SFORGE_RUST_RUNTIME_ARCHIVE", str(missing))
    backend = FakeBackend([ExecResult(exit_code=1)])

    with pytest.raises(RuntimeError, match="Rust runtime archive not found"):
        ensure_task_runtime(
            backend, object(), _task("rust"), logging.getLogger(__name__)
        )


def test_custom_runtime_checksum_is_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "rust-runtime.tar.xz"
    archive.write_bytes(b"unexpected runtime")
    monkeypatch.setenv("SFORGE_RUST_RUNTIME_ARCHIVE", str(archive))
    monkeypatch.setenv("SFORGE_RUST_RUNTIME_SHA256", "0" * 64)
    backend = FakeBackend([ExecResult(exit_code=1)])

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        ensure_task_runtime(
            backend, object(), _task("rust"), logging.getLogger(__name__)
        )

    assert backend.copies == []


def test_empty_override_disables_missing_runtime_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SFORGE_RUST_RUNTIME_ARCHIVE", "")
    backend = FakeBackend([ExecResult(exit_code=1)])

    with pytest.raises(RuntimeError, match="injection was disabled"):
        ensure_task_runtime(
            backend, object(), _task("rust"), logging.getLogger(__name__)
        )


def test_work_and_judge_lifecycles_call_shared_runtime_helper() -> None:
    assert "ensure_task_runtime(" in inspect.getsource(run_agent.run_agent)
    assert "ensure_task_runtime(" in inspect.getsource(
        run_evaluation.judge_submission
    )
