"""Shared Pioneer HomeKit bridge backed by the existing receiver connections."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import hashlib
from pathlib import Path

from pyhap.accessory import Accessory, Bridge
from pyhap.accessory_driver import AccessoryDriver
from pyhap.const import CATEGORY_TELEVISION, CATEGORY_LIGHTBULB, CATEGORY_SWITCH
from homeassistant.components import zeroconf

from .media_player import MODE_NAMES, mode_title
from .const import DEFAULT_MODE_IDS


def homekit_modes(receiver):
    """Keep even excluded modes configurable in Apple Home."""
    return sorted(set(MODE_NAMES) | receiver.learned_modes | DEFAULT_MODE_IDS)

_LOGGER = logging.getLogger(__name__)


class HassAccessoryDriver(AccessoryDriver):
    """Do not stop Home Assistant's event loop when unloading this accessory."""

    async def async_stop(self):
        self.stop_event.set()
        if self.advertiser and self.mdns_service_info:
            await self.advertiser.async_unregister_service(self.mdns_service_info)
            # The advertiser belongs to Home Assistant and is shared with its
            # other integrations. Closing it would break their discovery too.
        self.aio_stop_event.set()
        self.http_server.async_stop()
        await self.async_add_job(self.accessory.stop)


class PioneerHomeKitTV(Accessory):
    category = CATEGORY_TELEVISION

    def __init__(self, driver, name, receiver):
        super().__init__(driver, name)
        self.receiver = receiver
        self.set_info_service(manufacturer="Pioneer", model=name, serial_number=f"pioneer-{receiver.host}")

        tv = self.add_preload_service("Television", chars=[
            "Active", "ActiveIdentifier", "ConfiguredName", "SleepDiscoveryMode", "RemoteKey",
        ])
        tv.is_primary_service = True
        tv.configure_char("ConfiguredName", value=name)
        self.sleep_discovery = tv.configure_char("SleepDiscoveryMode", value=int(not receiver.ready or not receiver.power))
        self.active = tv.configure_char(
            "Active", value=0, getter_callback=self.get_tv_active,
            setter_callback=lambda value: self._command(receiver.set_power, bool(value)),
        )
        self.active_identifier = tv.configure_char(
            "ActiveIdentifier", value=0,
            getter_callback=self.get_tv_input, setter_callback=self.select_input,
        )
        tv.configure_char("RemoteKey", setter_callback=self.remote_key)
        self.tv = tv
        self.inputs: dict[int, object] = {}

        speaker = self.add_preload_service("TelevisionSpeaker", chars=[
            "Name", "Active", "VolumeControlType", "VolumeSelector", "Volume", "Mute",
        ])
        speaker.configure_char("Name", value=f"{name} Volume")
        self.speaker_active = speaker.configure_char(
            "Active", value=1, getter_callback=lambda: int(receiver.ready and receiver.power and not receiver.muted),
        )
        speaker.configure_char("VolumeControlType", value=1)
        speaker.configure_char("VolumeSelector", setter_callback=self.volume_step)
        self.speaker_volume = speaker.configure_char(
            "Volume", value=0, getter_callback=lambda: receiver.volume if receiver.ready else 0,
        )
        self.speaker_mute = speaker.configure_char(
            "Mute", value=False, getter_callback=lambda: bool(not receiver.ready or not receiver.power or receiver.muted),
            setter_callback=lambda value: self._command(receiver.set_mute, bool(value)),
        )
        tv.add_linked_service(speaker)

        self.listening_switch = None
        if (receiver.options["toggleListeningMode"] and receiver.options["toggleListeningModeLink"]
                and not receiver.options["additionalEntitiesInHa"]):
            listening = self.add_preload_service(
                "Switch", chars=["Name", "ConfiguredName", "On"],
                unique_id=f"{name}listeningMode",
            )
            # Match the Homebridge service's names and subtype. Apple Home
            # does not arrange linked services by their creation order alone.
            listening.configure_char("Name", value="listeningMode")
            listening.configure_char("ConfiguredName", value="listeningMode")
            self.listening_switch = listening.configure_char(
                "On", value=False, getter_callback=self.listening_switch_on,
                setter_callback=lambda value: self._command(receiver.toggle_mode),
            )
            tv.add_linked_service(listening)

        light = self.add_preload_service("Lightbulb", chars=["Name", "ConfiguredName", "On", "Brightness"])
        light.configure_char("Name", value="Volume")
        light.configure_char("ConfiguredName", value="Volume")
        self.light_on = light.configure_char(
            "On", value=False, getter_callback=lambda: bool(receiver.ready and receiver.power and not receiver.muted),
            setter_callback=self.set_light_on,
        )
        self.light_brightness = light.configure_char(
            "Brightness", value=0,
            getter_callback=lambda: receiver.volume if receiver.ready else 0,
            setter_callback=lambda value: self._command(receiver.set_volume, int(value)),
        )
        tv.add_linked_service(light)

        self.refresh()

    def _command(self, func, *args):
        # pyhap handles characteristic writes on worker threads. The Pioneer
        # transport and its asyncio tasks belong to Home Assistant's loop.
        self.driver.loop.call_soon_threadsafe(func, *args)

    def listening_switch_on(self):
        receiver = self.receiver
        return bool(
            receiver.ready and receiver.power and (
                not receiver.listening_mode
                or receiver.listening_mode in (
                    receiver.options["listeningMode"],
                    receiver.options["listeningModeFallback"],
                )
            )
        )

    def get_tv_active(self):
        value = int(self.receiver.ready and self.receiver.power)
        _LOGGER.debug("HomeKit TV Active read: power=%s connected=%s", value, self.receiver.available)
        return value

    def get_tv_input(self):
        value = int(self.receiver.input_id or 0) if self.receiver.ready else 0
        _LOGGER.debug("HomeKit TV ActiveIdentifier read: input=%s connected=%s", value, self.receiver.available)
        return value

    def add_inputs(self):
        """Use numeric Pioneer input IDs for stable HomeKit identifiers."""
        changed = False
        for input_id, name in sorted(self.receiver.inputs.items()):
            number = int(input_id)
            label = self.receiver.input_name(input_id)
            if number in self.inputs:
                self.inputs[number].configure_char("ConfiguredName", value=label)
                self.inputs[number].configure_char(
                    "CurrentVisibilityState", value=int(input_id in self.receiver.options["hiddenInputs"])
                )
                continue
            service = self.add_preload_service("InputSource", chars=[
                "Identifier", "ConfiguredName", "IsConfigured", "CurrentVisibilityState",
                "TargetVisibilityState", "InputSourceType",
            ], unique_id=f"source_{input_id}")
            service.configure_char("Identifier", value=number)
            service.configure_char(
                "ConfiguredName", value=label,
                setter_callback=lambda value, source=input_id: self._command(
                    self.receiver.rename_input, source, str(value)
                ),
            )
            service.configure_char("IsConfigured", value=1)
            visibility = int(input_id in self.receiver.options["hiddenInputs"])
            service.configure_char("CurrentVisibilityState", value=visibility)
            service.configure_char(
                "TargetVisibilityState", value=visibility,
                setter_callback=lambda value, source=input_id: self._command(
                    self.set_input_visibility, source, value
                ),
            )
            service.configure_char("InputSourceType", value=0)
            self.tv.add_linked_service(service)
            self.inputs[number] = service
            changed = True
        return changed

    def set_input_visibility(self, input_id, value):
        hidden = set(self.receiver.options["hiddenInputs"])
        if int(value):
            hidden.add(input_id)
        else:
            hidden.discard(input_id)
        self.receiver.options["hiddenInputs"] = sorted(hidden)
        self.inputs[int(input_id)].configure_char("CurrentVisibilityState", value=int(bool(value)))
        self.receiver.persist()

    def refresh(self):
        receiver = self.receiver
        # Homebridge only published characteristics when their values changed.
        # Re-sending ActiveIdentifier during each volume event makes the Home
        # app refresh the selected TV channel while the brightness is dragged.
        for char, value in (
            (self.active, int(receiver.ready and receiver.power)),
            (self.sleep_discovery, int(not receiver.ready or not receiver.power)),
            (self.speaker_active, int(receiver.ready and receiver.power and not receiver.muted)),
            (self.active_identifier, int(receiver.input_id or 0) if receiver.ready else 0),
            (self.speaker_volume, receiver.volume if receiver.ready else 0),
            (self.speaker_mute, bool(not receiver.ready or not receiver.power or receiver.muted)),
            (self.light_on, bool(receiver.ready and receiver.power and not receiver.muted)),
            (self.light_brightness, receiver.volume if receiver.ready and receiver.power and not receiver.muted else 0),
        ):
            if char.value != value:
                char.set_value(value)
        if self.listening_switch is not None:
            value = self.listening_switch_on()
            if self.listening_switch.value != value:
                self.listening_switch.set_value(value)

    def select_input(self, number):
        input_id = f"{int(number):02d}"
        if int(number) in self.inputs:
            self._command(self._select_input, input_id)

    def _select_input(self, input_id):
        self.receiver.set_input(input_id)
        if self.receiver.available and self.receiver.power:
            # FN normally arrives on the Telnet stream. Verify the result if
            # it does not, instead of leaving the Home app on a stale input.
            self.driver.loop.call_later(2, self._verify_input, input_id)

    def _verify_input(self, input_id):
        if self.receiver.available and self.receiver.power and self.receiver.input_id != input_id:
            self.receiver.transport.send("?F", "FN")

    def volume_step(self, direction):
        self._command(self.receiver.volume_down if direction else self.receiver.volume_up)

    def set_light_on(self, value):
        self._command(self.receiver.set_mute, not bool(value))

    def remote_key(self, key):
        name = {
            4: "arrow_up", 5: "arrow_down", 6: "arrow_left", 7: "arrow_right",
            8: "select", 9: "back", 11: "play_pause", 15: "information",
        }.get(key)
        if name:
            self._command(self.receiver.remote_key, name)


