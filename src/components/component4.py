"""Component 4 — Correlation Engine: joins r/f records, groups by connection, writes shards."""

import glob
import json
import os
from collections import defaultdict


def _compute_route_intent(cache_class: str, f_object_status: str) -> str:
    """Derive route_intent from the final cache_path_class.

    Priority:
      RAM / Disk  → edge_intended   (cache hit at edge; no forward leg required)
      Peer        → peer_intended   (forward leg went to an ICP/RDC peer)
      Parent      → parent_intended (forward leg went to a parent node)
      Origin      → origin_intended (forward leg went to origin)
      unknown     → fall back to f18 object_status flags for best-effort classification
    """
    if cache_class in ("RAM", "Disk"):
        return "edge_intended"
    if cache_class == "Peer":
        return "peer_intended"
    if cache_class == "Parent":
        return "parent_intended"
    if cache_class == "Origin":
        return "origin_intended"
    # unknown: use f18 object_status as fallback
    s = f_object_status.lower()
    if "g" in s:
        return "peer_intended"
    if "p" in s:
        return "parent_intended"
    return "origin_intended"


def _make_txn(r, f, confidence: str) -> dict:
    cache_class = r.get("cache_path_class", "unknown")
    if f and "o" in str(f.get("object_status", "")):
        cache_class = "Origin"
    f_obj_status = str(f.get("object_status", "")) if f else ""
    route = _compute_route_intent(cache_class, f_obj_status)
    return {"request_id": r.get("request_id", ""), "r_record": r, "f_record": f,
            "cache_path_class": cache_class, "route_intent": route, "confidence_class": confidence}


