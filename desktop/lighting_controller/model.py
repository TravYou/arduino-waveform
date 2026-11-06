"""Hardware-independent lighting sequence and preset format."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Literal


SCHEMA_VERSION = 1
MAX_STEPS = 32
MIN_DURATION_MS = 1_000
MAX_DURATION_MS = 86_400_000
Mode = Literal["solid", "blink", "off"]


@dataclass(frozen=True)
class Step:
    mode: Mode = "solid"
    red: int = 0
    green: int = 255
    blue: int = 0
    brightness: int = 25  # Percent, 0..100.
    duration_ms: int = 60_000
    blink_hz: float = 2.0

    def validate(self) -> None:
        if self.mode not in ("solid", "blink", "off"):
            raise ValueError("Mode must be solid, blink, or off")
        for name in ("red", "green", "blue"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= 255:
                raise ValueError(f"{name} must be an integer from 0 to 255")
        if type(self.brightness) is not int or not 0 <= self.brightness <= 100:
            raise ValueError("Brightness must be an integer from 0 to 100%")
        if (type(self.duration_ms) is not int or
                not MIN_DURATION_MS <= self.duration_ms <= MAX_DURATION_MS or
                self.duration_ms % 1_000):
            raise ValueError("Duration must be a whole number of seconds, from 1 to 86400")
        if type(self.blink_hz) not in (float, int) or not 0.1 <= self.blink_hz <= 10.0:
            raise ValueError("Blink frequency must be between 0.1 and 10 Hz")

    @property
    def period_ms(self) -> int:
        self.validate()
        return round(1_000 / self.blink_hz) if self.mode == "blink" else 0

    @property
    def brightness_byte(self) -> int:
        return round(self.brightness * 255 / 100)


@dataclass
class Sequence:
    steps: list[Step] = field(default_factory=list)

    def validate(self) -> None:
        if not 1 <= len(self.steps) <= MAX_STEPS:
            raise ValueError(f"A sequence needs 1 to {MAX_STEPS} steps")
        for number, step in enumerate(self.steps, start=1):
            try:
                step.validate()
            except ValueError as exc:
                raise ValueError(f"Step {number}: {exc}") from exc

    def to_dict(self) -> dict:
        self.validate()
        return {"schema_version": SCHEMA_VERSION,
                "steps": [asdict(step) for step in self.steps]}

    @classmethod
    def from_dict(cls, data: dict) -> "Sequence":
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Unsupported preset version (expected {SCHEMA_VERSION})")
        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= MAX_STEPS:
            raise ValueError(f"A preset needs 1 to {MAX_STEPS} steps")
        fields = set(Step.__dataclass_fields__)
        steps = []
        for number, item in enumerate(raw_steps, start=1):
            if not isinstance(item, dict) or set(item) != fields:
                raise ValueError(f"Step {number} has missing or unknown fields")
            steps.append(Step(**item))
        sequence = cls(steps)
        sequence.validate()
        return sequence

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Sequence":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc
        return cls.from_dict(data)
