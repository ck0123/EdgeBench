from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from sforge.harness.config import load_config
from sforge.harness import network_isolation


def test_config_parses_deduplicated_api_endpoint_list(monkeypatch) -> None:
    urls = [
        "https://api.openai.com/v1",
        "http://host.docker.internal:4101/v1",
        "https://api.openai.com/v1",
    ]
    monkeypatch.setenv("SFORGE_AGENT_API_BASE_URLS", json.dumps(urls))

    config = load_config()

    assert config.agent_api_base_urls == urls[:2]


@pytest.mark.parametrize("value", ["not-json", "{}", '["", 7]'])
def test_config_rejects_malformed_api_endpoint_list(monkeypatch, value) -> None:
    monkeypatch.setenv("SFORGE_AGENT_API_BASE_URLS", value)

    with pytest.raises(ValueError, match="SFORGE_AGENT_API_BASE_URLS"):
        load_config()


def test_allowlist_contains_judge_and_every_llm_api_endpoint(monkeypatch) -> None:
    resolved = {
        "api.openai.com": ["192.0.2.20"],
        "api.anthropic.com": ["192.0.2.21"],
    }
    monkeypatch.setattr(
        network_isolation,
        "resolve_hostname",
        lambda hostname, _logger: resolved[hostname],
    )

    endpoints = network_isolation.build_allowed_endpoints(
        "http://host.docker.internal:8080",
        [
            "https://api.openai.com/v1",
            "https://api.anthropic.com",
            "https://api.openai.com/responses",
        ],
        "192.0.2.1",
        Mock(),
    )

    assert [(endpoint.ip, endpoint.port) for endpoint in endpoints] == [
        ("192.0.2.1", 8080),
        ("192.0.2.20", 443),
        ("192.0.2.21", 443),
    ]


@pytest.mark.parametrize(
    "url",
    ["ftp://api.example.com/model", "https://user:secret@api.example.com"],
)
def test_allowlist_rejects_unsafe_llm_api_urls(url) -> None:
    with pytest.raises(RuntimeError):
        network_isolation.build_allowed_endpoints(
            "http://host.docker.internal:8080",
            [url],
            "192.0.2.1",
            Mock(),
        )
