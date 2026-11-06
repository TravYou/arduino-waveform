from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lighting_controller.model import MAX_STEPS, Sequence, Step
from lighting_controller.protocol import (
    Reply, load_commands, parse_reply, parse_status, set_command,
)
from lighting_controller.runner import UnoRunner


class FakeTransport:
    def __init__(self) -> None:
        self.ready = True
        self.pending = None
        self.commands: list[str] = []

    @property
    def busy(self) -> bool:
        return self.pending is not None

    def send(self, command, callback) -> None:
        if self.busy:
            raise RuntimeError("A command is already pending")
        self.commands.append(command)
        self.pending = (command, callback)

    def respond(self, response: str | None = None) -> None:
        command, callback = self.pending
        self.pending = None
        callback(parse_reply(response or f"OK {command.split()[0]}"))


class ModelTests(unittest.TestCase):
    def test_example_presets_round_trip(self) -> None:
        folder = Path(__file__).resolve().parents[1] / "presets"
        for path in folder.glob("*.json"):
            with self.subTest(path=path):
                sequence = Sequence.load(path)
                with tempfile.TemporaryDirectory() as directory:
                    saved = Path(directory) / "saved.json"
                    sequence.save(saved)
                    self.assertEqual(Sequence.load(saved), sequence)

    def test_rejects_invalid_steps_and_preset_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "Brightness"):
            Sequence([Step(brightness=101)]).validate()
        with self.assertRaisesRegex(ValueError, "1 to 32"):
            Sequence([Step()] * (MAX_STEPS + 1)).validate()
        with self.assertRaisesRegex(ValueError, "Unsupported preset version"):
            Sequence.from_dict({"schema_version": 99, "steps": []})
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            Sequence.from_dict({"schema_version": 1, "steps": [{"mode": "off"}]})

    def test_blink_2hz_has_500ms_period(self) -> None:
        step = Step(mode="blink", red=255, green=0, blue=0,
                    duration_ms=60_000, blink_hz=2.0)
        self.assertEqual(step.period_ms, 500)
        self.assertEqual(load_commands(Sequence([step]))[1],
                         "STEP 0 B 255 0 0 64 60000 500")


class ProtocolTests(unittest.TestCase):
    def test_replies_and_status(self) -> None:
        reply = parse_reply("OK STATUS RUNNING 1 1250 2")
        self.assertEqual(parse_status(reply).elapsed_ms, 1250)
        self.assertEqual(parse_status(reply).index, 1)
        self.assertEqual(parse_reply("ERR VALUE"), Reply("", (), "VALUE"))
        with self.assertRaises(ValueError):
            parse_reply("noise")

    def test_manual_set_validation(self) -> None:
        self.assertEqual(set_command(255, 0, 5, 128), "SET 255 0 5 128")
        with self.assertRaises(ValueError):
            set_command(256, 0, 0, 128)


class RunnerTests(unittest.TestCase):
    def test_uploads_one_command_at_a_time_and_reports_status(self) -> None:
        transport = FakeTransport()
        runner = UnoRunner(transport)
        statuses = []
        started = []
        runner.on_status = statuses.append
        runner.on_started = lambda: started.append(True)
        runner.start(Sequence([Step(duration_ms=1000), Step(mode="off", duration_ms=1000)]))
        self.assertEqual(transport.commands, ["BEGIN 2"])
        while transport.pending and transport.pending[0] != "STATUS":
            transport.respond()
        self.assertEqual([c.split()[0] for c in transport.commands],
                         ["BEGIN", "STEP", "STEP", "COMMIT", "START", "STATUS"])
        transport.respond("OK STATUS RUNNING 0 250 2")
        self.assertEqual(started, [True])
        self.assertEqual(statuses[0].state, "RUNNING")

    def test_stop_during_upload_preempts_remaining_steps(self) -> None:
        transport = FakeTransport()
        runner = UnoRunner(transport)
        runner.start(Sequence([Step(), Step()]))
        runner.stop()
        transport.respond("OK BEGIN")
        self.assertEqual(transport.commands, ["BEGIN 2", "STOP"])
        transport.respond("OK STOP")
        self.assertEqual(transport.commands[-1], "STATUS")

    def test_error_aborts_upload(self) -> None:
        transport = FakeTransport()
        runner = UnoRunner(transport)
        errors = []
        runner.on_error = errors.append
        runner.start(Sequence([Step()]))
        transport.respond("ERR COUNT")
        self.assertFalse(runner.busy)
        self.assertEqual(transport.commands, ["BEGIN 1"])
        self.assertIn("COUNT", errors[0])


if __name__ == "__main__":
    unittest.main()
