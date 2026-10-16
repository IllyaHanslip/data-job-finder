"""Reading and writing the small JSON files the program keeps between runs."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    """Return the parsed contents of a JSON file, or None if it is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically, so an interrupted run never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_id, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(handle_id, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        for attempt in range(3):
            try:
                os.replace(temp_name, path)
                return
            except PermissionError:
                # Windows briefly locks files while antivirus or search indexing reads them.
                if attempt == 2:
                    raise
                time.sleep(0.2)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