class PioneerListeningModesHomeKitTV(Accessory):
    """Standalone TV whose input channels choose receiver listening modes."""

    category = CATEGORY_TELEVISION

    def __init__(self, driver, name, receiver):
        super().__init__(driver, f"{name} Listening Modes")
        self.receiver = receiver
        self.set_info_service(
            manufacturer="Pioneer", model=name,
            serial_number=f"pioneer-listening-{receiver.host}",
        )
        tv = self.add_preload_service("Television", chars=[
            "Active", "ActiveIdentifier", "ConfiguredName", "SleepDiscoveryMode", "RemoteKey",
        ])
        tv.is_primary_service = True
        tv.configure_char("ConfiguredName", value=f"{name} Listening Modes")
        self.active = tv.configure_char(
            "Active", value=0, getter_callback=lambda: int(receiver.ready and receiver.power),
            setter_callback=lambda value: self._command(self.restore_active),
        )
        self.active_identifier = tv.configure_char(
            "ActiveIdentifier", value=0,
            getter_callback=lambda: int(receiver.listening_mode or 0) if receiver.ready else 0,
            setter_callback=self.select_mode,
        )
        self.sleep_discovery = tv.configure_char("SleepDiscoveryMode", value=int(not receiver.ready))
        tv.configure_char("RemoteKey", setter_callback=self.remote_key)
        self.tv = tv
        self.inputs = {}
        self._configured_inputs = set()
        self.refresh()

    def _command(self, func, *args):
        self.driver.loop.call_soon_threadsafe(func, *args)

    def restore_active(self):
        # The TV power control is required by HomeKit, but this TV only
        # selects modes. Keep its state tied to the actual receiver power.
        self.active.set_value(int(self.receiver.power))

    def add_inputs(self):
        changed = False
        available = set(homekit_modes(self.receiver))
        hidden = self.receiver.excluded_modes
        for mode in sorted(available):
            number = int(mode)
            label = mode_title(mode)
            visibility = int(mode in hidden)
            if number in self.inputs:
                service = self.inputs[number]
                if number not in self._configured_inputs:
                    service.configure_char("IsConfigured", value=1)
                    self._configured_inputs.add(number)
                    changed = True
                service.configure_char("CurrentVisibilityState", value=visibility)
                service.configure_char(
                    "TargetVisibilityState", value=visibility,
                    setter_callback=lambda value, selected=mode: self._command(
                        self.set_mode_visibility, selected, value
                    ),
                )
                continue
            service = self.add_preload_service("InputSource", chars=[
                "Identifier", "ConfiguredName", "IsConfigured", "CurrentVisibilityState",
                "TargetVisibilityState", "InputSourceType",
            ], unique_id=f"mode_{mode}")
            service.configure_char("Identifier", value=number)
            service.configure_char("ConfiguredName", value=label)
            service.configure_char("IsConfigured", value=1)
            service.configure_char("CurrentVisibilityState", value=visibility)
            service.configure_char(
                "TargetVisibilityState", value=visibility,
                setter_callback=lambda value, selected=mode: self._command(
                    self.set_mode_visibility, selected, value
                ),
            )
            service.configure_char("InputSourceType", value=0)
            self.tv.add_linked_service(service)
            self.inputs[number] = service
            self._configured_inputs.add(number)
            changed = True
        for number, service in self.inputs.items():
            if f"{number:04d}" not in available:
                if number in self._configured_inputs:
                    service.configure_char("IsConfigured", value=0)
                    self._configured_inputs.remove(number)
                    changed = True
                service.configure_char("CurrentVisibilityState", value=1)
                service.configure_char("TargetVisibilityState", value=1)
        return changed

    def set_mode_visibility(self, mode, value):
        if int(value):
            self.receiver.excluded_modes.add(mode)
        else:
            self.receiver.excluded_modes.discard(mode)
        self.inputs[int(mode)].configure_char("CurrentVisibilityState", value=int(bool(value)))
        self.receiver.persist()
        self.receiver._notify()

    def select_mode(self, number):
        mode = f"{int(number):04d}"
        if mode in homekit_modes(self.receiver):
            self._command(self.receiver.set_listening_mode, mode)

    def remote_key(self, key):
        name = {
            4: "arrow_up", 5: "arrow_down", 6: "arrow_left", 7: "arrow_right",
            8: "select", 9: "back", 11: "play_pause", 15: "information",
        }.get(key)
        if name:
            self._command(self.receiver.remote_key, name)

    def refresh(self):
        for char, value in (
            (self.active, int(self.receiver.ready and self.receiver.power)),
            (self.sleep_discovery, int(not self.receiver.ready)),
            (self.active_identifier, int(self.receiver.listening_mode or 0) if self.receiver.ready else 0),
        ):
            if char.value != value:
                char.set_value(value)


