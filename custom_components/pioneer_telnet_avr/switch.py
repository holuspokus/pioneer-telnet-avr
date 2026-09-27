"""Listening mode, Telnet session, and individually selected inputs."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .entity import PioneerEntity
from .extra_entities import setup_optional_entities


def switch_name(entry, receiver, label: str) -> str:
    """Keep switch names consistent within one receiver."""
    if receiver.options["showReceiverNameInSwitches"]:
        return f"{label} {entry.title}"
    return label


async def async_setup_entry(hass, entry, async_add_entities):
    receiver = hass.data[DOMAIN][entry.entry_id]
    entities = []
    if receiver.options["toggleListeningMode"] and receiver.options["additionalEntitiesInHa"]:
        entities.append(PioneerListeningSwitch(entry, receiver))
    if receiver.options["telnetSwitch"] and receiver.options["telnetSwitchInHa"]:
        entities.append(PioneerTelnetSwitch(entry, receiver))
    if receiver.options["additionalEntitiesInHa"]:
        entities.extend(PioneerInputSwitch(entry, receiver, input_id) for input_id in receiver.options["inputSwitches"])
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain != "switch" or not entity.unique_id:
            continue
        remove_extra = not receiver.options["additionalEntitiesInHa"] and (
            entity.unique_id == f"{entry.unique_id}_listening_mode"
            or entity.unique_id.startswith(f"{entry.unique_id}_input_")
        )
        remove_telnet = (not receiver.options["telnetSwitch"] or not receiver.options["telnetSwitchInHa"]) and (
            entity.unique_id == f"{entry.unique_id}_telnet"
        )
        if remove_extra or remove_telnet:
            registry.async_remove(entity.entity_id)
    async_add_entities(entities)
    for category in ("toneControls", "virtualSurroundBack"):
        setup_optional_entities(hass, entry, async_add_entities, category, SwitchEntity)

    # Zone support differs by model. Add a switch only after its status query
    # succeeds, without opening another socket or restarting Home Assistant.
    added_zones: set[int] = set()

    def add_detected_zones():
        new_zones = [zone for zone in (2, 3) if receiver.zone_power[zone] is not None and zone not in added_zones]
        if new_zones:
            added_zones.update(new_zones)
            async_add_entities([PioneerZoneSwitch(entry, receiver, zone) for zone in new_zones])

    if receiver.options["zoneControl"]:
        entry.async_on_unload(receiver.subscribe(add_detected_zones))
        add_detected_zones()


class PioneerZoneSwitch(PioneerEntity, SwitchEntity):
    _attr_has_entity_name = False

    def __init__(self, entry, receiver, zone: int):
        self.zone = zone
        super().__init__(entry, receiver, f"zone_{zone}", switch_name(entry, receiver, f"Zone {zone}"))
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.unique_id}_zone_{zone}")},
            "name": f"{entry.title} Zone {zone}",
            "manufacturer": "Pioneer",
            "via_device_id": receiver.parent_device_id,
        }

    @property
    def is_on(self):
        return self.receiver.zone_power[self.zone] is True

    async def async_turn_on(self, **kwargs):
        self.receiver.set_zone_power(self.zone, True)

    async def async_turn_off(self, **kwargs):
        self.receiver.set_zone_power(self.zone, False)


class PioneerInputSwitch(PioneerEntity, SwitchEntity):
    _attr_has_entity_name = False

    def __init__(self, entry, receiver, input_id):
        self.input_id = input_id
        super().__init__(entry, receiver, f"input_{input_id}", switch_name(entry, receiver, receiver.input_switch_label(input_id)))

    @property
    def name(self):
        return switch_name(self.entry, self.receiver, self.receiver.input_switch_label(self.input_id))

    @property
    def is_on(self):
        return self.receiver.power and self.receiver.input_id == self.input_id

    async def async_turn_on(self, **kwargs):
        await self.receiver.press_input(self.input_id)

    async def async_turn_off(self, **kwargs):
        if self.is_on:
            self.receiver.set_power(False)


class PioneerListeningSwitch(PioneerEntity, SwitchEntity):
    _attr_has_entity_name = False

    def __init__(self, entry, receiver):
        super().__init__(entry, receiver, "listening_mode", switch_name(entry, receiver, "Listening Mode"))
        if not receiver.options["toggleListeningModeLink"]:
            self._attr_device_info = {
                "identifiers": {(DOMAIN, f"{entry.unique_id}_audio")},
                "name": f"{entry.title} Listening Mode",
                "manufacturer": "Pioneer",
                "via_device_id": receiver.parent_device_id,
            }

    @property
    def is_on(self):
        if not self.receiver.power:
            return False
        if not self.receiver.listening_mode:
            # Same initial state as Homebridge while the SR reply is pending.
            return True
        return self.receiver.listening_mode in (
            self.receiver.options["listeningMode"], self.receiver.options["listeningModeFallback"]
        )

    @property
    def extra_state_attributes(self):
        return {
            "received_mode_id": self.receiver.listening_mode,
            "on_mode_id": self.receiver.options["listeningMode"],
            "fallback_mode_id": self.receiver.options["listeningModeFallback"],
            "off_mode_id": self.receiver.options["listeningModeOther"],
        }

    async def async_turn_on(self, **kwargs):
        self.receiver.toggle_mode()

    async def async_turn_off(self, **kwargs):
        self.receiver.toggle_mode()


class PioneerTelnetSwitch(PioneerEntity, SwitchEntity):
    _attr_has_entity_name = False

    def __init__(self, entry, receiver):
        super().__init__(entry, receiver, "telnet", switch_name(entry, receiver, "Telnet connection"))

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        return self.receiver.telnet_switch_on

    async def async_turn_on(self, **kwargs):
        self.receiver.turn_telnet_on()

    async def async_turn_off(self, **kwargs):
        self.receiver.turn_telnet_off()
