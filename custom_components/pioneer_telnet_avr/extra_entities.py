"""Optional controls documented in the Pioneer RS-232 command reference."""

from homeassistant.components.number import NumberEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity

from .const import DOMAIN
from .entity import PioneerEntity

CHANNELS = {
    "L__": "Front Left", "R__": "Front Right", "C__": "Center",
    "SL_": "Surround Left", "SR_": "Surround Right", "SBL": "Surround Back Left",
    "SBR": "Surround Back Right", "SW_": "Subwoofer", "LH_": "Front Height Left",
    "RH_": "Front Height Right", "LW_": "Front Wide Left", "RW_": "Front Wide Right",
}
AUDIO_FORMATS = {
    "00": "Analog", "01": "Analog", "02": "Analog", "03": "PCM", "04": "PCM",
    "05": "Dolby Digital", "06": "DTS", "07": "DTS-ES Matrix",
    "08": "DTS-ES Discrete", "09": "DTS 96/24", "10": "DTS 96/24 ES Matrix",
    "11": "DTS 96/24 ES Discrete", "12": "MPEG-2 AAC", "13": "WMA9 Pro",
    "14": "DSD → PCM", "15": "HDMI Through", "16": "Dolby Digital Plus",
    "17": "Dolby TrueHD", "18": "DTS Express", "19": "DTS-HD Master Audio",
    "20": "DTS-HD High Resolution", "21": "DTS-HD High Resolution",
    "22": "DTS-HD High Resolution", "23": "DTS-HD High Resolution",
    "24": "DTS-HD High Resolution", "25": "DTS-HD High Resolution",
    "26": "DTS-HD High Resolution", "27": "DTS-HD Master Audio",
}


class ExtraButton(PioneerEntity, ButtonEntity):
    def __init__(self, entry, receiver, key, label, command, reply_key):
        self.command, self.reply_key = command, reply_key
        super().__init__(entry, receiver, f"extra_button_{key}", f"{entry.title} {label}")

    async def async_press(self):
        self.receiver.set_extra(self.command, self.reply_key)


class ExtraSensor(PioneerEntity, SensorEntity):
    def __init__(self, entry, receiver, key, label):
        self.key = key
        super().__init__(entry, receiver, f"extra_{key}", f"{entry.title} {label}")

    @property
    def native_value(self):
        value = self.receiver.extras.get(self.key)
        if self.key == "AST" and value:
            return AUDIO_FORMATS.get(value[:2], f"Audio-Code {value[:2]}")
        return value

    @property
    def extra_state_attributes(self):
        value = self.receiver.extras.get(self.key)
        return {"raw_protocol_response": value} if self.key in ("AST", "VST") and value else None


class ExtraSwitch(PioneerEntity, SwitchEntity):
    _attr_has_entity_name = False

    def __init__(self, entry, receiver, key, label, on_command, off_command):
        self.key, self.on_command, self.off_command = key, on_command, off_command
        name = f"{label} {entry.title}" if receiver.options["showReceiverNameInSwitches"] else label
        super().__init__(entry, receiver, f"extra_{key}", name)

    @property
    def is_on(self):
        return self.receiver.extras.get(self.key) == "1"

    async def async_turn_on(self, **kwargs):
        if self.key == "TO":
            if self.receiver.extras.get("TO") == "0":
                self.receiver.set_extra("TO", "TO")
        else:
            self.receiver.set_extra(self.on_command, self.key)

    async def async_turn_off(self, **kwargs):
        if self.key == "TO":
            if self.receiver.extras.get("TO") == "1":
                self.receiver.set_extra("TO", "TO")
        else:
            self.receiver.set_extra(self.off_command, self.key)


class ExtraSelect(PioneerEntity, SelectEntity):
    def __init__(self, entry, receiver, key, label, choices, suffix):
        self.key, self.choices, self.suffix = key, choices, suffix
        super().__init__(entry, receiver, f"extra_{key}", f"{entry.title} {label}")

    @property
    def options(self):
        return list(self.choices.values())

    @property
    def current_option(self):
        return self.choices.get(self.receiver.extras.get(self.key))

    async def async_select_option(self, option):
        code = next((code for code, label in self.choices.items() if label == option), None)
        if code is None:
            raise ValueError(option)
        self.receiver.set_extra(f"{code}{self.suffix}", self.key)


