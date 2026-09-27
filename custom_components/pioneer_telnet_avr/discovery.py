"""Discover classic Pioneer VSX receivers via RAOP and probe Telnet ports."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging

from homeassistant.core import HomeAssistant
from homeassistant.components import zeroconf as ha_zeroconf
from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

_LOGGER = logging.getLogger(__name__)
PORTS = (23, 24, 8102)
SERVICE_TYPE = "_raop._tcp.local."


@dataclass(frozen=True)
class Receiver:
    name: str
    orig_name: str
    host: str
    port: int
    fqdn: str


async def _port_open(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 10)
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def discover(hass: HomeAssistant, *, target: str = "VSX", ports: tuple[int, ...] = PORTS, timeout: float = 30.5, collect_all: bool = False) -> list[Receiver]:
    """Discover RAOP advertisements and select the first open Telnet port."""
    aiozc = await ha_zeroconf.async_get_async_instance(hass)
    names: set[str] = set()
    pending: set[asyncio.Task] = set()
    found: dict[tuple[str, int], Receiver] = {}
    found_event = asyncio.Event()

    async def probe(service_name: str) -> None:
        info = AsyncServiceInfo(SERVICE_TYPE, service_name)
        if not await info.async_request(aiozc.zeroconf, 3000):
            return
        # The reference searches the entire Bonjour advertisement, including TXT.
        advertisement = json.dumps({
            "name": service_name,
            "host": info.server,
            "txt": {
                str(key.decode(errors="replace") if isinstance(key, bytes) else key):
                str(value.decode(errors="replace") if isinstance(value, bytes) else value)
                for key, value in info.properties.items()
            },
        })
        if target.lower() not in advertisement.lower():
            return
        host = info.server.removesuffix(".")
        if not host:
            addresses = info.parsed_addresses()
            if not addresses:
                return
            host = addresses[0]
        name = service_name.split(".", 1)[0].split("@")[-1]
        for port in ports:
            if await _port_open(host, port):
                found[(host.lower(), port)] = Receiver(name, service_name, host, port, service_name)
                found_event.set()
                break

    def listener(zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
        if state_change is ServiceStateChange.Removed or name in names:
            return
        names.add(name)
        task = hass.async_create_task(probe(name))
        pending.add(task)
        task.add_done_callback(pending.discard)

    browser = AsyncServiceBrowser(aiozc.zeroconf, SERVICE_TYPE, handlers=[listener])
    try:
        # Homebridge checks at 0.5 s, then 1 s later, then waits 29 s.
        # Preserve the longer discovery window when no AVR appears promptly.
        for interval in (0.5, 1.0, max(0, timeout - 1.5)):
            try:
                await asyncio.wait_for(found_event.wait(), interval)
            except asyncio.TimeoutError:
                pass
            if found:
                if collect_all:
                    # Allow further Bonjour announcements to reach the receiver
                    # picker without waiting out the full discovery timeout.
                    await asyncio.sleep(2.0)
                break
        if pending:
            await asyncio.wait(pending, timeout=10)
    finally:
        await browser.async_cancel()
    return list(found.values())