class PioneerMcaccHomeKitTV(Accessory):
    """Standalone MCACC TV with selectable memories in Apple Home."""

    category = CATEGORY_TELEVISION

    def __init__(self, driver, name, receiver):
        super().__init__(driver, f"{name} MCACC")
        self.receiver = receiver
        self.set_info_service(
            manufacturer="Pioneer", model=name,
            serial_number=f"pioneer-mcacc-{receiver.host}",
        )
        tv = self.add_preload_service("Television", chars=[
            "Active", "ActiveIdentifier", "ConfiguredName", "SleepDiscoveryMode", "RemoteKey",
        ])
        tv.is_primary_service = True
        tv.configure_char("ConfiguredName", value=f"{name} MCACC")
        self.active = tv.configure_char(
            "Active", value=0, getter_callback=lambda: int(receiver.ready and receiver.power),
            setter_callback=lambda value: self._command(self.restore_active),
        )
        self.active_identifier = tv.configure_char(
            "ActiveIdentifier", value=0,
            getter_callback=lambda: int(receiver.extras.get("MC") or 0) if receiver.ready else 0,
            setter_callback=self.select_memory,
        )
        self.sleep_discovery = tv.configure_char("SleepDiscoveryMode", value=int(not receiver.ready))
        tv.configure_char("RemoteKey", setter_callback=self.remote_key)
        self.tv = tv
        self.inputs = {}
        self.refresh()

    def _command(self, func, *args):
        self.driver.loop.call_soon_threadsafe(func, *args)

    def restore_active(self):
        # The TV power control is required by HomeKit, but this TV only
        # selects MCACC memories. Keep its state tied to receiver power.
        self.active.set_value(int(self.receiver.power))

    def add_inputs(self):
        changed = False
        for memory in range(1, 7):
            key = str(memory)
            visibility = int(key in self.receiver.homekit_hidden_mcacc)
            if memory in self.inputs:
                self.inputs[memory].configure_char("CurrentVisibilityState", value=visibility)
                continue
            service = self.add_preload_service("InputSource", chars=[
                "Identifier", "ConfiguredName", "IsConfigured", "CurrentVisibilityState",
                "TargetVisibilityState", "InputSourceType",
            ], unique_id=f"memory_{memory}")
            service.configure_char("Identifier", value=memory)
            service.configure_char("ConfiguredName", value=f"Memory {memory}")
            service.configure_char("IsConfigured", value=1)
            service.configure_char("CurrentVisibilityState", value=visibility)
            service.configure_char(
                "TargetVisibilityState", value=visibility,
                setter_callback=lambda value, selected=key: self._command(
                    self.set_memory_visibility, selected, value
                ),
            )
            service.configure_char("InputSourceType", value=0)
            self.tv.add_linked_service(service)
            self.inputs[memory] = service
            changed = True
        return changed

    def set_memory_visibility(self, memory, value):
        if int(value):
            self.receiver.homekit_hidden_mcacc.add(memory)
        else:
            self.receiver.homekit_hidden_mcacc.discard(memory)
        self.inputs[int(memory)].configure_char("CurrentVisibilityState", value=int(bool(value)))
        self.receiver.persist()

    def select_memory(self, number):
        if int(number) in self.inputs:
            self._command(self.receiver.set_extra, f"{int(number)}MC", "MC")

    def remote_key(self, key):
        name = {
            4: "arrow_up", 5: "arrow_down", 6: "arrow_left", 7: "arrow_right",
            8: "select", 9: "back", 11: "play_pause", 15: "information",
        }.get(key)
        if name:
            self._command(self.receiver.remote_key, name)

    def refresh(self):
        for char, value in (
            (self.active, int(self.receiver.ready and self.receiver.power)),
            (self.sleep_discovery, int(not self.receiver.ready)),
            (self.active_identifier, int(self.receiver.extras.get("MC") or 0) if self.receiver.ready else 0),
        ):
            if char.value != value:
                char.set_value(value)


