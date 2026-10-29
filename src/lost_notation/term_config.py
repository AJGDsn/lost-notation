"""Small, dependency-free reader for the project's simple vocabulary YAML."""
from __future__ import annotations

from pathlib import Path


def load_term_group(path: Path, group: str) -> list[str]:
    """Read a top-level list group from the deliberately simple term config.

    The project does not need a general YAML parser for this human-editable
    file: only ``group:`` headings and ``- term`` entries are supported.  A
    malformed entry is an explicit error rather than silently changing search
    scope.
    """
    active: str | None = None
    result: list[str] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")) and line.endswith(":"):
            active = line[:-1].strip()
            continue
        if line.lstrip().startswith("- "):
            if active is None:
                raise ValueError(f"{path}:{number}: term appears before a group heading")
            if active == group:
                value = line.lstrip()[2:].strip()
                if not value:
                    raise ValueError(f"{path}:{number}: blank term")
                result.append(value)
            continue
        raise ValueError(f"{path}:{number}: expected a group heading or '- term'")
    if not result:
        raise ValueError(f"{path}: no terms found for group '{group}'")
    return list(dict.fromkeys(result))
