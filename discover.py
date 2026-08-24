import asyncio
import re
import socket
import time

from zeroconf import (
    IPVersion,
    ServiceBrowser,
    ServiceInfo,
    ServiceStateChange,
    Zeroconf,
)
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

# Note the trailing dot: zeroconf rejects a type that is not fully qualified.
SERVICE_TYPE = "_port22._tcp.local."

# How long we wait for a resolved service record before giving up on it.
RESOLVE_TIMEOUT_MS = 3000


def get_local_ip() -> str:
    """Return this machine's LAN IP (not 127.0.0.1).

    Opening a UDP socket toward a public address sends nothing; it just makes
    the OS pick the interface it would route through, which is the address our
    peers can actually reach us on.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # note: connect() takes ONE tuple argument
        return s.getsockname()[0]
    except OSError:
        # No default route (offline / captive adapter). Fall back to whatever
        # the hostname resolves to, and finally to loopback.
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


def _safe_label(text: str) -> str:
    """Trim a hostname down to something legal inside a DNS-SD label."""
    cleaned = re.sub(r"[^A-Za-z0-9-]+", "-", text).strip("-")
    return (cleaned or "port22")[:40]


def service_instance_name(port: int, hostname: str | None = None) -> str:
    """Fully qualified instance name we advertise under.

    Includes the port so two peers running on one machine (handy for testing)
    do not collide on the same name.
    """
    host = _safe_label(hostname or socket.gethostname())
    return f"{host}-{port}.{SERVICE_TYPE}"


def make_service_info(port: int, ip: str | None = None) -> ServiceInfo:
    """Build the record we publish for this device."""
    ip = ip or get_local_ip()
    hostname = socket.gethostname()
    return ServiceInfo(
        SERVICE_TYPE,
        service_instance_name(port, hostname),
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties={"app": "port22", "host": hostname},
        server=f"{_safe_label(hostname)}.local.",
    )


# --- Zeroconf lifecycle --- #


async def open_zeroconf() -> AsyncZeroconf:
    """Create the one AsyncZeroconf instance the app uses for its whole run."""
    return AsyncZeroconf(ip_version=IPVersion.V4Only)


async def close_zeroconf(azc: AsyncZeroconf | None) -> None:
    """Close it, tolerating a half-initialised or already-closed instance."""
    if azc is None:
        return
    try:
        await azc.async_close()
    except Exception:
        pass


# --- Advertising --- #


async def advertise(
    azc: AsyncZeroconf, port: int, ip: str | None = None
) -> ServiceInfo:
    """Register this device on the LAN. Returns the info needed to unregister."""
    info = make_service_info(port, ip)
    await azc.async_register_service(info)
    return info


async def unadvertise(azc: AsyncZeroconf | None, info: ServiceInfo | None) -> None:
    """Withdraw our advertisement so peers stop seeing a dead address."""
    if azc is None or info is None:
        return
    try:
        await azc.async_unregister_service(info)
    except Exception:
        pass


# --- Browsing --- #


async def _resolve(zc: Zeroconf, type_: str, name: str, out: dict) -> None:
    info = AsyncServiceInfo(type_, name)
    if not await info.async_request(zc, RESOLVE_TIMEOUT_MS):
        return
    addresses = info.parsed_addresses(IPVersion.V4Only)
    if addresses and info.port:
        out[name] = (name, addresses[0], info.port)


async def find_peers(
    azc: AsyncZeroconf,
    timeout: float = 3.0,
    exclude_names: set[str] | None = None,
    exclude_ips: set[str] | None = None,
) -> list[tuple[str, str, int]]:
    """Browse for `_port22._tcp.local.` peers for `timeout` seconds.

    Returns a list of (name, ip, port). Anything in `exclude_names` or
    `exclude_ips` is dropped -- that is how a device avoids "discovering"
    its own advertisement and dialling itself.
    """
    exclude_names = exclude_names or set()
    exclude_ips = exclude_ips or set()

    found: dict[str, tuple[str, str, int]] = {}
    pending: list[asyncio.Task] = []
    zc = azc.zeroconf

    def on_change(zeroconf, service_type, name, state_change):
        if state_change is not ServiceStateChange.Added:
            return
        if name in exclude_names or name in found:
            return
        pending.append(asyncio.ensure_future(_resolve(zc, service_type, name, found)))

    browser = AsyncServiceBrowser(zc, SERVICE_TYPE, handlers=[on_change])
    try:
        await asyncio.sleep(timeout)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        await browser.async_cancel()

    return [
        peer
        for name, peer in found.items()
        if name not in exclude_names and peer[1] not in exclude_ips
    ]


# --- Synchronous helper (standalone use, e.g. `uv run discover.py`) --- #


class PeerListener:
    """Blocking listener kept for scripts that are not inside an event loop."""

    def __init__(self):
        self.peers: list[tuple[str, str, int]] = []

    def add_service(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        if info:
            addresses = info.parsed_addresses(IPVersion.V4Only)
            if addresses and info.port:
                self.peers.append((name, addresses[0], info.port))

    def remove_service(self, zc, type_, name):
        pass

    def update_service(self, zc, type_, name):
        pass


def find_peers_sync(timeout: float = 3.0) -> list[tuple[str, str, int]]:
    """Blocking browse. Do NOT call this from inside the asyncio loop."""
    zc = Zeroconf(ip_version=IPVersion.V4Only)
    listener = PeerListener()
    browser = ServiceBrowser(zc, SERVICE_TYPE, listener)
    try:
        time.sleep(timeout)
    finally:
        browser.cancel()
        zc.close()
    return listener.peers


if __name__ == "__main__":
    print(f"Browsing for {SERVICE_TYPE} ...")
    for name, ip, port in find_peers_sync():
        print(f"  {name} -> {ip}:{port}")
