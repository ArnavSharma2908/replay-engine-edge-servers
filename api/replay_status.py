"""Small, file-backed replay status tracker shared by the orchestrator and API."""

import json
import os
from pathlib import Path
from typing import Dict


def count_requests(shard_dir: Path) -> int:
    """Count replay requests from the shard JSONL output."""
    total = 0
    for shard_path in sorted(shard_dir.glob("shard_*.jsonl")):
        with shard_path.open("r", encoding="utf-8") as shard_file:
            for line in shard_file:
                if not line.strip():
                    continue
                try:
                    connection = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total += sum(
                    len(batch.get("requests", []))
                    for batch in connection.get("request_batches", [])
                )
    return total


def write_status(status_file: Path, *, running: bool, requests_replayed: int,
                 requests_total: int, pid: int = 0) -> None:
    """Atomically publish the current replay state for readers."""
    status_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "running": running,
        "requests_replayed": requests_replayed,
        "requests_total": requests_total,
        "pid": pid,
    }
    temporary = status_file.with_suffix(status_file.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as status_handle:
        json.dump(payload, status_handle)
    os.replace(temporary, status_file)


def read_status(status_file: Path) -> Dict[str, object]:
    """Read status, returning a safe completed/empty state when absent."""
    try:
        with status_file.open("r", encoding="utf-8") as status_handle:
            payload = json.load(status_handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"running": False, "requests_replayed": 0, "requests_total": 0}

    running = bool(payload.get("running", False))
    pid = int(payload.get("pid", 0) or 0)
    if running and pid:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            running = False
        except PermissionError:
            pass
        except OSError:
            running = False

    return {
        "running": running,
        "requests_replayed": int(payload.get("requests_replayed", 0)),
        "requests_total": int(payload.get("requests_total", 0)),
    }
