# Uno Lighting Controller

Compose a sequence on your computer, then send it to an Arduino Uno R3. The Uno
runs the sequence without further commands from the computer. Version 1 controls
all 20 sections of the 1 m, 12 V BTF-LIGHTING WS2811 COB strip together.

## Set up

1. Clone or download this repository and open its folder in VS Code.
2. In Arduino IDE, install **Adafruit NeoPixel** from Library Manager. Open
   `firmware/UnoLightingController/UnoLightingController.ino`. Select
   **Arduino Uno** and its USB port, then upload. Close Arduino Serial Monitor
   before connecting the desktop app; only one program can own the port.
3. Install Python 3.10 or newer, then create a virtual environment and launch
   the app:

   macOS/Linux:

   ```sh
   python3 -m venv .venv
   .venv/bin/python -m pip install -e .
   .venv/bin/python -m lighting_controller
   ```

   Windows PowerShell:

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e .
   .\.venv\Scripts\python.exe -m lighting_controller
   ```

   Select the virtual environment's Python as VS Code's interpreter. If
   `python3` or `py -3` is not found, use the path to your Python 3.10+
   installation.

4. In the app, select the Uno's serial port (such as `/dev/cu.usbmodem…` on
   macOS or `COM3` on Windows) and click **Connect**.
   Opening the port resets an Uno R3; wait for the connection to complete.
   Edit steps or open one of the included presets, then click **Start sequence**.
   **Stop and turn off** cancels playback while connected.

You can edit and save presets with no board attached. The app never
automatically reconnects. Reconnecting during playback resets the Uno and
stops the sequence. Closing the app or unplugging USB does not send a Stop
command; the Uno can continue only if it has independent power. If the Uno
loses power or resets, it starts with the strip off and does not resume a
previous sequence.

## Wiring and power

Disconnect both power sources before wiring. Use the **input** end of the
strip, identified by its data direction arrow and DI/DIN marking; confirm the
printed labels instead of relying on wire colors.

```text
Computer USB ────────────────────────── Uno USB (data)
12 V supply + ───────────────┬──────── strip +12V
                             └──────── Uno barrel jack center + (suitable splitter)
12 V supply − ───────────────┬──────── strip GND
                             └──────── Uno barrel jack outer − / GND
Uno D6 ── 330–470 Ω ─────────────────── strip DI/DIN
```

The Uno's [recommended barrel-jack input range is 7–12 V](https://store.arduino.cc/products/arduino-uno-rev3).
Use a regulated 12 V supply rated at least 2 A and an appropriate splitter or
two supplies with shared ground. **Never put 12 V on the Uno's 5V or data pin,**
and do not route strip power through the Uno. A 500–1000 µF capacitor rated
at least 16 V across strip +12V/GND near its input is recommended. USB alone
will not keep the Uno running after the computer cable is removed.

The firmware uses `NEO_RGB + NEO_KHZ800`: on this strip, the earlier `NEO_GRB`
setting displayed green when sent red. If colors are still swapped, test each
primary color and change the `NEO_*` color order to match. If data does not
work despite correct power, ground, and DI wiring, test `NEO_KHZ400`.

## Step behavior

- **Solid:** one color at the chosen brightness for the duration.
- **Blink:** the color alternates on and off at the chosen full-cycle rate,
  with a 50% duty cycle. At 2 Hz, that is 250 ms on and 250 ms off.
- **Off:** dark for the duration.

The first step starts on **Start**; transitions use scheduled boundaries and
the strip turns off after the final step. Durations are whole seconds from 1
to 86,400; blink rates are 0.1–10 Hz. Up to 32 steps fit on the Uno. The GUI
may edit a preset while playback runs; changes affect only the next Start.

## Code structure

- `model.py` owns the sequence and JSON format, with no Qt or serial code.
- `protocol.py` encodes commands and parses replies.
- `runner.py` defines the `SequenceRunner` interface and `UnoRunner`. A future
  `MacRunner` can use the same model and send immediate `SET`/`OFF` commands.
- `serial_transport.py` owns QtSerialPort and command timeouts.
- `app.py` owns only the widgets and user interaction.

The firmware's ASCII protocol runs at **115200 baud**, one newline-terminated
command and one reply at a time:

```text
HELLO                         -> OK HELLO 1 32
BEGIN count                   -> OK BEGIN
STEP index S|B|O R G B brightness duration_ms period_ms -> OK STEP
COMMIT                        -> OK COMMIT
START                         -> OK START
STOP                          -> OK STOP
STATUS                        -> OK STATUS state index elapsed_ms count
SET R G B brightness          -> OK SET
OFF                           -> OK OFF
```

`brightness` on the wire is 0–255; the GUI displays 0–100%. For a non-blink
step, `period_ms` is 0. Invalid commands return `ERR code`. `BEGIN` clears the
previous program and turns the strip off; an incomplete upload cannot start.
`SET` and `OFF` cancel playback. They support future Mac-run timing, but that
mode is intentionally absent from the version 1 UI.

The GUI's 100% maps to 255 with no further firmware brightness limit. A color
of `255,255,255` at 100% requests full output from all three RGB channels;
100% of a single color drives only that channel. Actual light output depends
on the strip and its 12 V power supply.

## Checks

Run pure Python tests with:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Compile firmware in Arduino IDE for Uno. Hardware checks remain necessary:
run all three included presets, confirm a 2 Hz blink and final off, test Stop,
and verify playback after closing the app or unplugging USB while external
power remains connected.
