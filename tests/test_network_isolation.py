from __future__ import annotations

import subprocess

from sforge.harness import network_isolation


def test_macos_iptables_probe_enters_the_docker_vm(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(network_isolation.sys, "platform", "darwin")
    monkeypatch.setattr(network_isolation.subprocess, "run", fake_run)

    assert network_isolation.check_iptables_permission()
    assert commands == [
        [
            "docker",
            "run",
            "--rm",
            "--pull",
            "never",
            "--privileged",
            "--pid",
            "host",
            "--network",
            "host",
            "--entrypoint",
            "/usr/bin/nsenter",
            network_isolation.DOCKER_VM_IPTABLES_HELPER_IMAGE,
            "-t",
            "1",
            "-m",
            "-n",
            "--",
            "iptables",
            "-L",
            "INPUT",
            "-n",
        ]
    ]


def test_linux_iptables_probe_uses_passwordless_sudo(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(network_isolation.sys, "platform", "linux")
    monkeypatch.setattr(network_isolation.subprocess, "run", fake_run)

    assert network_isolation.check_iptables_permission()
    assert commands == [["sudo", "-n", "iptables", "-L", "INPUT", "-n"]]
