from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _history_file(workspace: Path) -> Path:
    return workspace / "history" / "runs.jsonl"


def append_history_record(workspace: Path, record: dict[str, Any]) -> Path:
    history_file = _history_file(workspace)
    history_file.parent.mkdir(parents=True, exist_ok=True)
    with history_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    return history_file


def load_history_records(workspace: Path, limit: int = 50) -> list[dict[str, Any]]:
    history_file = _history_file(workspace)
    if not history_file.exists():
        return []

    rows: list[dict[str, Any]] = []
    with history_file.open("r", encoding="utf-8") as file:
        for line in file:
            raw = line.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)

    rows.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
    return rows[: max(0, limit)]
