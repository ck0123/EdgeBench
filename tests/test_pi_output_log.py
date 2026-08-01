from __future__ import annotations

import json
from types import SimpleNamespace

from sforge.harness.agent.pi import PiAgent, PiJsonOutputLogFilter
from sforge.harness.backend.docker_backend import DockerBackend
from sforge.harness.backend.k8s_backend import K8sBackend
from sforge.harness.config import SForgeConfig


def _event_line(event: dict, *, newline: bool = True) -> bytes:
    suffix = b"\n" if newline else b""
    return json.dumps(event, separators=(",", ":")).encode() + suffix


def _delta_event(content: str = "x" * 10_000) -> dict:
    message = {
        "role": "assistant",
        "provider": "zai",
        "model": "glm-5.2",
        "content": [{"type": "thinking", "thinking": content}],
    }
    return {
        "type": "message_update",
        "message": message,
        "assistantMessageEvent": {
            "type": "thinking_delta",
            "contentIndex": 0,
            "delta": "next",
            "partial": message,
        },
    }


def test_pi_json_log_filter_keeps_delta_without_duplicate_snapshots() -> None:
    raw = _event_line(_delta_event())
    log_filter = PiJsonOutputLogFilter()

    compacted = b"".join(
        (
            log_filter.feed(raw[:17]),
            log_filter.feed(raw[17:503]),
            log_filter.feed(raw[503:]),
            log_filter.finish(),
        )
    )

    event = json.loads(compacted)
    assert event == {
        "type": "message_update",
        "assistantMessageEvent": {
            "type": "thinking_delta",
            "contentIndex": 0,
            "delta": "next",
        },
        "sforge_compacted": True,
    }
    assert len(compacted) < len(raw) / 100


def test_pi_json_log_filter_preserves_complete_and_unrecognized_events() -> None:
    message_end = _event_line(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "complete"}],
            },
        }
    )
    malformed = b"not-json\n"
    nonduplicate_delta = _delta_event("partial")
    nonduplicate_delta["assistantMessageEvent"]["partial"] = json.loads(
        json.dumps(nonduplicate_delta["message"])
    )
    nonduplicate_delta["message"]["content"][0]["thinking"] = "different"
    nonduplicate = _event_line(nonduplicate_delta, newline=False)
    log_filter = PiJsonOutputLogFilter()

    output = log_filter.feed(message_end + malformed + nonduplicate)
    output += log_filter.finish()

    assert output == message_end + malformed + nonduplicate


def test_pi_agent_enables_json_log_compaction() -> None:
    agent = PiAgent(SForgeConfig())

    assert isinstance(agent.create_output_log_filter(), PiJsonOutputLogFilter)


def test_docker_backend_filters_only_the_persisted_log(tmp_path) -> None:
    raw = _event_line(_delta_event())

    class FakeApi:
        def exec_create(self, container_id, command, **kwargs):
            return {"Id": "exec-id"}

        def exec_start(self, exec_id, stream):
            return iter((raw[:31], raw[31:]))

        def exec_inspect(self, exec_id):
            return {"ExitCode": 0}

    container = SimpleNamespace(
        id="container-id",
        client=SimpleNamespace(api=FakeApi()),
    )
    backend = object.__new__(DockerBackend)
    backend._raw = lambda handle: container
    log_path = tmp_path / "agent_output.txt"

    output, exit_code, timed_out, _elapsed = backend._exec_run_impl(
        object(),
        ["pi", "--mode", "json"],
        timeout=5,
        log_file=log_path,
        output_log_filter=PiJsonOutputLogFilter(),
    )

    assert output == raw.decode()
    assert exit_code == 0
    assert timed_out is False
    persisted = json.loads(log_path.read_text())
    assert persisted["assistantMessageEvent"]["delta"] == "next"
    assert persisted["sforge_compacted"] is True
    assert "message" not in persisted
    assert "partial" not in persisted["assistantMessageEvent"]


def test_k8s_backend_filters_only_the_persisted_log(tmp_path, monkeypatch) -> None:
    raw = _event_line(_delta_event())

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = SimpleNamespace(
                read=self._read,
            )
            self._chunks = iter((raw[:43], raw[43:], b"__SFORGE_EXIT__0\n"))

        def _read(self, size):
            return next(self._chunks, b"")

        def wait(self):
            return 0

    monkeypatch.setattr(
        "sforge.harness.backend.k8s_backend.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    backend = object.__new__(K8sBackend)
    backend._kubectl_base = ["kubectl"]
    backend._handle = lambda handle: SimpleNamespace(name="work-pod")
    backend._build_shell_cmd = lambda *args, **kwargs: "pi --mode json"
    log_path = tmp_path / "agent_output.txt"

    output, exit_code, timed_out, _elapsed = backend._exec_streaming(
        object(),
        ["pi", "--mode", "json"],
        timeout=5,
        log_file=log_path,
        output_log_filter=PiJsonOutputLogFilter(),
    )

    assert output == raw.decode()
    assert exit_code == 0
    assert timed_out is False
    persisted = json.loads(log_path.read_text().splitlines()[0])
    assert persisted["assistantMessageEvent"]["delta"] == "next"
    assert persisted["sforge_compacted"] is True
    assert "message" not in persisted
    assert "partial" not in persisted["assistantMessageEvent"]
