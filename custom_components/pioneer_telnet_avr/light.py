"""Keep the original volume light as a distinct device control."""

from homeassistant.components.light import LightEntity, ColorMode
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .entity import PioneerEntity


async def async_setup_entry(hass, entry, async_add_entities):
    receiver = hass.data[DOMAIN][entry.entry_id]
    if receiver.options["volumeAsLight"] and receiver.options["additionalEntitiesInHa"]:
        async_add_entities([PioneerVolumeLight(entry, receiver)])
    else:
        # An earlier enabled light can remain in the entity registry after the
        # options reload. Remove it so a new HomeKit Bridge does not export an
        # unavailable volume light.
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "light", DOMAIN, f"{entry.unique_id}_volume_light"
        )
        if entity_id:
            registry.async_remove(entity_id)
    if receiver.options["zoneControl"]:
        added_zones: set[int] = set()

        def add_detected_zones():
            new_zones = [zone for zone in (2, 3) if receiver.zone_power[zone] is not None and zone not in added_zones]
            if new_zones:
                added_zones.update(new_zones)
                async_add_entities([PioneerZoneVolumeLight(entry, receiver, zone) for zone in new_zones])

        entry.async_on_unload(receiver.subscribe(add_detected_zones))
        add_detected_zones()


class PioneerZoneVolumeLight(PioneerEntity, LightEntity):
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(self, entry, receiver, zone: int):
        self.zone = zone
        super().__init__(entry, receiver, f"zone_{zone}_volume_light", f"{entry.title} Zone {zone} Volume")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.unique_id}_zone_{zone}")},
            "name": f"{entry.title} Zone {zone}",
            "manufacturer": "Pioneer",
            "via_device_id": receiver.parent_device_id,
        }

    @property
    def is_on(self):
        return self.receiver.zone_power[self.zone] is True and self.receiver.zone_muted[self.zone] is not True

    @property
    def brightness(self):
        raw = self.receiver.zone_volume[self.zone]
        if not self.is_on or raw is None:
            return 0
        minimum = self.receiver.min_volume / 100 * 81
        maximum = self.receiver.max_volume / 100 * 81
        return round(max(0, min(1, (raw - minimum) / max(1, maximum - minimum))) * 255)

    async def async_turn_on(self, **kwargs):
        if "brightness" in kwargs:
            minimum = self.receiver.min_volume / 100
            maximum = self.receiver.max_volume / 100
            level = minimum + kwargs["brightness"] / 255 * (maximum - minimum)
            self.receiver.set_zone_volume(self.zone, level)
        if self.receiver.zone_muted[self.zone]:
            self.receiver.set_zone_mute(self.zone, False)

    async def async_turn_off(self, **kwargs):
        self.receiver.set_zone_mute(self.zone, True)


class PioneerVolumeLight(PioneerEntity, LightEntity):
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(self, entry, receiver):
        name = f"Volume {entry.title}" if receiver.options["showReceiverNameInSwitches"] else "Volume"
        super().__init__(entry, receiver, "volume_light", name)

    @property
    def is_on(self):
        return self.receiver.power and not self.receiver.muted

    @property
    def brightness(self):
        return round(self.receiver.volume * 255 / 100) if self.is_on else 0

    async def async_turn_on(self, **kwargs):
        if "brightness" in kwargs:
            self.receiver.set_volume(round(kwargs["brightness"] * 100 / 255))
        if self.receiver.muted:
            self.receiver.set_mute(False)

    async def async_turn_off(self, **kwargs):
        self.receiver.set_mute(True)