def _build_f_index(normalized_f_path: str) -> dict:
    index = defaultdict(list)
    with open(normalized_f_path, "r", encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("request_id_usable"):
                index[rec["request_id"]].append(rec)
    return index


def _correlate(normalized_r_path: str, f_index: dict) -> tuple:
    txns, quality = [], {"strong_primary": 0, "multiple_f": 0, "unmatched_r": 0}
    with open(normalized_r_path, "r", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if not r.get("request_id_usable"):
                quality["unmatched_r"] += 1
                if r.get("cache_path_class", "unknown") in ("RAM", "Disk"):
                    txns.append(_make_txn(r, None, "unmatched_r"))
                continue
            candidates = f_index.get(r["request_id"], [])
            if len(candidates) == 1:
                quality["strong_primary"] += 1
                txns.append(_make_txn(r, candidates[0], "strong_primary"))
            elif len(candidates) > 1:
                quality["multiple_f"] += 1
                txns.append(_make_txn(r, candidates[0], "multiple_f"))
            else:
                quality["unmatched_r"] += 1
                if r.get("cache_path_class", "unknown") in ("RAM", "Disk"):
                    txns.append(_make_txn(r, None, "unmatched_r"))
    return txns, quality


def _write_shards(txns: list, output_dir: str, quality: dict,
                  h2_threshold_ms: float = 5.0, max_gap_ms: float = 5000.0) -> None:
    # Always write quality report, even when there is nothing to shard.
    def _write_quality():
        with open(os.path.join(output_dir, "correlation_quality_report.json"), "w") as fh:
            json.dump(quality, fh, indent=2)

    if not txns:
        _write_quality()
        return

    conn_map = defaultdict(list)
    for txn in txns:
        cid = txn["r_record"].get("resolved_connection_id")
        if cid:
            conn_map[cid].append(txn)

    groups, events = [], []
    for cid, group_txns in conn_map.items():
        group_txns.sort(key=lambda x: x["r_record"].get("event_time", 0.0))
        start, end = group_txns[0]["r_record"]["event_time"], group_txns[-1]["r_record"]["event_time"]
        groups.append({"resolved_connection_id": cid, "connection_start": start,
                       "connection_end": end, "transactions": group_txns})
        events.extend([(start, 1), (end, -1)])

    if not groups:
        _write_quality()
        return

    # Remove stale shard files from any previous run before writing new ones.
    for stale in glob.glob(os.path.join(output_dir, "shard_*.jsonl")):
        os.remove(stale)

    # Sort events: at equal timestamps, process ends (-1) before starts (+1)
    # so connections that end exactly when another starts are NOT counted as concurrent.
    events.sort(key=lambda x: (x[0], x[1]))
    concurrent = peak = 0
    for _, d in events:
        concurrent += d
        peak = max(peak, concurrent)
    shard_count = max(1, peak)

    # Sort groups by start time for greedy allocation
    groups.sort(key=lambda x: x["connection_start"])
    replay_epoch = groups[0]["connection_start"]

    # Greedy interval partitioning: allocate connections to shards ensuring no overlaps
    shard_end_times = []  # Track latest end time for each shard
    connection_to_shard = {}  # Map connection index -> shard_id
    
    for idx, grp in enumerate(groups):
        start = grp["connection_start"]
        end = grp["connection_end"]
        
        # Find first shard where this connection doesn't overlap
        assigned = False
        for shard_id, shard_end in enumerate(shard_end_times):
            if start >= shard_end:  # No overlap
                connection_to_shard[idx] = shard_id
                shard_end_times[shard_id] = end
                assigned = True
                break
        
        # If no existing shard works, create a new one
        if not assigned:
            shard_id = len(shard_end_times)
            connection_to_shard[idx] = shard_id
            shard_end_times.append(end)
    
    # Use actual shard count from greedy allocation (should match peak)
    actual_shard_count = len(shard_end_times)

    handles = {}
    try:
        for sid in range(actual_shard_count):
            handles[sid] = open(os.path.join(output_dir, f"shard_{sid + 1}.jsonl"), "w", encoding="utf-8")

        for idx, grp in enumerate(groups):
            shard = connection_to_shard[idx]
            offset_ms = (grp["connection_start"] - replay_epoch) * 1000
            txn_list = grp["transactions"]
            is_h2 = txn_list[0]["r_record"].get("h2_connection_id") is not None

            batches, curr = [], []
            for i, txn in enumerate(txn_list):
                curr.append(txn)
                if i < len(txn_list) - 1:
                    gap = (txn_list[i + 1]["r_record"]["event_time"] - txn["r_record"]["event_time"]) * 1000
                    if not is_h2 or gap > h2_threshold_ms:
                        batches.append({"requests": curr, "gap_ms": min(gap, max_gap_ms)})
                        curr = []
                else:
                    batches.append({"requests": curr, "gap_ms": None})

            handles[shard].write(json.dumps({
                "shard_id": shard, "connection_group_index": idx,
                "resolved_connection_id": grp["resolved_connection_id"],
                "start_offset_ms": offset_ms, "request_batches": batches,
            }) + "\n")
    finally:
        for fh in handles.values():
            fh.close()

    with open(os.path.join(output_dir, "shard_metadata.json"), "w") as fh:
        json.dump({"shard_count": actual_shard_count, "replay_epoch": replay_epoch}, fh, indent=2)
    _write_quality()


def _build_shard_metadata(shard_path: str) -> dict:
    """Extract metadata from a single shard file.

    Returns: {"connections": [conn_meta, ...]}
    Each conn_meta includes: connection_id, connection_type, total_requests, start_offset_ms,
    requests_timing map (cumulative replay offset ms -> request count), and hit counts by route_intent.
    requests_timing keys are cumulative sums of gap_ms — matching actual VU replay timing exactly.
    """
    connections = []
    with open(shard_path, "r", encoding="utf-8") as fh:
        for line in fh:
            conn = json.loads(line)
            cid = conn.get("resolved_connection_id", "unknown")
            start_offset = conn.get("start_offset_ms", 0)

            total_requests = 0
            route_intent_counts = defaultdict(int)
            # keys are cumulative replay offset (ms) as exact float strings; no bucketing, no rounding
            requests_timing = {}
            cumulative_ms = 0.0

            for batch in conn.get("request_batches", []):
                batch_reqs = batch.get("requests", [])
                for req in batch_reqs:
                    total_requests += 1
                    route_intent_counts[req.get("route_intent", "unknown")] += 1

                key = str(cumulative_ms)
                requests_timing[key] = requests_timing.get(key, 0) + len(batch_reqs)

                gap = batch.get("gap_ms")
                if gap is not None:
                    cumulative_ms += gap

            # Determine connection type from h2_connection_id presence
            if total_requests > 0:
                first_req = conn["request_batches"][0]["requests"][0]
                h2_id = first_req.get("r_record", {}).get("h2_connection_id")
                conn_type = "h2" if h2_id else "h1"
            else:
                conn_type = "unknown"

            conn_meta = {
                "connection_id": cid,
                "connection_type": conn_type,
                "total_requests": total_requests,
                "start_offset_ms": start_offset,
                "requests_timing": requests_timing,
                "edge_hit": route_intent_counts.get("edge_intended", 0),
                "parent_hit": route_intent_counts.get("parent_intended", 0),
                "peer_hit": route_intent_counts.get("peer_intended", 0),
                "origin_hit": route_intent_counts.get("origin_intended", 0),
            }
            connections.append(conn_meta)

    return {"connections": connections}


def _write_shard_metadata(output_dir: str, shard_count: int) -> None:
    """Write metadata JSON file for each shard file."""
    for sid in range(shard_count):
        shard_path = os.path.join(output_dir, f"shard_{sid + 1}.jsonl")
        metadata = _build_shard_metadata(shard_path)
        metadata_path = os.path.join(output_dir, f"shard_{sid + 1}_metadata.json")
        with open(metadata_path, "w", encoding="utf-8") as fh:
            json.dump(metadata, fh, indent=2)



def correlate_and_shard(output_dir: str) -> None:
    """
    Read normalized_r.jsonl and normalized_f.jsonl from output_dir.
    Join r/f records by request_id, group by connection, assign shards, and write:
      shard_*.jsonl, shard_*_metadata.json, shard_metadata.json, correlation_quality_report.json
    """
    
    input_dir = output_dir
    output_dir = os.path.join(output_dir, "shards")
    os.makedirs(output_dir, exist_ok=True)

    # 🔹 Changed 'output_dir' to 'input_dir' for these two read operations
    f_index = _build_f_index(os.path.join(input_dir, "normalized_f.jsonl"))
    txns, quality = _correlate(os.path.join(input_dir, "normalized_r.jsonl"), f_index)
    
    _write_shards(txns, output_dir, quality)

    # Read shard_count from metadata and write per-shard metadata
    shard_meta_path = os.path.join(output_dir, "shard_metadata.json")
    if not os.path.exists(shard_meta_path):
        print(f"⚠ Warning: Shard metadata not created (no transactions to shard)")
        return
    
    with open(shard_meta_path, "r") as fh:
        meta = json.load(fh)
    _write_shard_metadata(output_dir, meta["shard_count"])