class PioneerExtraLight(Accessory):
    """Separate volume lamp inside the shared Pioneer bridge."""

    category = CATEGORY_LIGHTBULB

    def __init__(self, driver, name, receiver):
        display_name = f"Volume {name}" if receiver.options["showReceiverNameInSwitches"] else "Volume"
        super().__init__(driver, display_name)
        self.receiver = receiver
        self.set_info_service(manufacturer="Pioneer", model=name, serial_number=f"volume-{receiver.host}")
        light = self.add_preload_service("Lightbulb", chars=["Name", "On", "Brightness"])
        light.configure_char("Name", value="Volume")
        self.on = light.configure_char(
            "On", value=False,
            getter_callback=lambda: bool(receiver.ready and receiver.power and not receiver.muted),
            setter_callback=lambda value: driver.loop.call_soon_threadsafe(receiver.set_mute, not bool(value)),
        )
        self.brightness = light.configure_char(
            "Brightness", value=0, getter_callback=lambda: receiver.volume if receiver.ready else 0,
            setter_callback=lambda value: driver.loop.call_soon_threadsafe(receiver.set_volume, int(value)),
        )
        self.refresh()

    def refresh(self):
        for char, value in (
            (self.on, bool(self.receiver.ready and self.receiver.power and not self.receiver.muted)),
            (self.brightness, self.receiver.volume if self.receiver.ready and self.receiver.power and not self.receiver.muted else 0),
        ):
            if char.value != value:
                char.set_value(value)


