"""Component 2 — Log Ingest and Parser: reads log files, classifies lines, writes parsed JSONL."""

import gzip
import json
import os
import warnings
from pathlib import Path

_KNOWN_METHODS = {"GET", "HEAD", "POST", "PATCH"}


def _is_gzip(path: Path) -> bool:
    """Detect gzip by magic bytes — Trident files have no .gz extension."""
    try:
        with open(path, "rb") as fh:
            return fh.read(2) == b"\x1f\x8b"
    except OSError:
        return False


def _open_log(path: Path):
    if _is_gzip(path):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def _parse_line(raw: str, file_name: str, line_no: int, schemas: dict, max_pos: dict) -> dict:
    """Parse one log line. Returns accepted/rejected/ignored result dict."""
    tokens = raw.strip().split(" ")

    # Scan left-to-right for the type token ('r' or 'f').
    type_idx = next((i for i, t in enumerate(tokens) if t in ("r", "f")), None)
    if type_idx is None:
        return {"status": "ignored", "reason": "no_type_token"}

    log_type = tokens[type_idx]
    required = type_idx + max_pos[log_type]
    if len(tokens) < required:
        return {"status": "rejected", "bucket": "malformed_line",
                "reason": f"need {required} tokens, got {len(tokens)}"}

    data = {"source_file": file_name, "source_line_no": line_no, "raw_line": raw.strip()}
    for pos, (name, cast) in schemas[log_type].items():
        tok = tokens[type_idx + pos - 1]
        try:
            data[name] = cast(tok)
        except (ValueError, TypeError):
            return {"status": "rejected", "bucket": "type_validation_error",
                    "reason": f"cast failed for '{name}' (value: '{tok}')"}

    if data.get("event_time") is None:
        return {"status": "rejected", "bucket": "invalid_timestamp", "reason": "event_time absent"}

    # Status must be 0 (special Trident value) or a valid 3-digit HTTP code (100-999).
    st = data.get("status")
    if not isinstance(st, int) or (st != 0 and not (100 <= st <= 999)):
        return {"status": "rejected", "bucket": "missing_required_field",
                "reason": f"invalid status '{st}'"}

    method = data.get("method", "")
    if method and method not in _KNOWN_METHODS:
        warnings.warn(f"{file_name}:{line_no}: unexpected method '{method}'", stacklevel=3)

    return {"status": "accepted", "type": f"{log_type}_event", "data": data}


def ingest(input_files: list, schemas: dict, output_dir: str) -> dict:
    """
    Stream all input log files, parse each line, and write results to output_dir.

    Writes: parsed_r.jsonl, parsed_f.jsonl, ingest_summary.json, reject_report.json
    Returns the ingest_summary dict so main.py can check the reject rate.
    """
    r_schema, f_schema = schemas["r"], schemas["f"]
    field_schemas = {"r": r_schema, "f": f_schema}
    max_pos = {"r": max(r_schema), "f": max(f_schema)}

    total = accepted_r = accepted_f = ignored = rejected = 0
    rejects = {}

    parsed_r = os.path.join(output_dir, "parsed_r.jsonl")
    parsed_f = os.path.join(output_dir, "parsed_f.jsonl")

    with open(parsed_r, "w", encoding="utf-8") as r_out, \
         open(parsed_f, "w", encoding="utf-8") as f_out:

        for raw_path in input_files:
            path = Path(raw_path)
            try:
                with _open_log(path) as fh:
                    for line_no, raw in enumerate(fh, 1):
                        total += 1
                        raw = raw.rstrip("\n")
                        res = _parse_line(raw, str(path), line_no, field_schemas, max_pos)

                        if res["status"] == "accepted":
                            line = json.dumps({"status": "accepted", "type": res["type"], "data": res["data"]})
                            if res["type"] == "r_event":
                                accepted_r += 1
                                r_out.write(line + "\n")
                            else:
                                accepted_f += 1
                                f_out.write(line + "\n")
                        elif res["status"] == "rejected":
                            rejected += 1
                            bucket = res.get("bucket", "unknown")
                            rejects.setdefault(bucket, [])
                            if len(rejects[bucket]) < 20:
                                rejects[bucket].append({
                                    "file": str(path), "line_no": line_no,
                                    "reason": res.get("reason", ""), "raw_line": raw[:200],
                                })
                        else:
                            ignored += 1
            except FileNotFoundError:
                warnings.warn(f"file not found: {raw_path}", stacklevel=2)
            except OSError as exc:
                warnings.warn(f"cannot read {raw_path}: {exc}", stacklevel=2)

    rate = round(rejected / total * 100, 4) if total else 0.0
    summary = {"total_lines": total, "accepted_r": accepted_r, "accepted_f": accepted_f,
               "ignored": ignored, "rejected": rejected, "reject_rate_pct": rate}

    with open(os.path.join(output_dir, "ingest_summary.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    with open(os.path.join(output_dir, "reject_report.json"), "w") as fh:
        json.dump(rejects, fh, indent=2)

    return summary
