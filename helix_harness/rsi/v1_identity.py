"""Preserve published v1 IDs for the exact reviewed package relocation."""

import json
from pathlib import Path


def published_id(component: str, physical: str) -> str:
    # Read from the installed evaluator, never from a candidate release or workspace.
    manifest = Path(__file__).with_name("relocation-v1.json")
    pin = json.loads(manifest.read_bytes())[component]
    if physical == pin["relocated"]:
        return pin["published"]
    return physical