class PioneerExtraSwitch(Accessory):
    """Selected input or listening-mode switch in the shared bridge."""

    category = CATEGORY_SWITCH

    def __init__(self, driver, name, receiver, kind, input_id=None):
        label = (receiver.input_switch_label(input_id) if kind == "input" else
                 "Telnet connection" if kind == "telnet" else "Listening Mode")
        display_name = f"{label} {name}" if receiver.options["showReceiverNameInSwitches"] else label
        super().__init__(driver, display_name)
        self.receiver, self.kind, self.input_id = receiver, kind, input_id
        self.set_info_service(manufacturer="Pioneer", model=name, serial_number=f"{kind}-{receiver.host}-{input_id or ''}")
        switch = self.add_preload_service("Switch", chars=["Name", "On"])
        self.name_char = switch.configure_char("Name", value=label)
        self.on = switch.configure_char("On", value=False, getter_callback=self.is_on, setter_callback=self.set_on)
        self.refresh()

    def is_on(self):
        receiver = self.receiver
        if self.kind == "telnet":
            return receiver.telnet_switch_on
        if self.kind == "input":
            return bool(receiver.ready and receiver.power and receiver.input_id == self.input_id)
        return bool(receiver.ready and receiver.power and (
            not receiver.listening_mode or receiver.listening_mode in (
                receiver.options["listeningMode"], receiver.options["listeningModeFallback"],
            )
        ))

    def set_on(self, value):
        if self.kind == "telnet":
            self.driver.loop.call_soon_threadsafe(
                self.receiver.turn_telnet_on if value else self.receiver.turn_telnet_off
            )
        elif self.kind == "listening":
            self.driver.loop.call_soon_threadsafe(self.receiver.toggle_mode)
        elif value:
            self.driver.loop.call_soon_threadsafe(
                lambda: self.driver.loop.create_task(self.receiver.press_input(self.input_id))
            )
        elif self.is_on():
            self.driver.loop.call_soon_threadsafe(self.receiver.set_power, False)

    def refresh(self):
        if self.kind == "input":
            label = self.receiver.input_switch_label(self.input_id)
            if self.name_char.value != label:
                self.name_char.set_value(label)
        value = self.is_on()
        if self.on.value != value:
            self.on.set_value(value)


