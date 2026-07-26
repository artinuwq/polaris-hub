from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_ENV_LINE_RE = re.compile(r"^(?P<key>[A-Z_][A-Z0-9_]*)=(?P<value>.*)$")


@dataclass(frozen=True)
class EnvUpdateResult:
    key: str
    previous_value: str | None
    value: str
    changed: bool


def read_env_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_LINE_RE.match(line)
        if match and match.group("key") == key:
            return match.group("value")
    return None


def write_env_value(path: Path, key: str, value: str) -> EnvUpdateResult:
    if "\n" in value or "\r" in value:
        raise ValueError("Значение не может содержать перенос строки")

    previous_value = read_env_value(path, key)
    if previous_value == value:
        return EnvUpdateResult(key=key, previous_value=previous_value, value=value, changed=False)

    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updated_lines: list[str] = []
    replaced = False

    for line in lines:
        match = _ENV_LINE_RE.match(line)
        if match and match.group("key") == key:
            if not replaced:
                updated_lines.append(f"{key}={value}")
                replaced = True
            continue
        updated_lines.append(line)

    if not replaced:
        updated_lines.append(f"{key}={value}")

    text = "\n".join(updated_lines)
    if updated_lines:
        text += "\n"
    path.write_text(text, encoding="utf-8")
    return EnvUpdateResult(key=key, previous_value=previous_value, value=value, changed=True)
