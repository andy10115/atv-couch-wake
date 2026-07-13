"""Android TV discovery helpers."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class DeviceCandidate:
    host: str
    name: str = "Android TV"
    port: int = 6466
    source: str = "unknown"


async def discover_mdns(timeout: float = 3.0) -> list[DeviceCandidate]:
    """Discover Android TV Remote v2 services using mDNS."""
    try:
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf
    except ImportError as exc:  # pragma: no cover - dependency installed in production
        raise RuntimeError("zeroconf is not installed") from exc

    service_type = "_androidtvremote2._tcp.local."
    found: dict[str, DeviceCandidate] = {}
    pending: set[asyncio.Task[None]] = set()
    zc = AsyncZeroconf()

    async def resolve(name: str) -> None:
        info = AsyncServiceInfo(service_type, name)
        if not await info.async_request(zc.zeroconf, int(max(timeout, 1.0) * 1000)):
            return
        friendly = name.removesuffix(f".{service_type}").rstrip(".")
        for address in info.parsed_scoped_addresses():
            host = address.split("%")[0]
            try:
                if ipaddress.ip_address(host).version != 4:
                    continue
            except ValueError:
                continue
            found[host] = DeviceCandidate(host=host, name=friendly, port=info.port or 6466, source="mdns")

    def on_change(_zc: object, _service_type: str, name: str, state: object) -> None:
        if state is not ServiceStateChange.Added:
            return
        task = asyncio.create_task(resolve(name))
        pending.add(task)
        task.add_done_callback(pending.discard)

    browser = AsyncServiceBrowser(zc.zeroconf, service_type, handlers=[on_change])
    try:
        await asyncio.sleep(timeout)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        await browser.async_cancel()
        await zc.async_close()
    return sorted(found.values())


def _run_ip_json(arguments: list[str]) -> list[dict[str, object]]:
    try:
        result = subprocess.run(
            ["ip", "-j", *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        parsed = json.loads(result.stdout or "[]")
        return parsed if isinstance(parsed, list) else []
    except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError):
        return []


def local_ipv4_networks() -> list[ipaddress.IPv4Network]:
    """Return directly attached IPv4 networks, capped to safe scan sizes."""
    networks: set[ipaddress.IPv4Network] = set()
    for item in _run_ip_json(["-4", "route", "show", "scope", "link"]):
        dst = str(item.get("dst", ""))
        if not dst or dst == "default":
            continue
        try:
            net = ipaddress.ip_network(dst, strict=False)
        except ValueError:
            continue
        if isinstance(net, ipaddress.IPv4Network) and not net.is_loopback:
            # Do not accidentally probe thousands of addresses. Narrow broad LANs to /24
            # around the preferred source address when possible.
            if net.prefixlen < 24:
                preferred = item.get("prefsrc")
                if preferred:
                    try:
                        net = ipaddress.ip_network(f"{preferred}/24", strict=False)
                    except ValueError:
                        continue
                else:
                    continue
            networks.add(net)

    if networks:
        return sorted(networks, key=str)

    # Last-resort route lookup. No packets need to leave the machine for connect().
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 53))
        local = sock.getsockname()[0]
        return [ipaddress.ip_network(f"{local}/24", strict=False)]
    except OSError:
        return []
    finally:
        sock.close()


async def _probe(host: str, port: int, timeout: float) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def scan_networks(
    networks: Iterable[ipaddress.IPv4Network],
    *,
    port: int = 6466,
    timeout: float = 0.25,
    concurrency: int = 96,
) -> list[DeviceCandidate]:
    """Probe local IPv4 networks for the Android TV Remote command port."""
    semaphore = asyncio.Semaphore(concurrency)
    found: list[DeviceCandidate] = []

    async def probe_one(address: ipaddress.IPv4Address) -> None:
        async with semaphore:
            host = str(address)
            if await _probe(host, port, timeout):
                found.append(DeviceCandidate(host=host, port=port, source="subnet-scan"))

    tasks = [asyncio.create_task(probe_one(address)) for network in networks for address in network.hosts()]
    if tasks:
        await asyncio.gather(*tasks)
    return sorted(set(found))


async def discover_all(
    *,
    mdns: bool = True,
    subnet_scan: bool = True,
    mdns_timeout: float = 3.0,
    probe_timeout: float = 0.25,
    configured_subnet: str = "",
) -> list[DeviceCandidate]:
    candidates: dict[str, DeviceCandidate] = {}
    if mdns:
        try:
            for item in await discover_mdns(mdns_timeout):
                candidates[item.host] = item
        except RuntimeError:
            pass

    if subnet_scan:
        networks: list[ipaddress.IPv4Network]
        if configured_subnet:
            try:
                parsed = ipaddress.ip_network(configured_subnet, strict=False)
                networks = [parsed] if isinstance(parsed, ipaddress.IPv4Network) else []
            except ValueError:
                networks = []
        else:
            networks = local_ipv4_networks()
        for item in await scan_networks(networks, timeout=probe_timeout):
            candidates.setdefault(item.host, item)

    return sorted(candidates.values())
