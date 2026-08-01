from __future__ import annotations

from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from sforge.harness.backend.base import StreamingOutputCapture
from sforge.harness.backend.docker_backend import DockerBackend


def test_streaming_output_capture_preserves_unbounded_output() -> None:
    capture = StreamingOutputCapture()

    capture.append(b"first")
    capture.append(b"-second")

    assert capture.value() == b"first-second"


def test_streaming_output_capture_bounds_tail_and_reports_omission() -> None:
    capture = StreamingOutputCapture(max_bytes=8)

    capture.append(b"first-")
    capture.append(b"second")

    output = capture.value()
    assert b"4 earlier output bytes omitted" in output
    assert output.endswith(b"t-second")


def test_streaming_output_capture_requires_positive_limit() -> None:
    with pytest.raises(ValueError, match="positive"):
        StreamingOutputCapture(max_bytes=0)


def test_docker_write_to_container_executes_heredoc_through_shell() -> None:
    commands: list[list[str]] = []

    class FakeContainer:
        def exec_run(self, command):
            commands.append(command)
            return SimpleNamespace(exit_code=0, output=b"")

    backend = object.__new__(DockerBackend)
    backend._raw = lambda handle: FakeContainer()

    backend.write_to_container(
        object(),
        '{"providers": {}}',
        PurePosixPath("/home/agent/.pi/agent/models.json"),
    )

    assert commands[0][:2] == ["/bin/sh", "-c"]
    assert "> /home/agent/.pi/agent/models.json" in commands[0][2]
