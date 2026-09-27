"""Receiver state and commands for the classic Pioneer Telnet protocol."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
import logging
from pathlib import Path
import re
import time

from .protocol import PioneerTransport

_LOGGER = logging.getLogger(__name__)
DISPLAY_CHARS = json.loads(Path(__file__).with_name("display_chars.json").read_text())


def clean_input_name(name: str) -> str:
    """Apply the receiver's character set and 14-character name limit."""
    return "".join(char for char in name.strip() if char.isalnum() or char in " /:._-")[:14].strip()


class PioneerReceiver:
    def __init__(self, host: str, port: int, options: dict, rediscover=None, persist=None) -> None:
        self.host = host
        self.port = port
        self.options = options
        self.persist = persist
        self.power = False
        self.muted = False
        self._mute_seen = False
        self.volume = 0
        self.raw_volume = 0
        self._volume_seen = False
        self._volume_seen_on_connection = False
        self._input_seen_on_connection = False
        self._discovery_complete = False
        self.ready = False
        self.input_id: str | None = None
        self.zone_power: dict[int, bool | None] = {2: None, 3: None}
        self.zone_volume: dict[int, int | None] = {2: None, 3: None}
        self.zone_muted: dict[int, bool | None] = {2: None, 3: None}
        self.zone_input: dict[int, str | None] = {2: None, 3: None}
        self.listening_mode: str | None = None
        self.inputs: dict[str, str] = {}
        self.inputs_timestamp = 0.0
        self.learned_modes: set[str] = set()
        self.excluded_modes: set[str] = set()
        self._mode_request_generation = 0
        self.homekit_hidden_modes: set[str] = set()
        self.homekit_hidden_mcacc: set[str] = set()
        self.extras: dict[str, str] = {}
        self.display_text: str | None = None
        self._extra_updated: dict[str, float] = {}
        self._power_seen_on_connection = False
        self._audio_video_task: asyncio.Task | None = None
        self._mode_query_task: asyncio.Task | None = None
        self._volume_down_task: asyncio.Task | None = None
        self._volume_refresh_task: asyncio.Task | None = None
        self._telnet_disconnect_task: asyncio.Task | None = None
        self._last_set_volume: str | None = None
        self.available = False
        self._listeners: set[Callable[[], None]] = set()
        self._poll_task: asyncio.Task | None = None
        self._discovery_task: asyncio.Task | None = None
        self._last_switch = 0.0
        self._last_discovery = 0.0
        self._input_name_probe_id: str | None = None
        self._input_name_deadline = 0.0
        self._names_applied: set[str] = set()
        self.transport = PioneerTransport(
            host, port, self._message, self._connection,
            max_reconnect_attempts=options.get("maxReconnectAttempts", 1000),
            max_reconnect_before_discovery=options.get("maxReconnectAttemptsBeforeDiscover", 10),
            rediscover=rediscover,
        )
        # Homebridge starts its keepalive interval at receiver creation.
        self.transport.last_interaction = time.monotonic()

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()


    def start(self) -> None:
        self.transport.start()
        self._poll_task = asyncio.create_task(self._poll())

    async def stop(self) -> None:
        for task in (self._poll_task, self._discovery_task, self._audio_video_task, self._mode_query_task, self._volume_down_task, self._volume_refresh_task, self._telnet_disconnect_task):
            if task:
                task.cancel()
        await asyncio.gather(*(task for task in (self._poll_task, self._discovery_task, self._audio_video_task, self._mode_query_task, self._volume_down_task, self._volume_refresh_task, self._telnet_disconnect_task) if task), return_exceptions=True)
        await self.transport.stop()

    @property
    def telnet_switch_on(self) -> bool:
        return self.transport.connecting or self.transport.writer is not None

    def turn_telnet_on(self) -> None:
        if self._telnet_disconnect_task:
            self._telnet_disconnect_task.cancel()
            self._telnet_disconnect_task = None
        self.transport.last_interaction = time.monotonic()
        if self.transport.forced_disconnect or not self.telnet_switch_on:
            self.transport.reconnect()

    def turn_telnet_off(self) -> None:
        # Preserve Homebridge's delayed disconnect while the AVR is in use.
        if self._telnet_disconnect_task:
            self._telnet_disconnect_task.cancel()
        elapsed = time.monotonic() - self.transport.last_interaction if self.transport.last_interaction else float("inf")
        delay = 304.9 if self.power else 62 if elapsed < 62 else max(4.9, 304.9 - elapsed)
        if delay < 60:
            self.transport.forced_disconnect = True

        async def delayed_disconnect():
            try:
                await asyncio.sleep(delay)
                if not self.power and time.monotonic() - self.transport.last_interaction > 61:
                    await self.transport.disconnect()
                elif self.transport.forced_disconnect:
                    self.transport.forced_disconnect = False
                    self.transport.start()
            finally:
                if self._telnet_disconnect_task is asyncio.current_task():
                    self._telnet_disconnect_task = None

        self._telnet_disconnect_task = asyncio.create_task(delayed_disconnect())

    def _connection(self, connected: bool) -> None:
        self.available = connected
        if not connected:
            self.ready = False
            if self._discovery_task and not self._discovery_task.done():
                self._discovery_task.cancel()
            self._mode_request_generation += 1
            self._power_seen_on_connection = False
            self._mute_seen = False
            self._volume_seen_on_connection = False
            self._input_seen_on_connection = False
            self._last_set_volume = None
            if self._volume_down_task:
                self._volume_down_task.cancel()
            if self._volume_refresh_task:
                self._volume_refresh_task.cancel()
        self._notify()
        if connected:
            self._names_applied.clear()
            if self.power:
                self._schedule_audio_video_refresh()
            self.transport.send("?P", "PWR")
            self.transport.send("?V", "VOL")
            self.transport.send("?M", "MUT")
            self.transport.send("?F", "FN")
            if self.power:
                self._schedule_listening_mode_refresh()
            # Probe each zone on the same persistent receiver connection. An
            # unsupported query returns E04/E06 and leaves the zone unknown.
            if self.options["zoneControl"]:
                self.transport.send("?AP", "APR")
                self.transport.send("?BP", "BPR")
            for option, probes in (
                ("toneControls", (("?TO", "TO"), ("?BA", "BA"), ("?TR", "TR"))),
                ("mcaccControl", (("?MC", "MC"),)),
                ("phaseControl", (("?IS", "IS"),)),
                ("virtualSurroundBack", (("?VSB", "VSB"),)),
                ("channelLevels", tuple((f"?{channel}CLV", f"CLV{channel}") for channel in ("L__", "R__", "C__", "SL_", "SR_", "SBL", "SBR", "SW_", "LH_", "RH_", "LW_", "RW_"))),
                ("tunerControl", (("?FR", "FR"), ("?PR", "PR"))),
            ):
                if self.options[option]:
                    for command, key in probes:
                        self.transport.send(command, key)
            if not self._discovery_complete or time.monotonic() - self._last_discovery > 1800:
                self._last_discovery = time.monotonic()
                self._discovery_task = asyncio.create_task(self.discover_inputs())
            self._update_ready()

    def _update_ready(self) -> None:
        current_input_known = (
            self.input_id is not None
            and (bool(self.known_input_name(self.input_id))
                 or not self.input_name_pending(self.input_id))
        )
        self.ready = bool(
            self.available and self._discovery_complete and self._power_seen_on_connection
            and (not self.power or (
                self._volume_seen_on_connection and self._mute_seen
                and self._input_seen_on_connection and current_input_known
            ))
        )

    async def discover_inputs(self) -> None:
        """Probe the same 60 RGB input IDs as the Homebridge plugin."""
        responses = []
        complete = False
        try:
            for number in range(1, 61):
                input_id = f"{number:02d}"
                response = asyncio.get_running_loop().create_future()

                def complete(error: str | None, reply: str, future=response) -> None:
                    if not future.done():
                        future.set_result(error)

                self.transport.send(f"?RGB{input_id}", f"RGB{input_id}", complete)
                responses.append(response)
                await asyncio.sleep(0.055)
            # Unsupported inputs return an error; lost replies have a bounded
            # wait so one missing response cannot disable the AVR forever.
            _, pending = await asyncio.wait(responses, timeout=45)
            complete = not pending
        finally:
            if complete:
                self._discovery_complete = True
                if self.persist:
                    self.persist()
            elif self.available and self._discovery_task is asyncio.current_task():
                _LOGGER.warning("Input discovery incomplete for %s; retrying", self.host)
                asyncio.get_running_loop().call_later(15, self._retry_input_discovery)
            self._update_ready()
            self._notify()

    def _retry_input_discovery(self) -> None:
        if self.available and not self._discovery_complete and (
            self._discovery_task is None or self._discovery_task.done()
        ):
            self._last_discovery = time.monotonic()
            self._discovery_task = asyncio.create_task(self.discover_inputs())

    async def _refresh_audio_video(self) -> None:
        """Query power-on status with spacing, unless a recent reply is known."""
        for option, key in (("audioInfo", "AST"), ("videoInfo", "VST")):
            if self.options[option] and self.available and self.power:
                await asyncio.sleep(0.2)
                if key not in self._extra_updated or time.monotonic() - self._extra_updated[key] >= 300:
                    self.transport.send(f"?{key}", key)

    def _schedule_audio_video_refresh(self) -> None:
        if self._audio_video_task and not self._audio_video_task.done():
            return
        self._audio_video_task = asyncio.create_task(self._refresh_audio_video())

    def _schedule_listening_mode_refresh(self) -> None:
        if self._mode_query_task and not self._mode_query_task.done():
            return
        self._mode_query_task = asyncio.create_task(self._refresh_listening_mode())

    async def _refresh_listening_mode(self, initial_delay: float = 5.321) -> None:
        """Match the Homebridge reconnection query and ten retry attempts."""
        await asyncio.sleep(initial_delay)
        for attempt in range(10):
            if not self.available or not self.power:
                return
            answer = asyncio.get_running_loop().create_future()

            def complete(error: str | None, response: str) -> None:
                if not answer.done():
                    answer.set_result(error)

            self.transport.send("?S", "SR", complete)
            try:
                error = await asyncio.wait_for(answer, timeout=30)
            except asyncio.TimeoutError:
                error = "timeout"
            if error is None:
                return
            if attempt < 9:
                await asyncio.sleep(1.5)

    def _message(self, message: str) -> None:
        if message.startswith("FL"):
            # The first hex pair contains display flags; the next 14 are
            # characters, using the same table as the Homebridge plugin.
            raw = message[2:]
            if len(raw) == 30 and re.fullmatch(r"[0-9a-fA-F]{30}", raw):
                display = "".join(DISPLAY_CHARS.get(raw[i:i + 2].lower(), "") for i in range(2, 30, 2)).strip()
                if display != self.display_text:
                    self.display_text = display
                    self._notify()
                return
        for key, pattern in (
            ("AST", r"AST([0-9A-Za-z]{33})"), ("VST", r"VST([0-9A-Za-z]{25})"),
            ("TO", r"TO([01])"), ("BA", r"BA(0[0-9]|1[0-2])"),
            ("TR", r"TR(0[0-9]|1[0-2])"), ("MC", r"MC([1-6])"),
            ("IS", r"IS([012])"), ("VSB", r"VSB([01])"),
            ("FR", r"FR([AF][0-9]{5})"), ("PR", r"PR([A-G]0[1-9])"),
            ("CLV", r"CLV([A-Z_]{3})([0-9]{2})"),
        ):
            if match := re.fullmatch(pattern, message):
                value = match[2] if key == "CLV" else match[1]
                self.extras[key + (match[1] if key == "CLV" else "")] = value
                self._extra_updated[key] = time.monotonic()
                self._notify()
                return
        if (match := re.search(r"APR([01])", message)):
            first_seen = self.zone_power[2] is None
            self.zone_power[2] = match[1] == "0"
            if first_seen:
                self.transport.send("?ZS", "Z2F")
                self.transport.send("?ZV", "ZV")
                self.transport.send("?Z2M", "Z2MUT")
        elif (match := re.search(r"BPR([01])", message)):
            first_seen = self.zone_power[3] is None
            self.zone_power[3] = match[1] == "0"
            if first_seen:
                self.transport.send("?ZT", "Z3F")
                self.transport.send("?YV", "YV")
                self.transport.send("?Z3M", "Z3MUT")
        elif (match := re.search(r"Z([23])F(\d{2})", message)):
            self.zone_input[int(match[1])] = match[2]
        elif (match := re.search(r"(?:ZV|YV)(\d{2})", message)):
            self.zone_volume[2 if "ZV" in message else 3] = int(match[1])
        elif (match := re.search(r"Z([23])MUT([01])", message)):
            self.zone_muted[int(match[1])] = match[2] == "0"
        elif "PWR" in message:
            match = re.search(r"PWR([01])", message)
            if match:
                was_on = self.power
                self.power = match[1] == "0"
                if self.power and (not was_on or not self._power_seen_on_connection):
                    if not was_on:
                        # An off-state volume/input may be stale. Wait for a
                        # fresh snapshot before exposing the powered-on AVR.
                        self._volume_seen_on_connection = False
                        self._mute_seen = False
                        self._input_seen_on_connection = False
                        self.transport.send("?V", "VOL")
                        self.transport.send("?M", "MUT")
                        self.transport.send("?F", "FN")
                    self._schedule_audio_video_refresh()
                    self._schedule_listening_mode_refresh()
                self._power_seen_on_connection = True
                if self.power:
                    for input_id, name in self.options.get("inputNames", {}).items():
                        if input_id not in self._names_applied and self.inputs.get(input_id) != name:
                            self._names_applied.add(input_id)
                            self.rename_input(input_id, name)
        elif "MUT" in message:
            match = re.search(r"MUT([01])", message)
            if match:
                self.muted = match[1] == "0"
                self._mute_seen = True
        elif "VOL" in message:
            match = re.search(r"VOL(\d{3})", message)
            if match:
                self.raw_volume = int(match[1])
                self._volume_seen = True
                self._volume_seen_on_connection = True
                self.volume = self._decode_volume(int(match[1]))
        elif "FN" in message:
            match = re.search(r"FN(\d{2})", message)
            if match:
                self.input_id = match[1]
                self._input_seen_on_connection = True
                if self.known_input_name(self.input_id):
                    self._input_name_probe_id = None
                elif self._input_name_probe_id != self.input_id:
                    # FN can arrive before the input-name discovery reply.
                    # Query this input directly and avoid a transient "Input 25".
                    self._input_name_probe_id = self.input_id
                    self._input_name_deadline = time.monotonic() + 5
                    self.transport.send(f"?RGB{self.input_id}", f"RGB{self.input_id}")
                    asyncio.get_running_loop().call_later(5, self._finish_input_name_probe, self.input_id)
        elif message.startswith("SR"):
            match = re.fullmatch(r"SR(\d{4})", message)
            if match:
                self.listening_mode = match[1]
                changed = match[1] not in self.learned_modes or match[1] in self.excluded_modes
                self.learned_modes.add(match[1])
                self.excluded_modes.discard(match[1])
                if changed and self.persist:
                    self.persist()
        elif message.startswith("LM"):
            # LM is the playing-mode display code, not the SR set-mode ID.
            # Their numeric namespaces differ in the Pioneer protocol.
            pass
        elif "RGB" in message and not message.startswith(("E04RGB", "E06RGB")):
            match = re.search(r"RGB(\d{2}).(.+)", message)
            if match and match[2].strip():
                if self.inputs.get(match[1]) != match[2].strip():
                    self.inputs[match[1]] = match[2].strip()
                    self.inputs_timestamp = time.time()
                    if self.persist:
                        self.persist()
        self._update_ready()
        self._notify()

    @property
    def min_volume(self) -> int:
        return int(self.options.get("minVolume", 30))

    @property
    def max_volume(self) -> int:
        return int(self.options.get("maxVolume", 65))

    def _decode_volume(self, raw: int) -> int:
        low, high = self.min_volume * 1.85, self.max_volume * 1.85
        if high <= low:
            return int(raw * 100 / 185)
        return int((min(max(raw, low), high) - low) / (high - low) * 100)

    def set_volume(self, percentage: int) -> None:
        if not self.ready or not self.power:
            return
        percentage = int(percentage)
        if self._volume_seen and percentage == self.volume:
            return
        low, high = self.min_volume * 1.85, self.max_volume * 1.85
        raw = int(percentage / 100 * (high - low) + low) if self.max_volume > 0 else int(percentage * 1.85)
        command = f"{raw:03d}VL"
        if command == self._last_set_volume:
            return
        self._last_set_volume = command
        self.transport.last_interaction = time.monotonic()
        self.transport.send(command)

    def _schedule_volume_refresh(self) -> None:
        if self._volume_refresh_task:
            self._volume_refresh_task.cancel()

        async def refresh() -> None:
            await asyncio.sleep(1)
            if self.available and self.power:
                self.transport.send("?V", "VOL")
                self.transport.send("?M", "MUT")

        self._volume_refresh_task = asyncio.create_task(refresh())

    def volume_up(self) -> None:
        if self._volume_down_task:
            self._volume_down_task.cancel()
        if not self.ready or not self.power:
            return
        self.transport.last_interaction = time.monotonic()
        self.transport.send("VU", callback=lambda _error, _reply: self._schedule_volume_refresh())

    def volume_down(self) -> None:
        if self._volume_down_task:
            self._volume_down_task.cancel()
        if self._volume_refresh_task:
            self._volume_refresh_task.cancel()
        if not self.ready or not self.power:
            return
        self.transport.last_interaction = time.monotonic()

        async def step_down() -> None:
            for step in range(3):
                response = asyncio.get_running_loop().create_future()

                def complete(error: str | None, _message: str) -> None:
                    if not response.done():
                        response.set_result(error)

                self.transport.send("VD", "VOL", complete)
                try:
                    error = await asyncio.wait_for(response, timeout=40)
                except asyncio.TimeoutError:
                    break
                if error:
                    break
                if step < 2:
                    await asyncio.sleep(0.025)
            self._schedule_volume_refresh()

        self._volume_down_task = asyncio.create_task(step_down())

    def set_native_volume(self, level: float) -> None:
        if not self.ready or not self.power:
            return
        raw = int(max(0, min(1, level)) * 185)
        if self._volume_seen and raw == self.raw_volume:
            return
        command = f"{raw:03d}VL"
        if command == self._last_set_volume:
            return
        self._last_set_volume = command
        self.transport.last_interaction = time.monotonic()
        self.transport.send(command)

    def set_mute(self, mute: bool) -> None:
        self.transport.last_interaction = time.monotonic()
        if not self.ready or not self.power or (self._mute_seen and self.muted == mute):
            return
        self.transport.send("MO" if mute else "MF")

    def set_power(self, power: bool) -> None:
        if not self.ready:
            return
        self.transport.last_interaction = time.monotonic()
        self.transport.send("PO" if power else "PF")

    def set_zone_power(self, zone: int, power: bool) -> None:
        if not self.ready or self.zone_power.get(zone) is None:
            return
        self.transport.last_interaction = time.monotonic()
        self.transport.send(("AP" if zone == 2 else "BP") + ("O" if power else "F"))

    def set_zone_volume(self, zone: int, level: float) -> None:
        if not self.ready:
            return
        self.transport.last_interaction = time.monotonic()
        self.transport.send(f"{round(max(0, min(1, level)) * 81):02d}{'ZV' if zone == 2 else 'YV'}")

    def set_zone_mute(self, zone: int, mute: bool) -> None:
        if not self.ready:
            return
        self.transport.last_interaction = time.monotonic()
        self.transport.send(f"Z{zone}M{'O' if mute else 'F'}")

    def set_zone_input(self, zone: int, input_id: str) -> None:
        if not self.ready:
            return
        self.transport.last_interaction = time.monotonic()
        self.transport.send(f"{input_id}{'ZS' if zone == 2 else 'ZT'}")

    def set_input(self, input_id: str) -> None:
        if not self.ready or self.input_id == input_id:
            return
        self.transport.last_interaction = time.monotonic()
        self.transport.send(f"{input_id}FN")

    def remote_key(self, key: str) -> None:
        """Apply the same cursor and play/pause mapping as Homebridge."""
        self.transport.last_interaction = time.monotonic()
        if not self.ready or not self.power:
            return
        if key == "play_pause":
            self.toggle_mode()
            return
        command = {
            "arrow_up": "CUP", "arrow_down": "CDN",
            "arrow_left": "CLE", "arrow_right": "CRI",
            "select": "CEN", "back": "CRT", "information": "HM",
        }.get(key)
        if command:
            self.transport.send(command)

    def input_name(self, input_id: str) -> str:
        return self.known_input_name(input_id) or f"Input {input_id}"

    def known_input_name(self, input_id: str) -> str | None:
        return self.options.get("inputNames", {}).get(input_id) or self.inputs.get(input_id)

    def input_name_pending(self, input_id: str) -> bool:
        return (self._input_name_probe_id == input_id
                and time.monotonic() < self._input_name_deadline)

    def _finish_input_name_probe(self, input_id: str) -> None:
        if self.input_id == input_id and self._input_name_probe_id == input_id:
            self._update_ready()
            self._notify()

    def input_switch_label(self, input_id: str) -> str:
        """Disambiguate only selected input switches with identical names."""
        label = self.input_name(input_id)
        selected = self.options.get("inputSwitches", [])
        duplicates = sum(self.input_name(other).strip().casefold() == label.strip().casefold() for other in selected)
        return f"{label} ({input_id})" if duplicates > 1 else label

    def set_extra(self, command: str, reply_key: str | None = None) -> None:
        """Send a documented AVR command through the existing receiver session."""
        if not self.ready:
            return
        self.transport.last_interaction = time.monotonic()
        self.transport.send(command, reply_key)

    def set_listening_mode(self, mode: str) -> None:
        if not self.ready:
            return
        if not re.fullmatch(r"\d{4}", mode):
            raise ValueError("Invalid Pioneer listening mode")
        if mode in self.excluded_modes:
            return
        self._mode_request_generation += 1
        self._send_mode_with_retry(mode, self._mode_request_generation)

    def _send_mode_with_retry(
        self, mode: str, generation: int, attempt: int = 1,
        *, urgent: bool = False, fallback: str | None = None,
    ) -> None:
        if not self.ready or generation != self._mode_request_generation:
            return
        if mode in self.excluded_modes:
            return
        self.transport.last_interaction = time.monotonic()

        def answer(error: str | None, response: str) -> None:
            if not error or generation != self._mode_request_generation:
                return
            _LOGGER.debug("Listening mode %s attempt %s returned %s", mode, attempt, error)
            if attempt == 1:
                # Retry once quietly in case the receiver was briefly busy.
                asyncio.get_running_loop().call_later(
                    0.3,
                    lambda: self._send_mode_with_retry(
                        mode, generation, 2, urgent=urgent, fallback=fallback
                    ),
                )
                return
            if error == "E06" and mode not in self.excluded_modes:
                # The receiver rejected this SR parameter twice. Keep the
                # mode editable in settings; a later SR success restores it.
                self.excluded_modes.add(mode)
                if self.persist:
                    self.persist()
                self._notify()
            if fallback:
                asyncio.get_running_loop().call_later(0.1, self.set_listening_mode, fallback)

        self.transport.send(f"{'!' if urgent else ''}{mode}SR", "SR", answer)

    def reset_listening_modes(self) -> None:
        self._mode_request_generation += 1
        self.learned_modes.clear()
        if self.persist:
            self.persist()
        self._notify()

    def rename_input(self, input_id: str, name: str) -> None:
        if not self.available or not self.power:
            return
        clean = clean_input_name(name)
        if len(clean) < 2:
            _LOGGER.warning("Ignoring input %s rename: name must contain at least two valid characters", input_id)
            return
        self.transport.send(f"{clean}1RGB{input_id}")

    async def press_input(self, input_id: str) -> None:
        if not self.ready:
            return
        now = time.monotonic()
        if now - self._last_switch < 3:
            return
        self._last_switch = now
        if self.options.get("toggleOffIfActive", True) and self.power and self.input_id == input_id:
            self.set_power(False)
            return
        if not self.power:
            self.set_power(True)
            for _ in range(30):
                if self.power:
                    break
                await asyncio.sleep(0.5)
            if not self.power:
                return
            await asyncio.sleep(5)
            for _ in range(30):
                if self.ready:
                    break
                await asyncio.sleep(0.5)
        self.set_input(input_id)

    def toggle_mode(self) -> None:
        if not self.ready or not self.listening_mode:
            return
        primary = self.options.get("listeningMode", "0013")
        alternate = self.options.get("listeningModeOther", "0112")
        fallback = self.options.get("listeningModeFallback", "0101")
        for name, value in (("primary", primary), ("alternate", alternate), ("fallback", fallback)):
            if not re.fullmatch(r"\d{4}", value):
                raise ValueError(f"Invalid {name} listening mode")
        self.transport.last_interaction = time.monotonic()
        if self.listening_mode in (primary, fallback):
            self.set_listening_mode(alternate)
        else:
            self._mode_request_generation += 1
            self._send_mode_with_retry(
                primary, self._mode_request_generation, urgent=True, fallback=fallback,
            )

    async def _poll(self) -> None:
        count = 0
        while True:
            await asyncio.sleep(2.031)
            if not self.available:
                continue
            keepalive = max(5, min(20160, int(self.options.get("sendKeepAliveTimeoutMinutes", 2880))))
            if not self.power and self.transport.last_interaction and time.monotonic() - self.transport.last_interaction > keepalive * 60 + 59:
                await self.transport.disconnect()
                continue
            idle = time.monotonic() - self.transport.last_message
            if idle > 22:
                keepalive_queries = [("?P", "PWR"), ("?V", "VOL")]
                if self.power:
                    keepalive_queries.append(("?S", "SR"))
                command, reply_key = keepalive_queries[count % len(keepalive_queries)]
                self.transport.send(command, reply_key)
                count += 1
                if count % 10 == 0:
                    for zone, key in ((2, "APR"), (3, "BPR")):
                        if self.zone_power[zone] is not None:
                            self.transport.send("?AP" if zone == 2 else "?BP", key)
