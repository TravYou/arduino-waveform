"""Event-driven Qt serial link; exactly one request is in flight."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QIODevice, QTimer, Signal
from PySide6.QtSerialPort import QSerialPort

from .protocol import Reply, parse_reply


class SerialTransport(QObject):
    ready_changed = Signal(bool)
    error_message = Signal(str)
    board_reset = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.port = QSerialPort(self)
        self.port.setBaudRate(115200)
        self.port.readyRead.connect(self._read_available)
        self.port.errorOccurred.connect(self._port_error)
        self._buffer = bytearray()
        self._pending: tuple[str, Callable[[Reply], None]] | None = None
        self._ready = False
        self._opening = False
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._timed_out)
        self._boot_timer = QTimer(self)
        self._boot_timer.setSingleShot(True)
        self._boot_timer.timeout.connect(self._probe)

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def busy(self) -> bool:
        return self._pending is not None

    def open_port(self, system_location: str) -> None:
        self.close_port()
        self.port.setPortName(system_location)
        self.port.setBaudRate(115200)
        if not self.port.open(QIODevice.OpenModeFlag.ReadWrite):
            raise RuntimeError(f"Could not open {system_location}: {self.port.errorString()}")
        # Opening an Uno R3 port resets it. Let the bootloader finish first.
        self._opening = True
        self._boot_timer.start(2000)

    def close_port(self) -> None:
        self._boot_timer.stop()
        self._timeout.stop()
        self._opening = False
        self._pending = None
        self._buffer.clear()
        was_ready = self._ready
        self._ready = False
        if self.port.isOpen():
            self.port.close()
        if was_ready:
            self.ready_changed.emit(False)

    def _probe(self) -> None:
        if self.port.isOpen():
            try:
                self._send_raw("HELLO", self._on_hello)
            except RuntimeError as exc:
                self.error_message.emit(str(exc))
                self.close_port()

    def _on_hello(self, reply: Reply) -> None:
        self._opening = False
        if reply.error or reply.command != "HELLO" or reply.fields != ("1", "32"):
            self.error_message.emit("Uno firmware did not answer HELLO for protocol v1")
            self.close_port()
            return
        self._ready = True
        self.ready_changed.emit(True)

    def send(self, command: str, callback: Callable[[Reply], None]) -> None:
        if not self._ready:
            raise RuntimeError("The Uno is not ready")
        self._send_raw(command, callback)

    def _send_raw(self, command: str, callback: Callable[[Reply], None]) -> None:
        if not self.port.isOpen() or self._pending is not None:
            raise RuntimeError("Serial port is closed or another command is pending")
        if not command or "\n" in command or "\r" in command or not command.isascii():
            raise ValueError("Serial commands must be single ASCII lines")
        self._pending = (command.split()[0], callback)
        payload = (command + "\n").encode("ascii")
        if self.port.write(payload) != len(payload):
            self._pending = None
            raise RuntimeError(f"Could not write to the Uno: {self.port.errorString()}")
        self._timeout.start(2500)

    def _read_available(self) -> None:
        self._buffer.extend(bytes(self.port.readAll()))
        if len(self._buffer) > 512:
            self.error_message.emit("Serial response exceeded 512 bytes")
            self.close_port()
            return
        while b"\n" in self._buffer:
            raw, _, rest = self._buffer.partition(b"\n")
            self._buffer = bytearray(rest)
            line = raw.rstrip(b"\r").decode("ascii", errors="replace")
            if not line:
                continue
            if line.startswith("READY "):
                if self._ready:
                    self.board_reset.emit()
                    self.close_port()
                continue
            if self._pending is None:
                continue
            verb, callback = self._pending
            try:
                reply = parse_reply(line)
            except ValueError:
                self.error_message.emit(f"Unexpected serial response: {line}")
                continue
            if reply.error is None and reply.command != verb:
                self.error_message.emit(f"Expected {verb}, got {reply.command}")
                continue
            self._pending = None
            self._timeout.stop()
            callback(reply)

    def _timed_out(self) -> None:
        if self._pending is None:
            return
        _, callback = self._pending
        self._pending = None
        # A late reply cannot be correlated safely with a subsequent command.
        self.close_port()
        callback(Reply("", (), "TIMEOUT"))

    def _port_error(self, error: QSerialPort.SerialPortError) -> None:
        if error in (QSerialPort.SerialPortError.NoError,
                     QSerialPort.SerialPortError.NotOpenError):
            return
        if error in (QSerialPort.SerialPortError.ResourceError,
                     QSerialPort.SerialPortError.DeviceNotFoundError,
                     QSerialPort.SerialPortError.PermissionError):
            message = self.port.errorString()
            self.close_port()
            self.error_message.emit(f"Serial connection lost: {message}")
