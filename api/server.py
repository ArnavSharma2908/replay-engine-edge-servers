"""Local, read-only API for dashboard data.

Run with:
    python -m api.server

The server reads existing pipeline output and never writes to shard files.
"""

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlparse

from .replay_status import read_status


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def _load_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as file_handle:
            return json.load(file_handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def build_shards_response(shard_dir: Path) -> Dict[str, object]:
    """Build the exact /api/shards response from generated shard files."""
    metadata = _load_json(shard_dir / "shard_metadata.json", {})
    shard_paths = sorted(shard_dir.glob("shard_*.jsonl"))
    shards: List[Dict[str, object]] = []

    for shard_path in shard_paths:
        connection_count = 0
        metadata_path = shard_path.with_name(
            f"{shard_path.stem}_metadata.json"
        )
        shard_metadata = _load_json(metadata_path, {})
        if isinstance(shard_metadata.get("connections"), list):
            connection_count = len(shard_metadata["connections"])
        else:
            with shard_path.open("r", encoding="utf-8") as shard_file:
                connection_count = sum(1 for line in shard_file if line.strip())

        shard_id = shard_path.stem[len("shard_"):]
        shards.append({
            "id": int(shard_id) if shard_id.isdigit() else shard_id,
            "connection_count": connection_count,
            "status": "ready",
        })

    shard_count = int(metadata.get("shard_count", len(shards)))
    # Greedy interval partitioning emits one shard per simultaneously active
    # connection, so shard_count is the recorded peak concurrency.
    peak_concurrency = int(metadata.get("peak_concurrency", shard_count))
    return {
        "shard_count": shard_count,
        "peak_concurrency": peak_concurrency,
        "shards": shards,
    }


class ReplayApiHandler(BaseHTTPRequestHandler):
    """HTTP handler exposing only the two dashboard data endpoints."""

    server_version = "ReplayAPI/1.0"

    def _json_response(self, payload: Dict[str, object], status: int = 200) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(encoded)

    def do_OPTIONS(self) -> None:
        self._json_response({}, 204)

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/shards":
            self._json_response(build_shards_response(self.server.shard_dir))
            return
        if route == "/api/replay/status":
            status = read_status(self.server.status_file)
            self._json_response({
                "running": status["running"],
                "requests_replayed": status["requests_replayed"],
                "requests_total": status["requests_total"],
            })
            return
        self._json_response({"error": "Not found"}, 404)

    def log_message(self, format_string: str, *args) -> None:
        return


def create_server(host: str, port: int, shard_dir: Path,
                  status_file: Path) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), ReplayApiHandler)
    server.shard_dir = shard_dir
    server.status_file = status_file
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve replay dashboard API")
    parser.add_argument("--host", default=os.getenv("API_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int,
                        default=int(os.getenv("API_PORT", DEFAULT_PORT)))
    parser.add_argument("--shard-dir", type=Path,
                        default=Path(os.getenv("REPLAY_SHARD_DIR", "output/shards")))
    parser.add_argument("--status-file", type=Path,
                        default=Path(os.getenv("REPLAY_STATUS_FILE",
                                               "output/replay_status.json")))
    args = parser.parse_args()

    server = create_server(args.host, args.port, args.shard_dir, args.status_file)
    print(f"Replay API listening at http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
