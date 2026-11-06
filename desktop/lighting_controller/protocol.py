"""Version 1 ASCII protocol shared by the desktop runner and Uno sketch."""

from __future__ import annotations

from dataclasses import dataclass

from .model import Sequence, Step


def step_command(index: int, step: Step) -> str:
    step.validate()
    mode = {"solid": "S", "blink": "B", "off": "O"}[step.mode]
    return (f"STEP {index} {mode} {step.red} {step.green} {step.blue} "
            f"{step.brightness_byte} {step.duration_ms} {step.period_ms}")


def load_commands(sequence: Sequence) -> list[str]:
    sequence.validate()
    return ([f"BEGIN {len(sequence.steps)}"] +
            [step_command(i, step) for i, step in enumerate(sequence.steps)] +
            ["COMMIT", "START"])


def set_command(red: int, green: int, blue: int, brightness: int) -> str:
    for value in (red, green, blue, brightness):
        if type(value) is not int or not 0 <= value <= 255:
            raise ValueError("SET values must be integers from 0 to 255")
    return f"SET {red} {green} {blue} {brightness}"


@dataclass(frozen=True)
class Reply:
    command: str
    fields: tuple[str, ...]
    error: str | None = None


def parse_reply(line: str) -> Reply:
    parts = line.strip().split()
    if len(parts) >= 2 and parts[0] == "OK":
        return Reply(parts[1], tuple(parts[2:]))
    if len(parts) == 2 and parts[0] == "ERR":
        return Reply("", (), parts[1])
    raise ValueError(f"Unexpected board response: {line!r}")


@dataclass(frozen=True)
class BoardStatus:
    state: str
    index: int
    elapsed_ms: int
    count: int


def parse_status(reply: Reply) -> BoardStatus:
    if reply.command != "STATUS" or reply.error or len(reply.fields) != 4:
        raise ValueError("Malformed STATUS response")
    state, index, elapsed, count = reply.fields
    if state not in ("IDLE", "LOADING", "READY", "RUNNING", "MANUAL"):
        raise ValueError("Unknown board state")
    try:
        result = BoardStatus(state, int(index), int(elapsed), int(count))
    except ValueError as exc:
        raise ValueError("Malformed STATUS numbers") from exc
    if (not -1 <= result.index < 32 or result.elapsed_ms < 0 or
            not 0 <= result.count <= 32):
        raise ValueError("STATUS numbers out of range")
    return result
