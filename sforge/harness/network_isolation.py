# Copyright (c) 2026 ByteDance Ltd. and/or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Host-side iptables network isolation for work containers.

Restricts a container's outbound traffic to a whitelist of TCP endpoints
(judge server + API) by installing per-container iptables/ip6tables chains.
Rules live on the *host* network namespace and cannot be modified from inside
the container (no NET_ADMIN capability).

IPv6 is blocked entirely — whitelisted endpoints are resolved to IPv4 and
injected into the container's /etc/hosts via extra_hosts at creation time.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

import docker


DOCKER_VM_IPTABLES_HELPER_IMAGE = "ubuntu:22.04"


@dataclass
class AllowedEndpoint:
    ip: str
    port: int
    hostname: str | None = None


class NetworkIsolation:
    """Manages per-container iptables chains that whitelist specific endpoints."""

    def __init__(
        self,
        container: docker.models.containers.Container,
        endpoints: list[AllowedEndpoint],
        logger: logging.Logger,
    ) -> None:
        self._container = container
        self._endpoints = endpoints
        self._logger = logger
        self._chain = f"SFORGE_{container.id[:12]}"
        self._applied = False

        container.reload()
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})

        self._container_ip: str | None = None
        self._container_ip6: str | None = None
        self._bridge_iface = "docker0"

        for net_name, net_info in networks.items():
            if not self._container_ip:
                ip = net_info.get("IPAddress", "")
                if ip:
                    self._container_ip = ip
            if not self._container_ip6:
                ip6 = net_info.get("GlobalIPv6Address", "")
                if ip6:
                    self._container_ip6 = ip6
            net_id = net_info.get("NetworkID", "")
            if net_id:
                try:
                    net_obj = container.client.networks.get(net_id)
                    bridge_name = (
                        net_obj.attrs.get("Options", {})
                        .get("com.docker.network.bridge.name", "")
                    )
                    if bridge_name:
                        self._bridge_iface = bridge_name
                except Exception:
                    pass

        if not self._container_ip:
            raise RuntimeError(
                "Cannot determine container IPv4 address for network isolation"
            )

    def apply(self) -> None:
        if self._applied:
            return

        self._create_chain_v4()
        self._install_jumps_v4()

        if self._container_ip6:
            self._create_chain_v6()
            self._install_jumps_v6()

        self._applied = True
        self._logger.info(
            f"Network isolation applied: chain={self._chain}, "
            f"ip={self._container_ip}, ip6={self._container_ip6}, "
            f"allowed={len(self._endpoints)} endpoints"
        )

    def cleanup(self) -> None:
        if not self._applied:
            return

        for label, remover in [
            ("v4 jumps", self._remove_jumps_v4),
            ("v4 chain", self._remove_chain_v4),
            ("v6 jumps", self._remove_jumps_v6),
            ("v6 chain", self._remove_chain_v6),
        ]:
            try:
                remover()
            except Exception as exc:
                self._logger.warning(f"Failed to remove {label}: {exc}")

        self._applied = False
        self._logger.info(f"Network isolation cleaned up: chain={self._chain}")

    # -- IPv4 ----------------------------------------------------------------

    def _create_chain_v4(self) -> None:
        _iptables(["-N", self._chain])
        _iptables([
            "-A", self._chain,
            "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED",
            "-j", "RETURN",
        ])
        for ep in self._endpoints:
            _iptables([
                "-A", self._chain,
                "-d", ep.ip, "-p", "tcp", "--dport", str(ep.port),
                "-j", "RETURN",
            ])
        _iptables(["-A", self._chain, "-j", "DROP"])

    def _install_jumps_v4(self) -> None:
        if _chain_exists("DOCKER-USER", v6=False):
            _iptables([
                "-I", "DOCKER-USER",
                "-s", self._container_ip, "-j", self._chain,
            ])
        _iptables([
            "-I", "INPUT",
            "-i", self._bridge_iface,
            "-s", self._container_ip, "-j", self._chain,
        ])

    def _remove_jumps_v4(self) -> None:
        if _chain_exists("DOCKER-USER", v6=False):
            _iptables([
                "-D", "DOCKER-USER",
                "-s", self._container_ip, "-j", self._chain,
            ])
        _iptables([
            "-D", "INPUT",
            "-i", self._bridge_iface,
            "-s", self._container_ip, "-j", self._chain,
        ])

    def _remove_chain_v4(self) -> None:
        _iptables(["-F", self._chain])
        _iptables(["-X", self._chain])

    # -- IPv6 (block everything) ---------------------------------------------

    def _create_chain_v6(self) -> None:
        _iptables(["-N", self._chain], v6=True)
        _iptables([
            "-A", self._chain,
            "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED",
            "-j", "RETURN",
        ], v6=True)
        _iptables(["-A", self._chain, "-j", "DROP"], v6=True)

    def _install_jumps_v6(self) -> None:
        ip6 = self._container_ip6
        if _chain_exists("DOCKER-USER", v6=True):
            _iptables([
                "-I", "DOCKER-USER", "-s", ip6, "-j", self._chain,
            ], v6=True)
        _iptables([
            "-I", "INPUT",
            "-i", self._bridge_iface, "-s", ip6, "-j", self._chain,
        ], v6=True)
        _iptables([
            "-I", "FORWARD",
            "-i", self._bridge_iface, "-s", ip6, "-j", self._chain,
        ], v6=True)

    def _remove_jumps_v6(self) -> None:
        ip6 = self._container_ip6
        if not ip6:
            return
        if _chain_exists("DOCKER-USER", v6=True):
            _iptables([
                "-D", "DOCKER-USER", "-s", ip6, "-j", self._chain,
            ], v6=True)
        _iptables([
            "-D", "INPUT",
            "-i", self._bridge_iface, "-s", ip6, "-j", self._chain,
        ], v6=True)
        _iptables([
            "-D", "FORWARD",
            "-i", self._bridge_iface, "-s", ip6, "-j", self._chain,
        ], v6=True)

    def _remove_chain_v6(self) -> None:
        _iptables(["-F", self._chain], v6=True)
        _iptables(["-X", self._chain], v6=True)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _iptables_command(args: list[str], *, v6: bool = False) -> list[str]:
    program = "ip6tables" if v6 else "iptables"
    if sys.platform != "darwin":
        return ["sudo", "-n", program, *args]
    return [
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
        DOCKER_VM_IPTABLES_HELPER_IMAGE,
        "-t",
        "1",
        "-m",
        "-n",
        "--",
        program,
        *args,
    ]


