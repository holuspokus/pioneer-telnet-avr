"""Native HA controls supplementing the original accessory entities."""

import json
from pathlib import Path

from homeassistant.components.media_player import MediaPlayerDeviceClass, MediaPlayerEntity, MediaPlayerEntityFeature, MediaPlayerState

from .const import DEFAULT_MODE_IDS, DOMAIN
from .entity import PioneerEntity

MODE_NAMES = json.loads(Path(__file__).with_name("modes.json").read_text())


def mode_title(mode: str) -> str:
    return f"{MODE_NAMES.get(mode, 'Mode')} ({mode})"


def available_modes(receiver) -> list[str]:
    if receiver.options["onlyLearnedListeningModes"] and len(receiver.learned_modes) >= 10:
        modes = receiver.learned_modes
    else:
        modes = MODE_NAMES
    # SR can report modes that are absent from the fixed reference list.
    # Keep confirmed modes available even before the ten-mode threshold.
    return sorted((set(modes) | receiver.learned_modes | DEFAULT_MODE_IDS) - receiver.excluded_modes)


async def async_setup_entry(hass, entry, async_add_entities):
    receiver = hass.data[DOMAIN][entry.entry_id]
    entities = [PioneerMediaPlayer(entry, receiver)]
    async_add_entities(entities)
    if receiver.options["phaseControl"]:
        added_phase = False

        def add_phase():
            nonlocal added_phase
            if not added_phase and "IS" in receiver.extras:
                added_phase = True
                async_add_entities([PioneerPhaseTv(entry, receiver)])

        entry.async_on_unload(receiver.subscribe(add_phase))
        add_phase()
    if receiver.options["zoneControl"]:
        added_zones: set[int] = set()

        def add_detected_zones():
            new_zones = [zone for zone in (2, 3) if receiver.zone_power[zone] is not None and zone not in added_zones]
            if new_zones:
                added_zones.update(new_zones)
                async_add_entities([PioneerZonePlayer(entry, receiver, zone) for zone in new_zones])

        entry.async_on_unload(receiver.subscribe(add_detected_zones))
        add_detected_zones()


class PioneerPhaseTv(PioneerEntity, MediaPlayerEntity):
    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_supported_features = MediaPlayerEntityFeature.SELECT_SOURCE
    _choices = {"0": "Off", "1": "On", "2": "Full Band"}

    def __init__(self, entry, receiver):
        super().__init__(entry, receiver, "phase_tv", f"{entry.title} Phase Control")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.unique_id}_phase")},
            "name": f"{entry.title} Phase Control",
            "manufacturer": "Pioneer",
            "via_device_id": receiver.parent_device_id,
        }

    @property
    def state(self):
        return MediaPlayerState.ON if self.receiver.power else MediaPlayerState.OFF

    @property
    def source(self):
        return self._choices.get(self.receiver.extras.get("IS"))

    @property
    def source_list(self):
        return list(self._choices.values())

    async def async_select_source(self, source):
        code = next((key for key, name in self._choices.items() if name == source), None)
        if code is None:
            raise ValueError(source)
        self.receiver.set_extra(f"{code}IS", "IS")


