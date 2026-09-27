"""Zero-configuration discovery with an optional manual fallback."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.helpers import selector
from homeassistant.helpers.storage import Store

from .const import DEFAULT_MODE_IDS, DEFAULT_OPTIONS, DOMAIN
from .discovery import Receiver, discover
from .discovery import PORTS, _port_open
from .receiver import clean_input_name

MODE_NAMES = json.loads(Path(__file__).with_name("modes.json").read_text())


class PioneerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._found: list[Receiver] = []

    def _unconfigured(self, receivers: list[Receiver]) -> list[Receiver]:
        """Offer only receivers that do not already have a config entry."""
        entries = self.hass.config_entries.async_entries(DOMAIN)
        configured_ids = {entry.unique_id.casefold() for entry in entries if entry.unique_id}
        configured_hosts = {entry.data["host"].casefold() for entry in entries}
        return [receiver for receiver in receivers if
                receiver.fqdn.casefold() not in configured_ids and
                receiver.host.casefold() not in configured_hosts]

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return await self.async_step_manual()
        self._found = self._unconfigured(await discover(self.hass, collect_all=True))
        if self._found:
            return await self.async_step_choose()
        return await self.async_step_manual()

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo):
        host = getattr(discovery_info, "hostname", None) or discovery_info.host
        if not host:
            return self.async_abort(reason="cannot_connect")
        port = None
        for candidate in PORTS:
            if await _port_open(host, candidate):
                port = candidate
                break
        if port is None:
            return self.async_abort(reason="cannot_connect")
        receiver = Receiver(
            discovery_info.name.split(".", 1)[0].split("@")[-1], discovery_info.name,
            host.removesuffix("."), port, discovery_info.name,
        )
        await self.async_set_unique_id(receiver.fqdn)
        self._abort_if_unique_id_configured(updates={"host": receiver.host, "port": receiver.port})
        if not self._unconfigured([receiver]):
            return self.async_abort(reason="already_configured")
        self._found = [receiver]
        self.context["title_placeholders"] = {"name": receiver.name}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(self, user_input=None):
        if user_input is not None:
            return await self._create(self._found[0], user_input["showReceiverNameInSwitches"])
        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=vol.Schema({vol.Optional("showReceiverNameInSwitches", default=False): bool}),
            description_placeholders={"name": self._found[0].name},
        )

    async def async_step_choose(self, user_input=None):
        if user_input:
            return await self._create(self._found[int(user_input["receiver"])], user_input["showReceiverNameInSwitches"])
        options = {str(i): f"{device.name} ({device.host})" for i, device in enumerate(self._found)}
        return self.async_show_form(step_id="choose", data_schema=vol.Schema({
            vol.Required("receiver"): vol.In(options),
            vol.Optional("showReceiverNameInSwitches", default=False): bool,
        }))

    async def async_step_manual(self, user_input=None):
        if user_input:
            host = user_input["host"].strip()
            configured_port = user_input.get("port")
            ports = (configured_port,) if configured_port else PORTS
            port = None
            for candidate in ports:
                if await _port_open(host, candidate):
                    port = candidate
                    break
            if port is None:
                return self.async_show_form(
                    step_id="manual", errors={"base": "cannot_connect"},
                    data_schema=self._manual_schema(host=host, show_name=user_input["showReceiverNameInSwitches"]),
                )
            name = host.removesuffix(".local").split(".")[0]
            return await self._create(Receiver(name, name, host, port, host), user_input["showReceiverNameInSwitches"])
        return self.async_show_form(step_id="manual", data_schema=self._manual_schema())

    @staticmethod
    def _manual_schema(host=None, show_name=False):
        host_field = vol.Required("host", default=host) if host else vol.Required("host")
        return vol.Schema({
            host_field: str,
            vol.Optional("port"): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Optional("showReceiverNameInSwitches", default=show_name): bool,
        })

    async def _create(self, receiver: Receiver, show_receiver_name: bool):
        if not self._unconfigured([receiver]):
            return self.async_abort(reason="already_configured")
        await self.async_set_unique_id(receiver.fqdn)
        self._abort_if_unique_id_configured(updates={"host": receiver.host, "port": receiver.port})
        return self.async_create_entry(title=receiver.name, data={"host": receiver.host, "port": receiver.port, "fqdn": receiver.fqdn, "orig_name": receiver.orig_name}, options={"showReceiverNameInSwitches": show_receiver_name})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PioneerOptionsFlow(config_entry)


class PioneerOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        receiver = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        if user_input is not None:
            # Home Assistant returns section values as nested dictionaries.
            user_input = {
                key: value
                for values in user_input.values()
                for key, value in values.items()
            }
            user_input.pop("homekitListeningModesTv", None)
            reset_modes = user_input.pop("resetListeningModes", False)
            for mode in ("listeningMode", "listeningModeOther", "listeningModeFallback"):
                if not re.fullmatch(r"\d{4}", user_input[mode]):
                    return self.async_show_form(
                        step_id="init", errors={"base": "invalid_listening_mode"},
                        data_schema=self._options_schema(),
                    )
            for source, target in (("input_switches", "inputSwitches"),):
                chosen = user_input.pop(source)
                if isinstance(chosen, str):
                    chosen = [part.strip().zfill(2) for part in chosen.split(",") if part.strip()]
                user_input[target] = chosen
            for source, target in (
                ("hidden_mcacc_memories", "hiddenMcaccMemories"),
                ("hidden_zone_inputs", "hiddenZoneInputs"),
            ):
                user_input[target] = user_input.pop(source, self._entry.options.get(target, []))
            excluded_modes = set(user_input.pop("excluded_listening_modes", []))
            if len(user_input["inputSwitches"]) > 5:
                return self.async_show_form(step_id="init", errors={"base": "too_many_inputs"}, data_schema=self._options_schema())
            if receiver:
                receiver.excluded_modes = excluded_modes
                receiver._notify()
                # Save before the options update reloads the receiver.
                store = Store(self.hass, 1, f"{DOMAIN}_{self._entry.entry_id}")
                saved = await store.async_load() or {}
                # The normal store writes are delayed. A settings reload must
                # not revive an older, incomplete input-name discovery cache.
                if receiver.inputs:
                    saved["inputs"] = dict(receiver.inputs)
                    saved["inputs_timestamp"] = receiver.inputs_timestamp
                    saved["inputs_complete"] = receiver._discovery_complete
                saved["learned_modes"] = sorted(receiver.learned_modes)
                saved["excluded_modes"] = sorted(excluded_modes)
                await store.async_save(saved)
            user_input["inputNames"] = {
                key.removeprefix("input_name_"): clean_input_name(value)
                for key, value in list(user_input.items())
                if key.startswith("input_name_") and len(clean_input_name(value)) >= 2 and (
                    receiver is None or clean_input_name(value) != receiver.inputs.get(key.removeprefix("input_name_"))
                )
            }
            for key in list(user_input):
                if key.startswith("input_name_"):
                    del user_input[key]
            if reset_modes and receiver:
                receiver.reset_listening_modes()
                # The options update reloads this entry immediately. Save the
                # reset before the reload reads the learned-mode store.
                store = Store(self.hass, 1, f"{DOMAIN}_{self._entry.entry_id}")
                saved = await store.async_load() or {}
                saved.update({"learned_modes": [], "mode_learning_version": 3})
                for obsolete in ("rejected_modes", "unavailable_modes_by_input", "unavailable_audio_by_input"):
                    saved.pop(obsolete, None)
                await store.async_save(saved)
            return self.async_create_entry(title="", data={
                **user_input,
                "showReceiverNameInSwitches": self._entry.options.get("showReceiverNameInSwitches", False),
            })
        if receiver and receiver.available and not receiver.inputs:
            # The initial scan runs in the background. Give its replies time to
            # arrive before building the form, and retry if it has finished.
            if receiver._discovery_task and not receiver._discovery_task.done():
                await receiver._discovery_task
            if not receiver.inputs:
                await receiver.discover_inputs()
            for _ in range(100):
                if receiver.inputs or not receiver.available:
                    break
                await asyncio.sleep(0.1)
        return self.async_show_form(step_id="init", data_schema=self._options_schema())

    def _options_schema(self):
        options = {**DEFAULT_OPTIONS, **self._entry.options}
        if "listelingmodesAsTV" not in self._entry.options:
            options["listelingmodesAsTV"] = self._entry.options.get("homekitListeningModesTv", False)
        receiver = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        if receiver and receiver.inputs:
            input_options = [selector.SelectOptionDict(value=input_id, label=f"{receiver.input_name(input_id)} ({input_id})") for input_id in sorted(receiver.inputs)]
            choices = selector.SelectSelector(selector.SelectSelectorConfig(options=input_options, multiple=True, mode=selector.SelectSelectorMode.DROPDOWN))
            inputs_schema = {
                vol.Optional("input_switches", default=options["inputSwitches"]): choices,
            }
        else:
            inputs_schema = {
                vol.Optional("input_switches", default=",".join(options["inputSwitches"])): str,
            }
        schema = {
            **inputs_schema,
            vol.Optional("toggleOffIfActive", default=options["toggleOffIfActive"]): bool,
            vol.Optional("toggleListeningMode", default=options["toggleListeningMode"]): bool,
            vol.Optional("toggleListeningModeLink", default=options["toggleListeningModeLink"]): bool,
            vol.Optional("telnetSwitch", default=options["telnetSwitch"]): bool,
            vol.Optional("telnetSwitchInHa", default=options["telnetSwitchInHa"]): bool,
            vol.Optional("sendKeepAliveTimeoutMinutes", default=options["sendKeepAliveTimeoutMinutes"]): vol.Coerce(int),
            vol.Optional("listelingmodesAsTV", default=options["listelingmodesAsTV"]): bool,
            vol.Optional("homekitLinkedVolume", default=options["homekitLinkedVolume"]): bool,
            vol.Optional("volumeAsLight", default=options["volumeAsLight"]): bool,
            vol.Optional("additionalEntitiesInHa", default=options["additionalEntitiesInHa"]): bool,
            vol.Optional("zoneControl", default=options["zoneControl"]): bool,
            vol.Optional("onlyLearnedListeningModes", default=options["onlyLearnedListeningModes"]): bool,
            vol.Optional("resetListeningModes", default=False): bool,
        }
        learned_only = options["onlyLearnedListeningModes"]
        # With fewer than ten observed modes the three configuration dropdowns
        # would otherwise be nearly empty. This affects the options form only;
        # the learned-mode filter for exposed receiver/TV sources is unchanged.
        mode_ids = (
            sorted(receiver.learned_modes)
            if receiver and learned_only and len(receiver.learned_modes) >= 10
            else sorted(MODE_NAMES)
        )
        mode_ids = sorted((set(mode_ids) | DEFAULT_MODE_IDS) - (receiver.excluded_modes if receiver else set()))
        if not mode_ids:
            # Keep the selectors valid even when every known mode is excluded.
            mode_ids = sorted(DEFAULT_MODE_IDS)
        def multiple(values):
            return selector.SelectSelector(selector.SelectSelectorConfig(
                options=values, multiple=True, mode=selector.SelectSelectorMode.DROPDOWN,
            ))

        excluded_choices = sorted(set(MODE_NAMES) | DEFAULT_MODE_IDS |
                                  (receiver.learned_modes if receiver else set()) |
                                  (receiver.excluded_modes if receiver else set()))
        schema[vol.Optional("excluded_listening_modes", default=sorted(receiver.excluded_modes) if receiver else [])] = multiple([
            selector.SelectOptionDict(value=mode, label=f"{MODE_NAMES.get(mode, 'Mode')} ({mode})")
            for mode in excluded_choices
        ])
        schema[vol.Optional("hidden_mcacc_memories", default=options["hiddenMcaccMemories"])] = multiple([
            selector.SelectOptionDict(value=str(i), label=f"Memory {i}") for i in range(1, 7)
        ])
        if receiver and receiver.inputs:
            schema[vol.Optional("hidden_zone_inputs", default=options["hiddenZoneInputs"])] = multiple([
                selector.SelectOptionDict(value=input_id, label=f"{receiver.input_name(input_id)} ({input_id})")
                for input_id in sorted(receiver.inputs)
            ])
        for mode in ("listeningMode", "listeningModeOther", "listeningModeFallback"):
            available = mode_ids
            choices = [selector.SelectOptionDict(
                value=value,
                label=f"{MODE_NAMES.get(value, 'Listening Mode')} ({value})",
            ) for value in available]
            default_mode = options[mode] if options[mode] in available else available[0]
            schema[vol.Optional(mode, default=default_mode)] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=choices, mode=selector.SelectSelectorMode.DROPDOWN)
            )
        schema.update({
            vol.Optional("minVolume", default=options["minVolume"]): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            vol.Optional("maxVolume", default=options["maxVolume"]): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            vol.Optional("maxReconnectAttempts", default=options["maxReconnectAttempts"]): vol.Coerce(int),
            vol.Optional("maxReconnectAttemptsBeforeDiscover", default=options["maxReconnectAttemptsBeforeDiscover"]): vol.Coerce(int),
            **{vol.Optional(key, default=options[key]): bool for key in (
                "audioInfo", "videoInfo", "toneControls", "mcaccControl",
                "phaseControl", "virtualSurroundBack", "channelLevels", "tunerControl",
            )},
        })
        if receiver:
            for input_id, label in sorted(receiver.inputs.items()):
                schema[vol.Optional(f"input_name_{input_id}", default=options["inputNames"].get(input_id, label))] = str
        groups = {
            "inputs": {"input_switches", "toggleOffIfActive", "hidden_zone_inputs"},
            "listening_modes": {
                "toggleListeningMode", "toggleListeningModeLink", "listelingmodesAsTV",
                "onlyLearnedListeningModes", "resetListeningModes", "excluded_listening_modes",
                "listeningMode", "listeningModeOther", "listeningModeFallback",
            },
            "homekit": {"homekitLinkedVolume", "volumeAsLight", "additionalEntitiesInHa"},
            "volume_zones": {"minVolume", "maxVolume", "zoneControl"},
            "receiver_features": {
                "audioInfo", "videoInfo", "toneControls", "mcaccControl", "phaseControl",
                "virtualSurroundBack", "channelLevels", "tunerControl", "hidden_mcacc_memories",
            },
            "connection": {
                "telnetSwitch", "telnetSwitchInHa", "sendKeepAliveTimeoutMinutes",
                "maxReconnectAttempts", "maxReconnectAttemptsBeforeDiscover",
            },
        }
        grouped = {}
        for name, field_names in groups.items():
            fields = {key: value for key, value in schema.items() if key.schema in field_names}
            grouped[vol.Required(name)] = section(
                vol.Schema(fields), {"collapsed": name not in ("inputs", "listening_modes")}
            )
        input_names = {key: value for key, value in schema.items() if key.schema.startswith("input_name_")}
        if input_names:
            grouped[vol.Required("input_names")] = section(vol.Schema(input_names), {"collapsed": True})
        return vol.Schema(grouped)