class ExtraNumber(PioneerEntity, NumberEntity):
    def __init__(self, entry, receiver, key, label, kind):
        self.key, self.kind = key, kind
        super().__init__(entry, receiver, f"extra_{key}", f"{entry.title} {label}")
        if kind == "channel":
            self._attr_native_min_value, self._attr_native_max_value = -12, 12
            self._attr_native_step, self._attr_native_unit_of_measurement = 0.5, "dB"
        else:
            self._attr_native_min_value, self._attr_native_max_value = -6, 6
            self._attr_native_step, self._attr_native_unit_of_measurement = 1, "dB"

    @property
    def native_value(self):
        raw = self.receiver.extras.get(self.key)
        if raw is None:
            return None
        return (int(raw) - 50) / 2 if self.kind == "channel" else 6 - int(raw)

    async def async_set_native_value(self, value):
        if self.kind == "channel":
            raw = round(value * 2 + 50)
            self.receiver.set_extra(f"{self.key[3:]}{raw:02d}CLV", self.key)
        else:
            current = self.native_value
            if current is None:
                return
            command = ("BI" if value > current else "BD") if self.key == "BA" else ("TI" if value > current else "TD")
            for _ in range(round(abs(value - current))):
                self.receiver.set_extra(command, self.key)


def setup_optional_entities(hass, entry, async_add_entities, category, entity_type):
    receiver = hass.data[DOMAIN][entry.entry_id]
    if not receiver.options[category]:
        return
    added = set()

    def refresh():
        found = []
        def add(key, entity):
            if key in receiver.extras and key not in added:
                added.add(key)
                found.append(entity)
        if category == "audioInfo":
            add("AST", ExtraSensor(entry, receiver, "AST", "Audio-Information (AST)"))
        elif category == "videoInfo":
            add("VST", ExtraSensor(entry, receiver, "VST", "Video-Information (VST)"))
        elif category == "toneControls":
            add("TO", ExtraSwitch(entry, receiver, "TO", "Tone", "1TO", "0TO"))
            for key, label in (("BA", "Bass"), ("TR", "Treble")):
                add(key, ExtraNumber(entry, receiver, key, label, "tone"))
        elif category == "phaseControl":
            add("IS", ExtraSelect(entry, receiver, "IS", "Phase Control", {"0": "Off", "1": "On", "2": "Full Band"}, "IS"))
        elif category == "virtualSurroundBack":
            add("VSB", ExtraSwitch(entry, receiver, "VSB", "Virtual Surround Back", "1VSB", "0VSB"))
        elif category == "channelLevels":
            for channel, label in CHANNELS.items():
                key = f"CLV{channel}"
                add(key, ExtraNumber(entry, receiver, key, f"Channel {label}", "channel"))
        elif category == "tunerControl":
            add("FR", ExtraSensor(entry, receiver, "FR", "Tuner-Frequenz"))
            add("PR", ExtraSensor(entry, receiver, "PR", "Tuner-Preset"))
            for key, label, command, prerequisite in (
                ("freq_up", "Frequenz höher", "TFI", "FR"),
                ("freq_down", "Frequenz tiefer", "TFD", "FR"),
                ("band", "Tuner-Band wechseln", "TB", "FR"),
                ("preset_up", "Preset weiter", "TPI", "PR"),
                ("preset_down", "Preset zurück", "TPD", "PR"),
                ("class", "Preset-Klasse wechseln", "TC", "PR"),
            ):
                if prerequisite in receiver.extras and key not in added:
                    added.add(key)
                    found.append(ExtraButton(entry, receiver, key, label, command, None if key == "band" else prerequisite))
        chosen = [entity for entity in found if isinstance(entity, entity_type)]
        if chosen:
            async_add_entities(chosen)

    entry.async_on_unload(receiver.subscribe(refresh))
    refresh()
