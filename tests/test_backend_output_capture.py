from __future__ import annotations

import pytest

from sforge.harness.backend.base import StreamingOutputCapture


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
