"""Qt Widgets editor for whole-strip lighting sequences."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtSerialPort import QSerialPortInfo
from PySide6.QtWidgets import (
    QApplication, QComboBox, QColorDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget, QMainWindow,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .model import MAX_STEPS, Sequence, Step
from .protocol import BoardStatus
from .runner import UnoRunner
from .serial_transport import SerialTransport


PRESET_DIR = Path(__file__).resolve().parents[2] / "presets"


def describe(step: Step, number: int) -> str:
    color = f"#{step.red:02X}{step.green:02X}{step.blue:02X}"
    mode = {"solid": "Solid", "blink": f"Blink {step.blink_hz:g} Hz", "off": "Off"}[step.mode]
    color_part = f" {color}" if step.mode != "off" else ""
    return f"{number}. {mode}{color_part} · {step.duration_ms // 1000} s · {step.brightness}%"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Uno Lighting Controller")
        self.resize(780, 540)
        self.steps: list[Step] = [Step()]
        self._active_steps: list[Step] = []
        self.transport = SerialTransport()
        self.runner = UnoRunner(self.transport)
        self.runner.on_status = self._board_status
        self.runner.on_error = self._show_error
        self.runner.on_started = self._started
        self.runner.on_stopped = self._stopped
        self.transport.ready_changed.connect(self._connection_changed)
        self.transport.error_message.connect(self._show_error)
        self.transport.board_reset.connect(self._board_reset)
        self._syncing = False
        self._running = False
        self._uploading = False
        self._current_color = QColor(0, 255, 0)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        connection = QHBoxLayout()
        self.port_combo = QComboBox()
        self.refresh_button = QPushButton("Refresh ports")
        self.connect_button = QPushButton("Connect")
        connection.addWidget(QLabel("Uno port:"))
        connection.addWidget(self.port_combo, 1)
        connection.addWidget(self.refresh_button)
        connection.addWidget(self.connect_button)
        outer.addLayout(connection)

        content = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("Sequence (runs once, then turns off)"))
        self.step_list = QListWidget()
        left.addWidget(self.step_list, 1)
        add_row = QHBoxLayout()
        self.add_button = QPushButton("Add step")
        self.remove_button = QPushButton("Remove")
        self.up_button = QPushButton("Move up")
        self.down_button = QPushButton("Move down")
        for button in (self.add_button, self.remove_button, self.up_button, self.down_button):
            add_row.addWidget(button)
        left.addLayout(add_row)
        file_row = QHBoxLayout()
        self.open_button = QPushButton("Open preset…")
        self.save_button = QPushButton("Save preset…")
        file_row.addWidget(self.open_button)
        file_row.addWidget(self.save_button)
        left.addLayout(file_row)
        content.addLayout(left, 3)

        editor = QGroupBox("Selected step")
        form = QFormLayout(editor)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Solid", "solid")
        self.mode_combo.addItem("Blink", "blink")
        self.mode_combo.addItem("Off", "off")
        self.color_button = QPushButton("Choose color…")
        self.brightness_spin = QSpinBox()
        self.brightness_spin.setRange(0, 100)
        self.brightness_spin.setSuffix(" %")
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 86_400)
        self.duration_spin.setSuffix(" s")
        self.duration_spin.setToolTip("Duration in seconds (60 = 1 minute)")
        self.blink_spin = QDoubleSpinBox()
        self.blink_spin.setRange(0.1, 10.0)
        self.blink_spin.setDecimals(1)
        self.blink_spin.setSingleStep(0.1)
        self.blink_spin.setSuffix(" Hz")
        self.blink_spin.setToolTip("2 Hz = 250 ms on, 250 ms off")
        form.addRow("Mode", self.mode_combo)
        form.addRow("Color", self.color_button)
        form.addRow("Brightness", self.brightness_spin)
        form.addRow("Duration", self.duration_spin)
        form.addRow("Blink rate", self.blink_spin)
        content.addWidget(editor, 2)
        outer.addLayout(content, 1)

        controls = QHBoxLayout()
        self.start_button = QPushButton("Start sequence")
        self.stop_button = QPushButton("Stop and turn off")
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        outer.addLayout(controls)
        self.status_label = QLabel("Disconnected. You can edit and save presets without the Uno.")
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

        self.refresh_button.clicked.connect(self._refresh_ports)
        self.connect_button.clicked.connect(self._toggle_connection)
        self.step_list.currentRowChanged.connect(self._selected_step)
        self.add_button.clicked.connect(self._add_step)
        self.remove_button.clicked.connect(self._remove_step)
        self.up_button.clicked.connect(lambda: self._move_step(-1))
        self.down_button.clicked.connect(lambda: self._move_step(1))
        self.open_button.clicked.connect(self._open_preset)
        self.save_button.clicked.connect(self._save_preset)
        self.mode_combo.currentIndexChanged.connect(self._editor_changed)
        self.color_button.clicked.connect(self._choose_color)
        self.brightness_spin.valueChanged.connect(self._editor_changed)
        self.duration_spin.valueChanged.connect(self._editor_changed)
        self.blink_spin.valueChanged.connect(self._editor_changed)
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(self._stop)

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(500)
        self.poll_timer.timeout.connect(self.runner.request_status)
        self.poll_timer.start()
        self._refresh_ports()
        self._render_steps(0)
        self._update_buttons()

    def _refresh_ports(self) -> None:
        previous = self.port_combo.currentData()
        ports = QSerialPortInfo.availablePorts()
        preferred = [info for info in ports if info.systemLocation().startswith("/dev/cu.")]
        if preferred:
            ports = preferred
        self.port_combo.clear()
        for info in ports:
            location = info.systemLocation()
            description = info.description()
            label = location + (f" — {description}" if description else "")
            self.port_combo.addItem(label, location)
        if previous:
            index = self.port_combo.findData(previous)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)
        self._update_buttons()

    def _toggle_connection(self) -> None:
        if self.transport.port.isOpen():
            self.transport.close_port()
            self.runner.connection_lost()
            self.status_label.setText("Disconnected. The Uno continues a running sequence if externally powered.")
            self._update_buttons()
            return
        location = self.port_combo.currentData()
        if not location:
            self._show_error("No serial port selected. Connect the Uno and refresh ports.")
            return
        try:
            self.transport.open_port(location)
        except RuntimeError as exc:
            self._show_error(str(exc))
            return
        self.status_label.setText("Connecting… The Uno resets when its serial port opens.")
        self._update_buttons()

    def _connection_changed(self, ready: bool) -> None:
        if ready:
            self.status_label.setText("Connected. Ready to upload a sequence.")
            self.runner.request_status()
        else:
            self.runner.connection_lost()
            self._running = False
            self._uploading = False
            self.status_label.setText("Disconnected. Reconnecting resets the Uno and stops playback.")
        self._update_buttons()

    def _board_reset(self) -> None:
        self.status_label.setText("The Uno reset; playback stopped. Reconnect when ready.")

    def _show_error(self, message: str) -> None:
        self._uploading = False
        self.status_label.setText(message)
        self._update_buttons()

    def _board_status(self, status: BoardStatus) -> None:
        self._running = status.state == "RUNNING"
        if self._running and 0 <= status.index < len(self._active_steps):
            duration = self._active_steps[status.index].duration_ms // 1000
            elapsed = min(status.elapsed_ms // 1000, duration)
            self.status_label.setText(
                f"Running step {status.index + 1}/{status.count}: {elapsed}/{duration} s. "
                "The Uno owns the timing."
            )
        else:
            descriptions = {
                "IDLE": "Connected. No sequence loaded; lights off.",
                "LOADING": "Uploading sequence…",
                "READY": "Connected. Sequence finished or ready; lights off.",
                "MANUAL": "Connected. Manual color active.",
            }
            self.status_label.setText(descriptions.get(status.state, status.state))
        self._update_buttons()

    def _started(self) -> None:
        self._uploading = False
        self._running = True
        self.status_label.setText("Sequence started on the Uno.")
        self._update_buttons()

    def _stopped(self) -> None:
        self._uploading = False
        self._running = False
        self.status_label.setText("Stopped; lights off.")
        self._update_buttons()

    def _start(self) -> None:
        snapshot = list(self.steps)
        try:
            self.runner.start(Sequence(snapshot))
        except (RuntimeError, ValueError) as exc:
            self._show_error(str(exc))
            return
        self._active_steps = snapshot
        self._uploading = True
        self.status_label.setText("Uploading steps to the Uno…")
        self._update_buttons()

    def _stop(self) -> None:
        try:
            self.runner.stop()
        except RuntimeError as exc:
            self._show_error(str(exc))
            return
        self.status_label.setText("Stopping and turning off…")
        self._update_buttons()

    def _render_steps(self, selected: int) -> None:
        self.step_list.blockSignals(True)
        self.step_list.clear()
        for number, step in enumerate(self.steps, start=1):
            self.step_list.addItem(describe(step, number))
        self.step_list.setCurrentRow(min(selected, len(self.steps) - 1))
        self.step_list.blockSignals(False)
        self._selected_step(self.step_list.currentRow())

    def _selected_step(self, row: int) -> None:
        valid = 0 <= row < len(self.steps)
        for widget in (self.mode_combo, self.color_button, self.brightness_spin,
                       self.duration_spin, self.blink_spin):
            widget.setEnabled(valid)
        if not valid:
            self._update_buttons()
            return
        step = self.steps[row]
        self._syncing = True
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(step.mode))
        self._current_color = QColor(step.red, step.green, step.blue)
        self.brightness_spin.setValue(step.brightness)
        self.duration_spin.setValue(step.duration_ms // 1000)
        self.blink_spin.setValue(step.blink_hz)
        self._syncing = False
        self._update_color_button()
        self._update_buttons()

    def _update_color_button(self) -> None:
        self.color_button.setText(self._current_color.name().upper() + "  Choose…")
        self.color_button.setStyleSheet(
            f"background-color: {self._current_color.name()}; "
            f"color: {'black' if self._current_color.lightness() > 127 else 'white'};"
        )
        mode = self.mode_combo.currentData()
        self.color_button.setEnabled(mode != "off")
        self.blink_spin.setEnabled(mode == "blink")

    def _choose_color(self) -> None:
        chosen = QColorDialog.getColor(self._current_color, self, "Choose LED color")
        if chosen.isValid():
            self._current_color = chosen
            self._update_color_button()
            self._editor_changed()

    def _editor_changed(self, *_: object) -> None:
        row = self.step_list.currentRow()
        if self._syncing or row < 0:
            return
        self.steps[row] = replace(
            self.steps[row], mode=self.mode_combo.currentData(),
            red=self._current_color.red(), green=self._current_color.green(),
            blue=self._current_color.blue(), brightness=self.brightness_spin.value(),
            duration_ms=self.duration_spin.value() * 1000,
            blink_hz=self.blink_spin.value(),
        )
        self.step_list.item(row).setText(describe(self.steps[row], row + 1))
        self._update_color_button()

    def _add_step(self) -> None:
        if len(self.steps) >= MAX_STEPS:
            self._show_error(f"The Uno supports at most {MAX_STEPS} steps")
            return
        row = self.step_list.currentRow()
        self.steps.insert(row + 1, Step())
        self._render_steps(row + 1)

    def _remove_step(self) -> None:
        row = self.step_list.currentRow()
        if row < 0 or len(self.steps) <= 1:
            return
        self.steps.pop(row)
        self._render_steps(min(row, len(self.steps) - 1))

    def _move_step(self, offset: int) -> None:
        row = self.step_list.currentRow()
        destination = row + offset
        if row < 0 or not 0 <= destination < len(self.steps):
            return
        self.steps[row], self.steps[destination] = self.steps[destination], self.steps[row]
        self._render_steps(destination)

    def _open_preset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open preset", str(PRESET_DIR), "JSON files (*.json)")
        if not path:
            return
        try:
            sequence = Sequence.load(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot open preset", str(exc))
            return
        self.steps = sequence.steps
        self._render_steps(0)
        self.status_label.setText(f"Opened {Path(path).name}. Press Start to send it to the Uno.")

    def _save_preset(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save preset", str(PRESET_DIR / "my-sequence.json"),
                                              "JSON files (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            Sequence(list(self.steps)).save(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot save preset", str(exc))
            return
        self.status_label.setText(f"Saved {Path(path).name}.")

    def _update_buttons(self) -> None:
        is_open = self.transport.port.isOpen()
        self.connect_button.setText("Disconnect" if is_open else "Connect")
        self.connect_button.setEnabled(is_open or self.port_combo.currentData() is not None)
        self.start_button.setEnabled(self.transport.ready and not self.runner.busy and
                                     not self._running and not self._uploading)
        self.stop_button.setEnabled(self.transport.ready)
        row = self.step_list.currentRow()
        self.remove_button.setEnabled(len(self.steps) > 1 and row >= 0)
        self.add_button.setEnabled(len(self.steps) < MAX_STEPS)
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(0 <= row < len(self.steps) - 1)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override.
        self.transport.close_port()  # Closing the GUI does not send STOP.
        super().closeEvent(event)


def main() -> None:
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()
