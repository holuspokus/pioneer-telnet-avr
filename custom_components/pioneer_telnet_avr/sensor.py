"""Optional sensor entities."""

from homeassistant.components.sensor import SensorEntity, SensorStateClass

from .const import DOMAIN
from .entity import PioneerEntity
from .extra_entities import setup_optional_entities
from .media_player import mode_title


async def async_setup_entry(hass, entry, async_add_entities):
    receiver = hass.data[DOMAIN][entry.entry_id]
    # These two values are already received on the regular Telnet session.
    # Register them immediately so users can find them even before the first
    # FL/FN message arrives or while the receiver is switched off.
    async_add_entities([
        PioneerDisplaySensor(entry, receiver),
        PioneerInputSensor(entry, receiver),
        PioneerListeningModeSensor(entry, receiver),
        PioneerVolumeSensor(entry, receiver),
    ])
    if receiver.options["zoneControl"]:
        added_zones: set[int] = set()

        def add_detected_zones():
            new_zones = [zone for zone in (2, 3) if receiver.zone_power[zone] is not None and zone not in added_zones]
            if new_zones:
                added_zones.update(new_zones)
                async_add_entities([PioneerZoneVolumeSensor(entry, receiver, zone) for zone in new_zones])

        entry.async_on_unload(receiver.subscribe(add_detected_zones))
        add_detected_zones()
    for key, category, label in (
        ("AST", "audioInfo", "Audio-Information (AST)"),
        ("VST", "videoInfo", "Video-Information (VST)"),
    ):
        if receiver.options[category]:
            from .extra_entities import ExtraSensor
            async_add_entities([ExtraSensor(entry, receiver, key, label)])
    setup_optional_entities(hass, entry, async_add_entities, "tunerControl", SensorEntity)


class PioneerDisplaySensor(PioneerEntity, SensorEntity):
    def __init__(self, entry, receiver):
        super().__init__(entry, receiver, "display", f"{entry.title} Display")

    @property
    def native_value(self):
        if not self.receiver.power:
            return ""
        return self.receiver.display_text or ""


class PioneerInputSensor(PioneerEntity, SensorEntity):
    """Current receiver input as reported by FN on the Telnet session."""

    def __init__(self, entry, receiver):
        super().__init__(entry, receiver, "active_input", f"{entry.title} Active input")

    @property
    def native_value(self):
        if not self.receiver.power:
            return "Off"
        if self.receiver.input_id is None:
            return None
        if (not self.receiver.known_input_name(self.receiver.input_id)
                and self.receiver.input_name_pending(self.receiver.input_id)):
            return None
        return self.receiver.input_name(self.receiver.input_id)

    @property
    def extra_state_attributes(self):
        return {"input_id": self.receiver.input_id} if self.receiver.input_id else None


class PioneerListeningModeSensor(PioneerEntity, SensorEntity):
    def __init__(self, entry, receiver):
        super().__init__(entry, receiver, "listening_mode_sensor", f"{entry.title} Listening Mode")

    @property
    def native_value(self):
        if not self.receiver.power or not self.receiver.listening_mode:
            return None
        return mode_title(self.receiver.listening_mode)


class PioneerVolumeSensor(PioneerEntity, SensorEntity):
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry, receiver):
        super().__init__(entry, receiver, "volume_sensor", f"{entry.title} Volume")

    @property
    def native_value(self):
        if not self.receiver.power or not self.receiver._volume_seen:
            return None
        return self.receiver.volume


class PioneerZoneVolumeSensor(PioneerEntity, SensorEntity):
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry, receiver, zone: int):
        self.zone = zone
        super().__init__(entry, receiver, f"zone_{zone}_volume_sensor", f"{entry.title} Zone {zone} Volume")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.unique_id}_zone_{zone}")},
            "name": f"{entry.title} Zone {zone}",
            "manufacturer": "Pioneer",
            "via_device_id": receiver.parent_device_id,
        }

    @property
    def native_value(self):
        if self.receiver.zone_power[self.zone] is not True:
            return None
        raw = self.receiver.zone_volume[self.zone]
        return round(raw * 100 / 81) if raw is not None else None
