"""CI smoke test: import main.py and every module in core/ and ui/, reporting all failures."""

from __future__ import annotations

import importlib
import pkgutil
import sys
import traceback

failures = 0
names = ["main"] + [
    f"{package}.{module.name}"
    for package in ("core", "ui")
    for module in pkgutil.iter_modules([package])
]
for name in names:
    try:
        importlib.import_module(name)
        print(f"ok    {name}")
    except Exception:
        failures += 1
        print(f"FAIL  {name}")
        traceback.print_exc()

sys.exit(1 if failures else 0)