class PioneerSharedHomeKit:
    """One HAP bridge for all receivers configured in this HA instance."""

    notification_id = "pioneer_telnet_avr_shared_homekit"

    def __init__(self, hass):
        self.hass = hass
        self.driver = None
        self.bridge = None
        self.members = {}
        self.pending = {}
        self._add_lock = asyncio.Lock()
        self._pairing_task = None

    @staticmethod
    def _aid(entry_id, suffix):
        # Keep each accessory identity stable across discovery and HA restarts.
        number = int.from_bytes(hashlib.sha256(f"{entry_id}:{suffix}".encode()).digest()[:6], "big")
        return number + 8

    async def add_receiver(self, entry, receiver):
        wants_bridge = any((
            receiver.options["homekitLinkedVolume"],
            receiver.options["listelingmodesAsTV"],
            receiver.options["mcaccControl"],
            not receiver.options["additionalEntitiesInHa"] and any((
                receiver.options["volumeAsLight"],
                receiver.options["toggleListeningMode"],
                receiver.options["inputSwitches"],
            )),
            receiver.options["telnetSwitch"] and not receiver.options["telnetSwitchInHa"],
        ))
        if not wants_bridge:
            return
        if not receiver.ready:
            if self.driver is None:
                from homeassistant.components.persistent_notification import async_dismiss
                async_dismiss(self.hass, self.notification_id)
            pending = {"entry": entry, "receiver": receiver, "task": None}

            def activate_when_ready():
                if (receiver.ready and self.pending.get(entry.entry_id) is pending
                        and (pending["task"] is None or pending["task"].done())):
                    pending["task"] = self.hass.async_create_task(
                        self._activate_pending(entry.entry_id, pending)
                    )

            pending["unsubscribe"] = receiver.subscribe(activate_when_ready)
            self.pending[entry.entry_id] = pending
            activate_when_ready()
            return
        await self._add_ready_receiver(entry, receiver)

    async def _activate_pending(self, entry_id, pending):
        if self.pending.get(entry_id) is not pending or not pending["receiver"].ready:
            return
        try:
            await self._add_ready_receiver(pending["entry"], pending["receiver"])
        except Exception:
            _LOGGER.exception("Could not add %s to the Pioneer HomeKit bridge", pending["entry"].title)
            return
        if self.pending.get(entry_id) is pending:
            self.pending.pop(entry_id)
            pending["unsubscribe"]()

    async def _add_ready_receiver(self, entry, receiver):
        async with self._add_lock:
            if receiver.ready and entry.entry_id not in self.members:
                await self._add_ready_receiver_locked(entry, receiver)

    async def _add_ready_receiver_locked(self, entry, receiver):
        if self.driver is None:
            advertiser = await zeroconf.async_get_async_instance(self.hass)

            def create_driver():
                driver = HassAccessoryDriver(
                    loop=self.hass.loop, port=65021,
                    persist_file=str(Path(self.hass.config.path(".storage")) / "pioneer_homekit_shared.state"),
                    async_zeroconf_instance=advertiser,
                )
                bridge = Bridge(driver, "Pioneer Telnet AVR")
                driver.add_accessory(bridge)
                return driver, bridge

            self.driver, self.bridge = await self.hass.async_add_executor_job(create_driver)

        def add_accessories():
            accessories = []
            if receiver.options["homekitLinkedVolume"]:
                accessories.append(("tv", PioneerHomeKitTV(self.driver, entry.title, receiver)))
            if receiver.options["listelingmodesAsTV"]:
                accessories.append(("listening_tv", PioneerListeningModesHomeKitTV(self.driver, entry.title, receiver)))
            if receiver.options["mcaccControl"]:
                accessories.append(("mcacc_tv", PioneerMcaccHomeKitTV(self.driver, entry.title, receiver)))
            if not receiver.options["additionalEntitiesInHa"]:
                if receiver.options["volumeAsLight"]:
                    accessories.append(("volume", PioneerExtraLight(self.driver, entry.title, receiver)))
                if receiver.options["toggleListeningMode"] and not (
                    receiver.options["toggleListeningModeLink"] and receiver.options["homekitLinkedVolume"]
                ):
                    accessories.append(("listening_switch", PioneerExtraSwitch(
                        self.driver, entry.title, receiver, "listening"
                    )))
                for input_id in receiver.options["inputSwitches"]:
                    accessories.append((f"input_{input_id}", PioneerExtraSwitch(
                        self.driver, entry.title, receiver, "input", input_id
                    )))
            if receiver.options["telnetSwitch"] and not receiver.options["telnetSwitchInHa"]:
                accessories.append(("telnet", PioneerExtraSwitch(
                    self.driver, entry.title, receiver, "telnet"
                )))
            for suffix, accessory in accessories:
                accessory.aid = self._aid(entry.entry_id, suffix)
                accessory.add_inputs() if hasattr(accessory, "add_inputs") else None
                accessory.refresh()
                self.bridge.add_accessory(accessory)
            return accessories

        try:
            accessories = await self.hass.async_add_executor_job(add_accessories)
        except Exception:
            if not self.members:
                await self.hass.async_add_executor_job(self.driver.http_server.server_close)
                self.driver = self.bridge = None
            raise
        if not accessories:
            return
        signature = self._signature(receiver)

        def refresh():
            for _, accessory in accessories:
                accessory.refresh()
            member = self.members.get(entry.entry_id)
            if member and signature_changed(member, receiver):
                if member["task"] is None or member["task"].done():
                    member["task"] = self.hass.async_create_task(self._refresh_inputs(entry.entry_id))

        def signature_changed(member, current_receiver):
            return member["signature"] != self._signature(current_receiver)

        self.members[entry.entry_id] = {
            "receiver": receiver, "accessories": accessories,
            "signature": signature, "unsubscribe": receiver.subscribe(refresh), "task": None,
        }
        if len(self.members) == 1:
            try:
                await self.driver.async_start()
            except Exception:
                self.members.pop(entry.entry_id)["unsubscribe"]()
                await self.hass.async_add_executor_job(self.driver.http_server.server_close)
                self.driver = self.bridge = None
                raise
            await self._start_pairing_notification()
        else:
            await self.hass.async_add_executor_job(self.driver.config_changed)

    @staticmethod
    def _signature(receiver):
        return (
            tuple(sorted(receiver.inputs.items())),
            tuple(homekit_modes(receiver)) if receiver.options["listelingmodesAsTV"] else (),
            tuple(sorted(receiver.excluded_modes)),
            tuple(sorted(receiver.homekit_hidden_mcacc)),
        )

    async def _refresh_inputs(self, entry_id):
        member = self.members.get(entry_id)
        while member and self._signature(member["receiver"]) != member["signature"]:
            for _, accessory in member["accessories"]:
                if hasattr(accessory, "add_inputs"):
                    await self.hass.async_add_executor_job(accessory.add_inputs)
                if isinstance(accessory, PioneerExtraSwitch) and accessory.kind == "input":
                    accessory.refresh()
            member["signature"] = self._signature(member["receiver"])
            await self.hass.async_add_executor_job(self.driver.config_changed)

    async def _start_pairing_notification(self):
        import pyqrcode
        from homeassistant.components.persistent_notification import async_create, async_dismiss

        def render_qr():
            qr_png = io.BytesIO()
            pyqrcode.create(self.bridge.xhm_uri()).png(qr_png, scale=6, quiet_zone=4)
            return "data:image/png;base64," + base64.b64encode(qr_png.getvalue()).decode("ascii")

        try:
            qr_image = await self.hass.async_add_executor_job(render_qr)
        except Exception:
            _LOGGER.exception("Could not create QR code for the Pioneer HomeKit bridge")
            qr_image = None
        message = (
            "Alle Pioneer-HomeKit-Geräte mit **einer** Bridge koppeln: "
            f"Code **{self.driver.state.pincode.decode()}** eingeben.\n\n"
        )
        if qr_image:
            message += f"![HomeKit-Kopplungs-QR-Code]({qr_image})\n\n"
        message += (
            "Bisher einzeln gekoppelte Pioneer-TVs in Apple Home entfernen. "
            "Den Receiver-Media-Player in der HA HomeKit Bridge ausschliessen, "
            "wenn das Haupt-TV hier ebenfalls aktiviert ist."
        )

        async def watch():
            paired = None
            while self.driver:
                current = bool(self.driver.state.paired_clients)
                if current != paired:
                    if current:
                        async_dismiss(self.hass, self.notification_id)
                    else:
                        async_create(self.hass, message, title="Pioneer HomeKit Bridge",
                                     notification_id=self.notification_id)
                    paired = current
                await asyncio.sleep(2)

        self._pairing_task = self.hass.async_create_task(watch())

    async def remove_receiver(self, entry_id):
        pending = self.pending.pop(entry_id, None)
        if pending:
            pending["unsubscribe"]()
            if pending["task"]:
                await pending["task"]
        member = self.members.pop(entry_id, None)
        if not member:
            return
        member["unsubscribe"]()
        if member["task"]:
            member["task"].cancel()
            try:
                await member["task"]
            except asyncio.CancelledError:
                pass
        for _, accessory in member["accessories"]:
            self.bridge.accessories.pop(accessory.aid, None)
        if self.members:
            await self.hass.async_add_executor_job(self.driver.config_changed)
            return
        from homeassistant.components.persistent_notification import async_dismiss
        async_dismiss(self.hass, self.notification_id)
        if self._pairing_task:
            self._pairing_task.cancel()
            try:
                await self._pairing_task
            except asyncio.CancelledError:
                pass
            self._pairing_task = None
        await self.driver.async_stop()
        self.driver = self.bridge = None
