"""Synthetic external configuration for tests only; never real business data."""

import json
from pathlib import Path

from app.services.printing_config import PROJECT_ROOT


def demo_config():
    return json.loads((PROJECT_ROOT / "config/pricing.demo.json").read_text())


def write_external_config(directory, data=None):
    data = demo_config() if data is None else data
    data["configuration_kind"] = "private"
    path = Path(directory) / "synthetic-commercial.private.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path
