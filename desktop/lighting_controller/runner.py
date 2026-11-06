"""Sequence execution boundary; a future MacRunner can implement this contract."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .model import Sequence
from .protocol import BoardStatus, Reply, load_commands, parse_status


ReplyCallback = Callable[[Reply], None]


class CommandTransport(Protocol):
    @property
    def ready(self) -> bool: ...

    @property
    def busy(self) -> bool: ...

    def send(self, command: str, callback: ReplyCallback) -> None: ...


class SequenceRunner(Protocol):
    def start(self, sequence: Sequence) -> None: ...

    def stop(self) -> None: ...

    def request_status(self) -> None: ...


class UnoRunner:
    """Upload and start a sequence; the Uno owns all subsequent timing."""

    def __init__(self, transport: CommandTransport) -> None:
        self.transport = transport
        self.busy = False
        self.stop_requested = False
        self._commands: list[str] = []
        self.on_status: Callable[[BoardStatus], None] = lambda status: None
        self.on_error: Callable[[str], None] = lambda message: None
        self.on_started: Callable[[], None] = lambda: None
        self.on_stopped: Callable[[], None] = lambda: None

    def start(self, sequence: Sequence) -> None:
        if not self.transport.ready:
            raise RuntimeError("Connect to the Uno first")
        if self.busy or self.transport.busy:
            raise RuntimeError("Wait for the current command to finish")
        self._commands = load_commands(sequence)
        self.busy = True
        self.stop_requested = False
        self._send_next()

    def connection_lost(self) -> None:
        """Discard a partial upload when the transport closes or the Uno resets."""
        self.busy = False
        self.stop_requested = False
        self._commands.clear()

    def _send_next(self) -> None:
        if self.stop_requested:
            self._commands.clear()
            self._send_stop()
            return
        if not self._commands:
            self.busy = False
            self.on_started()
            self.request_status()
            return
        command = self._commands.pop(0)
        try:
            self.transport.send(command, self._after_load_command)
        except RuntimeError as exc:
            self.busy = False
            self._commands.clear()
            self.on_error(str(exc))

    def _after_load_command(self, reply: Reply) -> None:
        if reply.error:
            self.busy = False
            self.stop_requested = False
            self._commands.clear()
            self.on_error(f"Board rejected command: {reply.error}")
        elif self.stop_requested:
            self._commands.clear()
            self._send_stop()
        else:
            self._send_next()

    def stop(self) -> None:
        if not self.transport.ready:
            raise RuntimeError("The Uno is not connected")
        self.stop_requested = True
        if self.busy or self.transport.busy:
            return
        self._send_stop()

    def _send_stop(self) -> None:
        self.stop_requested = False
        self.busy = True
        try:
            self.transport.send("STOP", self._after_stop)
        except RuntimeError as exc:
            self.busy = False
            self.on_error(str(exc))

    def _after_stop(self, reply: Reply) -> None:
        self.busy = False
        if reply.error:
            self.on_error(f"Stop failed: {reply.error}")
            return
        self.on_stopped()
        self.request_status()

    def request_status(self) -> None:
        if not self.transport.ready or self.busy or self.transport.busy:
            return
        try:
            self.transport.send("STATUS", self._after_status)
        except RuntimeError as exc:
            self.on_error(str(exc))

    def _after_status(self, reply: Reply) -> None:
        if self.stop_requested:
            self._send_stop()
            return
        if reply.error:
            self.on_error(f"Status request failed: {reply.error}")
            return
        try:
            self.on_status(parse_status(reply))
        except ValueError as exc:
            self.on_error(str(exc))
