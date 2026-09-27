"""Classic Pioneer AVR integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import logging
import re
import time

from .const import DEFAULT_OPTIONS, DOMAIN, PLATFORMS
from .discovery import discover
from .receiver import PioneerReceiver

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    options = {**DEFAULT_OPTIONS, **entry.options}
    if "listelingmodesAsTV" not in entry.options:
        options["listelingmodesAsTV"] = entry.options.get("homekitListeningModesTv", False)
    store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}")
    saved = await store.async_load() or {}
    if not saved.get("mode_visibility_migrated"):
        old_hidden = saved.get("homekit_hidden_modes", options["hiddenListeningModes"])
        saved["excluded_modes"] = sorted(set(saved.get("excluded_modes", [])) | set(old_hidden))
        saved.pop("homekit_hidden_modes", None)
        saved["mode_visibility_migrated"] = True
        await store.async_save(saved)
    saved_endpoint = saved.get("endpoint") or {}
    if "hidden_inputs" in saved:
        options["hiddenInputs"] = saved["hidden_inputs"]

    def use_discovered_endpoint(device):
        receiver.transport.host, receiver.transport.port = device.host, device.port
        receiver.persist()
        return device.host, device.port

    async def rediscover():
        # RAOP instance names commonly start with a stable twelve-digit MAC.
        old_service = entry.data.get("fqdn", "")
        old_identity = old_service.split("@", 1)[0]
        if not re.fullmatch(r"[0-9a-fA-F]{12}", old_identity):
            old_identity = None
        devices = await discover(hass, target=old_identity, timeout=5.0) if old_identity else []
        if not devices:
            # Collect all candidates before considering a renamed receiver.
            devices = await discover(hass, target="", timeout=5.0, collect_all=True)
        if old_identity:
            for device in devices:
                if device.fqdn.split("@", 1)[0].casefold() == old_identity.casefold():
                    return use_discovered_endpoint(device)
        for device in devices:
            if device.fqdn == old_service or device.host == entry.data["host"]:
                return use_discovered_endpoint(device)
        # If there is only one plausible AVR, follow the Homebridge fallback
        # when Bonjour presents it under a completely new name and address.
        receivers = [device for device in devices if device.name.upper().startswith(("VSX", "SC-"))]
        if len(receivers) == 1:
            return use_discovered_endpoint(receivers[0])
        return None

    receiver = PioneerReceiver(
        saved_endpoint.get("host", entry.data["host"]),
        saved_endpoint.get("port", entry.data["port"]), options, rediscover,
        lambda: store.async_delay_save(
            lambda: {"inputs": receiver.inputs, "inputs_complete": receiver._discovery_complete, "learned_modes": sorted(receiver.learned_modes), "excluded_modes": sorted(receiver.excluded_modes), "mode_visibility_migrated": True, "homekit_hidden_mcacc": sorted(receiver.homekit_hidden_mcacc), "mode_learning_version": 3, "inputs_timestamp": receiver.inputs_timestamp, "hidden_inputs": receiver.options["hiddenInputs"], "endpoint": {"host": receiver.transport.host, "port": receiver.transport.port}}, 15
        ),
    )
    receiver.inputs.update(saved.get("inputs", {}))
    # Older builds incorrectly mixed LM display codes with SR set-mode IDs.
    # Their provenance cannot be recovered, so start clean once on upgrade.
    if saved.get("mode_learning_version", 0) >= 2:
        receiver.learned_modes.update(saved.get("learned_modes", []))
    receiver.excluded_modes.update(saved.get("excluded_modes", []))
    receiver.homekit_hidden_mcacc.update(saved.get("homekit_hidden_mcacc", options["hiddenMcaccMemories"]))
    receiver.inputs_timestamp = saved.get("inputs_timestamp", 0)
    receiver.parent_device_id = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.unique_id)},
        name=entry.title,
        manufacturer="Pioneer",
        model=entry.title,
    ).id
    if saved.get("inputs_complete") and receiver.inputs and time.time() - receiver.inputs_timestamp < 1800:
        receiver._last_discovery = time.monotonic()
        receiver._discovery_complete = True
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = receiver
    # Older releases registered these TVs as HA media players. Remove their
    # registry records so neither can be picked up by the HA HomeKit Bridge.
    registry = er.async_get(hass)
    for suffix in ("listening_modes_tv", "mcacc_tv"):
        entity_id = registry.async_get_entity_id("media_player", DOMAIN, f"{entry.unique_id}_{suffix}")
        if entity_id:
            registry.async_remove(entity_id)
    device_registry = dr.async_get(hass)
    for suffix in ("listening_modes", "mcacc"):
        device = device_registry.async_get_device_by_identifier(
            (DOMAIN, f"{entry.unique_id}_{suffix}"), entry.entry_id
        )
        if device:
            device_registry.async_remove_device(device.id)
    # Old releases advertised the three TVs separately. Their stored keys
    # remain untouched, but their obsolete pairing prompts must disappear.
    from homeassistant.components.persistent_notification import async_dismiss
    for suffix in ("", "_listening_modes", "_mcacc"):
        async_dismiss(hass, f"pioneer_homekit_{entry.entry_id}{suffix}")
    receiver.start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    from .homekit_accessory import PioneerSharedHomeKit
    homekit = hass.data.setdefault(f"{DOMAIN}_shared_homekit", PioneerSharedHomeKit(hass))
    try:
        await homekit.add_receiver(entry, receiver)
    except Exception:
        _LOGGER.exception("Could not add %s to the Pioneer HomeKit bridge", entry.title)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    homekit = hass.data.get(f"{DOMAIN}_shared_homekit")
    if homekit:
        await homekit.remove_receiver(entry.entry_id)
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    receiver = hass.data[DOMAIN].pop(entry.entry_id)
    await receiver.stop()
    return True