def _iptables(args: list[str], *, v6: bool = False) -> None:
    cmd = _iptables_command(args, v6=v6)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"{'ip6tables' if v6 else 'iptables'} {' '.join(args)} failed: "
            f"{result.stderr.strip()}"
        )


def _chain_exists(chain: str, *, v6: bool = False) -> bool:
    cmd = _iptables_command(["-L", chain, "-n"], v6=v6)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def is_ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def resolve_hostname(hostname: str, logger: logging.Logger) -> list[str]:
    """Resolve a hostname to IPv4 addresses on the host."""
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_INET)
        ips = list(set(r[4][0] for r in results))
        logger.info(f"Resolved {hostname} -> {ips}")
        return ips
    except socket.gaierror as exc:
        raise RuntimeError(f"Failed to resolve hostname '{hostname}': {exc}")


def check_iptables_permission() -> bool:
    """Check host iptables, entering the Docker VM on macOS when needed."""
    try:
        result = subprocess.run(
            _iptables_command(["-L", "INPUT", "-n"]),
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def build_allowed_endpoints(
    judge_url: str,
    api_urls: list[str],
    gateway_ip: str,
    logger: logging.Logger,
    *,
    host_internal_ips: list[str] | None = None,
) -> list[AllowedEndpoint]:
    """Build a deduplicated whitelist from the Judge and LLM API URLs."""
    endpoints: list[AllowedEndpoint] = []

    def add_url(url: str, *, label: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise RuntimeError(f"Invalid {label} URL for network isolation: {url!r}")
        if parsed.username is not None or parsed.password is not None:
            raise RuntimeError(f"{label} URL must not contain credentials")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise RuntimeError(f"Invalid {label} URL port: {url!r}") from exc
        host = parsed.hostname
        if host == "host.docker.internal":
            for ip in dict.fromkeys([gateway_ip, *(host_internal_ips or [])]):
                if ip:
                    endpoints.append(AllowedEndpoint(ip=ip, port=port))
        elif is_ip_address(host):
            endpoints.append(AllowedEndpoint(ip=host, port=port))
        else:
            for ip in resolve_hostname(host, logger):
                endpoints.append(AllowedEndpoint(ip=ip, port=port, hostname=host))

    add_url(judge_url, label="Judge")
    for api_url in api_urls:
        add_url(api_url, label="LLM API")

    unique: dict[tuple[str, int], AllowedEndpoint] = {}
    for endpoint in endpoints:
        unique.setdefault((endpoint.ip, endpoint.port), endpoint)
    return list(unique.values())


def _remove_jumps_by_grep(
    prog: str, chain: str, logger: logging.Logger,
) -> None:
    """Remove all jump rules targeting *chain* from every parent chain.

    Uses ``iptables -S`` to get the exact rule specification (including ``-s``
    and ``-i`` flags) so that the ``-D`` command matches precisely — plain
    ``-D <parent> -j <chain>`` would fail because iptables requires an exact
    parameter match.
    """
    for parent in ("DOCKER-USER", "INPUT", "FORWARD"):
        result = subprocess.run(
            _iptables_command(["-S", parent], v6=prog == "ip6tables"),
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            if f"-j {chain}" not in line:
                continue
            # line looks like: -A DOCKER-USER -s 172.17.0.2/32 -j SFORGE_xxxx
            # Replace leading -A with -D to form the delete command.
            delete_args = line.split()
            if delete_args and delete_args[0] == "-A":
                delete_args[0] = "-D"
            subprocess.run(
                _iptables_command(
                    delete_args,
                    v6=prog == "ip6tables",
                ),
                capture_output=True,
            )


def cleanup_stale_chains(logger: logging.Logger) -> None:
    """Remove SFORGE_* iptables chains whose containers no longer exist."""
    try:
        client = docker.from_env()
        running_ids = {c.id[:12] for c in client.containers.list()}
    except Exception:
        return

    for v6 in (False, True):
        prog = "ip6tables" if v6 else "iptables"
        result = subprocess.run(
            _iptables_command(["-L", "-n"], v6=v6),
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            continue

        for chain in re.findall(r"Chain (SFORGE_[0-9a-f]{12})", result.stdout):
            cid = chain.removeprefix("SFORGE_")
            if cid in running_ids:
                continue
            logger.info(f"Removing stale {prog} chain: {chain}")
            _remove_jumps_by_grep(prog, chain, logger)
            subprocess.run(
                _iptables_command(["-F", chain], v6=v6),
                capture_output=True,
            )
            subprocess.run(
                _iptables_command(["-X", chain], v6=v6),
                capture_output=True,
            )