class PioneerZonePlayer(PioneerEntity, MediaPlayerEntity):
    """Independent zone controls over the receiver's single Telnet session."""

    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF |
        MediaPlayerEntityFeature.VOLUME_SET | MediaPlayerEntityFeature.VOLUME_MUTE |
        MediaPlayerEntityFeature.SELECT_SOURCE
    )

    def __init__(self, entry, receiver, zone: int):
        self.zone = zone
        super().__init__(entry, receiver, f"zone_{zone}_player", f"{entry.title} Zone {zone}")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.unique_id}_zone_{zone}")},
            "name": f"{entry.title} Zone {zone}",
            "manufacturer": "Pioneer",
            "via_device_id": receiver.parent_device_id,
        }

    @property
    def state(self):
        return MediaPlayerState.ON if self.receiver.zone_power[self.zone] else MediaPlayerState.OFF

    @property
    def volume_level(self):
        raw = self.receiver.zone_volume[self.zone]
        return raw / 81 if raw is not None else None

    @property
    def is_volume_muted(self):
        return self.receiver.zone_muted[self.zone]

    @property
    def source(self):
        input_id = self.receiver.zone_input[self.zone]
        return self.receiver.input_name(input_id) if input_id else None

    @property
    def source_list(self):
        return [self.receiver.input_name(input_id) for input_id in self.receiver.inputs if input_id not in self.receiver.options["hiddenZoneInputs"]]

    async def async_turn_on(self):
        self.receiver.set_zone_power(self.zone, True)

    async def async_turn_off(self):
        self.receiver.set_zone_power(self.zone, False)

    async def async_set_volume_level(self, volume):
        self.receiver.set_zone_volume(self.zone, volume)

    async def async_mute_volume(self, mute):
        self.receiver.set_zone_mute(self.zone, mute)

    async def async_select_source(self, source):
        for input_id in self.receiver.inputs:
            if self.receiver.input_name(input_id) == source:
                self.receiver.set_zone_input(self.zone, input_id)
                return
        raise ValueError(f"Unknown Pioneer zone source: {source}")


class PioneerMediaPlayer(PioneerEntity, MediaPlayerEntity):
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    def __init__(self, entry, receiver):
        super().__init__(entry, receiver, "player", entry.title)
        self._attr_supported_features = (
            MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF |
            MediaPlayerEntityFeature.VOLUME_SET | MediaPlayerEntityFeature.VOLUME_MUTE |
            MediaPlayerEntityFeature.VOLUME_STEP | MediaPlayerEntityFeature.PLAY |
            MediaPlayerEntityFeature.PAUSE | MediaPlayerEntityFeature.SELECT_SOURCE |
            MediaPlayerEntityFeature.SELECT_SOUND_MODE
        )

    @property
    def state(self):
        return MediaPlayerState.ON if self.receiver.power else MediaPlayerState.OFF

    @property
    def volume_level(self):
        return self.receiver.volume / 100

    @property
    def is_volume_muted(self):
        return self.receiver.muted

    @property
    def source(self):
        return self.receiver.input_name(self.receiver.input_id) if self.receiver.input_id else None

    @property
    def source_list(self):
        return [self.receiver.input_name(input_id) for input_id in self.receiver.inputs]

    @property
    def sound_mode(self):
        return mode_title(self.receiver.listening_mode) if self.receiver.listening_mode else None

    @property
    def sound_mode_list(self):
        return [mode_title(mode) for mode in available_modes(self.receiver)]

    async def async_turn_on(self):
        self.receiver.set_power(True)

    async def async_turn_off(self):
        self.receiver.set_power(False)

    async def async_set_volume_level(self, volume):
        self.receiver.set_volume(round(volume * 100))

    async def async_mute_volume(self, mute):
        self.receiver.set_mute(mute)

    async def async_select_source(self, source):
        for input_id in self.receiver.inputs:
            if self.receiver.input_name(input_id) == source:
                self.receiver.set_input(input_id)
                return
        raise ValueError(f"Unknown Pioneer source: {source}")

    async def async_select_sound_mode(self, sound_mode):
        mode = next((mode for mode in available_modes(self.receiver) if mode_title(mode) == sound_mode), None)
        if mode is None:
            raise ValueError(f"Unknown Pioneer listening mode: {sound_mode}")
        self.receiver.set_listening_mode(mode)

    async def async_media_play(self):
        self.receiver.toggle_mode()

    async def async_media_pause(self):
        self.receiver.toggle_mode()

    async def async_media_play_pause(self):
        self.receiver.toggle_mode()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        def remote_key(event):
            if event.data.get("entity_id") != self.entity_id or not self.receiver.power:
                return
            key = event.data.get("key_name")
            self.receiver.remote_key(key)

        self.async_on_remove(self.hass.bus.async_listen("homekit_tv_remote_key_pressed", remote_key))

    async def async_volume_up(self):
        self.receiver.volume_up()

    async def async_volume_down(self):
        self.receiver.volume_down()
