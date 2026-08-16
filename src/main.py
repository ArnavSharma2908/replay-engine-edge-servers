"""
Production Replay System — Main Orchestrator

Wires Components 1-4. Each component owns its own file I/O; main.py only
invokes them in sequence and passes the shared output directory as context.

Usage:
    python -m src.main <input_file> [<input_file> ...] <output_dir>
    python -m src.main <output_dir>     # auto-discovers logs in input/
    python -m src.main                  # input/ → output/
"""

import sys
from pathlib import Path

from .components import get_schemas, ingest, normalize, correlate_and_shard, build_traffic_profile

_REJECT_THRESHOLD_PCT = 0.5


def _discover_inputs(input_dir: Path) -> list:
    """Recursively return all non-hidden files under input_dir."""
    return sorted(p for p in input_dir.rglob("*") if p.is_file() and not p.name.startswith("."))


def process_log_files(input_files: list, output_dir: str) -> bool:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    schemas = get_schemas()

    summary = ingest([str(f) for f in input_files], schemas, str(out))
    print(f"Ingested — R: {summary['accepted_r']}  F: {summary['accepted_f']}  "
          f"Rejected: {summary['rejected']}  Rate: {summary['reject_rate_pct']:.4f}%")

    if summary["reject_rate_pct"] > _REJECT_THRESHOLD_PCT:
        print(f"[FATAL] Reject rate {summary['reject_rate_pct']:.4f}% exceeds threshold — aborting.")
        return False

    normalize(str(out))
    build_traffic_profile(str(out))
    correlate_and_shard(str(out))

    print(f"[OK] Done — outputs in: {out}")
    return True


if __name__ == "__main__":
    files = _discover_inputs(Path("input"))
    out_dir = "output"

    if not files:
        print("No input files found. Place log files in input/ or pass them as arguments.")
        sys.exit(1)

    sys.exit(0 if process_log_files(files, out_dir) else 1)