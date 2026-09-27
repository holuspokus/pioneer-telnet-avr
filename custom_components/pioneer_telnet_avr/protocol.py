"""Pioneer Telnet transport ported from homebridge-pioneer-avr-2025.

Reference: src/telnet-avr/{connection,dataHandler,messageQueue}.ts at
0ca97ba7f86f98b2069672d2f1e79d10422e6b9b. This transport intentionally
preserves command queue semantics; device state is handled by the integration.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
import logging
import re
import time

_LOGGER = logging.getLogger(__name__)
ResponseCallback = Callable[[str | None, str], None]
MessageCallback = Callable[[str], None]


@dataclass
class Pending:
    command: str
    key: str
    callback: ResponseCallback | None = None
    sent: bool = False


@dataclass
class Lock:
    created: float
    message: str | None


class PioneerTransport:
    """One persistent TCP session with the original plugin's reply-key queue."""

    def __init__(
        self,
        host: str,
        port: int,
        on_message: MessageCallback,
        on_connection: Callable[[bool], None],
        *,
        max_reconnect_attempts: int = 1000,
        max_reconnect_before_discovery: int = 10,
        rediscover: Callable[[], object] | None = None,
        on_command_error: Callable[[str, str], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.on_message = on_message
        self.on_connection = on_connection
        self.max_reconnect_attempts = max(100, min(100000, max_reconnect_attempts))
        self.max_reconnect_before_discovery = max(10, min(100, max_reconnect_before_discovery))
        self.rediscover = rediscover
        self.on_command_error = on_command_error
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.ready = False
        self.connecting = False
        self.forced_disconnect = False
        self.last_write = 0.0
        self.last_message = 0.0
        self.last_interaction = 0.0
        self._queue: deque[Pending] = deque()
        self._locks: dict[str, Lock] = {}
        self._runner: asyncio.Task | None = None
        self._queue_task: asyncio.Task | None = None
        self._reader_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._activity_at = 0.0
        self._connected_at = 0.0
        self._response_timeout = 35.0
        self._stopping = False

    def start(self) -> None:
        if self._runner is None or self._runner.done():
            self._stopping = False
            self._runner = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        for task in (self._reader_task, self._queue_task, self._watchdog_task, self._runner):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (self._reader_task, self._queue_task, self._watchdog_task, self._runner) if task),
            return_exceptions=True,
        )
        await self._close()

    async def disconnect(self) -> None:
        self.forced_disconnect = True
        await self._close()

    def reconnect(self) -> None:
        self.forced_disconnect = False
        async def resume():
            if self._runner and not self._runner.done():
                self._runner.cancel()
                await asyncio.gather(self._runner, return_exceptions=True)
            self.start()
        asyncio.create_task(resume())

    def send(self, command: str, key: str | None = None, callback: ResponseCallback | None = None) -> None:
        """Queue queries and reply-tracked commands; send idle actions directly."""
        if self.forced_disconnect and time.monotonic() - self.last_interaction < 60:
            self.reconnect()
        if (
            not command.startswith(("?", "!"))
            and self.writer is not None
            and key is None
            and not self._queue
        ):
            self._direct_send(command, callback)
            return
        reply_key = key or "!none"
        pending = Pending(command, reply_key, callback)
        if command == "?P":
            self._queue.appendleft(pending)
        else:
            self._queue.append(pending)
        if not self.forced_disconnect:
            self.start()

    async def _run(self) -> None:
        attempts = 0
        while not self._stopping and not self.forced_disconnect:
            try:
                self.connecting = True
                self.reader, self.writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), timeout=30
                )
                self.connecting = False
                attempts = 0
                self._connected_at = self._activity_at = time.monotonic()
                self.last_write = self.last_message = 0.0
                self._queue_task = asyncio.create_task(self._process_queue())
                self._reader_task = asyncio.create_task(self._read())
                await asyncio.sleep(0.5)
                got_power = asyncio.get_running_loop().create_future()

                def handshake(error: str | None, response: str) -> None:
                    if not got_power.done():
                        got_power.set_result(error is None)

                self.send("?P", "PWR", handshake)
                if not await asyncio.wait_for(got_power, timeout=30):
                    raise ConnectionError("Power query failed")
                self.ready = True
                self.on_connection(True)
                self._watchdog_task = asyncio.create_task(self._watch_connection())
                await self._reader_task
            except (OSError, ConnectionError, asyncio.TimeoutError) as error:
                _LOGGER.debug("Pioneer connection: %s", error)
            finally:
                self.connecting = False
                await self._close()
            if self._stopping or self.forced_disconnect:
                break
            attempts += 1
            if attempts >= self.max_reconnect_attempts:
                break
            if attempts >= self.max_reconnect_before_discovery and self.rediscover:
                try:
                    result = self.rediscover()
                    if asyncio.iscoroutine(result):
                        result = await result
                    if result:
                        self.host, self.port = result
                        attempts = 0
                except Exception:
                    _LOGGER.exception("Receiver rediscovery failed")
            delay = 0 if attempts == 1 or time.monotonic() - self.last_interaction < 60 else (60 if attempts > 30 else 15)
            await asyncio.sleep(delay)

    async def _close(self) -> None:
        if self.ready:
            self.ready = False
            self.on_connection(False)
        for task in (self._queue_task, self._watchdog_task, self._reader_task):
            if task and task is not asyncio.current_task() and not task.done():
                task.cancel()
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except OSError:
                pass
        self.reader = None
        self.writer = None
        self._queue.clear()
        self._locks.clear()

    async def _process_queue(self) -> None:
        while self.writer and not self._stopping:
            await asyncio.sleep(0.007)
            if not self._queue:
                continue
            now = time.monotonic()
            for key, lock in list(self._locks.items()):
                if lock.message and key != "!none" and now - lock.created > 13:
                    self._direct_send(lock.message)
                    lock.message = None
                elif now - lock.created > 25:
                    del self._locks[key]
            pending = self._queue[0]
            if pending.key in self._locks:
                continue
            self._locks[pending.key] = Lock(now, pending.command)
            if now - self.last_write < 0.017:
                await asyncio.sleep(0.007)
            pending.sent = True
            self._direct_send(pending.command)

    def _direct_send(self, command: str, callback: ResponseCallback | None = None) -> None:
        if not self.writer or self.writer.is_closing():
            return
        wire_command = command.removeprefix("!")
        self.writer.write((wire_command + "\r\n").encode())
        self.last_write = time.monotonic()
        self._activity_at = self.last_write
        if callback:
            callback(None, f"{wire_command}:SENT")

    async def _read(self) -> None:
        assert self.reader is not None
        while line := await self.reader.readline():
            self.last_message = time.monotonic()
            self._activity_at = self.last_message
            response = line.decode(errors="replace").strip()
            if response:
                self._handle_response(response)

    async def _watch_connection(self) -> None:
        """Detect a silent receiver even when keepalive writes still succeed."""
        while not self._stopping and self.writer:
            await asyncio.sleep(min(1.0, self._response_timeout / 2))
            now = time.monotonic()
            last_reply = self.last_message or self._connected_at
            no_reply_to_keepalive = (
                self.last_write - last_reply > 10
                and now - last_reply > 60
            )
            if not no_reply_to_keepalive and now - self._activity_at <= self._response_timeout:
                continue
            _LOGGER.warning("Pioneer receiver %s stopped responding; reconnecting", self.host)
            if self.writer:
                self.writer.close()
            return

    def _handle_response(self, response: str) -> None:
        if response.startswith("FL"):
            # The Homebridge transport handles display messages out of band.
            # They must not consume a queued reply, even while a query waits.
            self.on_message(response)
            return
        matched = False
        if re.fullmatch(r"E\d{2}", response) and self._queue and self._queue[0].sent:
            pending = self._queue[0]
            key = pending.key
            if self.on_command_error:
                self.on_command_error(pending.command, response)
            if pending.callback:
                pending.callback(response, response + key)
            self._finish(key)
            matched = True
        elif self._queue and self._queue[0].sent:
            pending = self._queue[0]
            if pending.key in response or pending.key == "!none":
                if pending.callback:
                    pending.callback(None, response)
                self._finish(pending.key)
                matched = True
        if not matched or response.startswith(("PWR", "VOL", "MUT", "FN", "SR", "LM", "RGB", "APR", "BPR", "ZV", "YV", "Z2F", "Z3F", "Z2MUT", "Z3MUT", "AST", "VST", "TO", "BA", "TR", "MC", "IS", "VSB", "CLV", "FR", "PR")):
            self.on_message(response)

    def _finish(self, key: str) -> None:
        if self._queue:
            self._queue.popleft()
        self._locks.pop(key, None)
