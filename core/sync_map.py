"""
The record on a Pacemaker of which Editor cases are on it: <device root>/.Pacemaker/rb_sync_map.json

    {"version": 2, "library_id": 123, "cases": {"<editor case id>": <device case id>}}

Editor case IDs only mean something to the library that issued them, so the record also
names that library. A Pacemaker whose record names another library is synced to a
different computer. Older records are a bare {case id: device id} dict with no owner.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from core.device_finder import device_root

OK, MISSING, CORRUPT = "ok", "missing", "corrupt"


@dataclass
class SyncRecord:
    cases: dict = field(default_factory=dict)   # {str(editor_case_id): device_case_id}
    library_id: Optional[int] = None            # None: no owner recorded yet
    status: str = OK

    def editor_case_ids(self) -> set[int]:
        return {int(key) for key in self.cases}


def path_for(device_db: str) -> str:
    return os.path.join(device_root(device_db), ".Pacemaker", "rb_sync_map.json")


def _validated_cases(cases) -> dict:
    """Raises ValueError or TypeError unless every entry is a pair of case IDs."""
    if not isinstance(cases, dict):
        raise ValueError("cases is not a dict")
    for key, value in cases.items():
        int(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"bad device case id: {value!r}")
    return cases


def read(device_db: str) -> SyncRecord:
    try:
        with open(path_for(device_db), "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        if "cases" in data:
            library_id = data.get("library_id")
            if library_id is not None and not isinstance(library_id, int):
                raise ValueError("bad library id")
            return SyncRecord(_validated_cases(data["cases"]), library_id)
        return SyncRecord(_validated_cases(data))
    except FileNotFoundError:
        return SyncRecord(status=MISSING)
    except (OSError, ValueError, TypeError):
        return SyncRecord(status=CORRUPT)


def write(device_db: str, cases: dict, library_id: Optional[int]) -> None:
    """Replace the record in one step, so an interruption can't leave it half written."""
    path = path_for(device_db)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump({"version": 2, "library_id": library_id, "cases": cases}, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def set_aside_corrupt(device_db: str) -> None:
    """Keep a damaged record for diagnosis, under a name nothing reads."""
    path = path_for(device_db)
    try:
        os.replace(path, path + ".corrupt")
    except OSError:
        pass


def rebuild_by_name(editor_cases: list[dict], device_cases: list[dict]) -> dict:
    """Pair Editor and device cases that have the same name and aren't ambiguous."""
    def unique_by_name(cases: list[dict]) -> dict:
        by_name: dict = {}
        for case in cases:
            by_name.setdefault((case["name"] or "").casefold(), []).append(case["case_id"])
        return {name: ids[0] for name, ids in by_name.items() if len(ids) == 1}

    device_ids = unique_by_name(device_cases)
    return {
        str(editor_id): device_ids[name]
        for name, editor_id in unique_by_name(editor_cases).items()
        if name in device_ids
    }
