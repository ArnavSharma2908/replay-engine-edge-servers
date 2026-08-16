"""Component 3 — Normalization Layer: transforms parsed r/f records to canonical form."""

import json
import logging
import os
import re

logger = logging.getLogger(__name__)


def _parse_arl(uri: str) -> dict:
    """Decompose ARL URI: /<RequestCode>/<CustomerID>/<CpCode>/<MaxAge>/<noscheme-url>"""
    parts = uri.split("/", 5)
    if len(parts) < 6:
        return {"arl_parse_error": True, "arl_path": uri}
    noscheme = parts[5]
    hp = noscheme.split("/", 1)
    return {
        "arl_parse_error": False,
        "arl_path": uri,
        "arl_request_code": parts[1],
        "arl_customer_id": parts[2],
        "arl_cpcode": parts[3],
        "arl_max_age": parts[4],
        "arl_noscheme_url": noscheme,
        "arl_resource_host": hp[0],
        "arl_resource_path": f"/{hp[1]}" if len(hp) > 1 else "/",
    }


def _parse_range(range_str: str) -> dict:
    """Classify and decompose a byte-range string into structured fields."""
    if not range_str:
        return {"range_class": "full_range", "range_start": None, "range_end": None}
    m = re.search(r"(?:bytes=)?(\d*)-(\d*)", range_str)
    if not m:
        return {"range_class": "unknown_range", "range_start": None, "range_end": None}
    sg, eg = m.groups()
    start = int(sg) if sg else None
    end = int(eg) if eg else None
    if start is None and end is None:
        rc = "unknown_range"
    elif start == 0:
        rc = "first_byte_range"
    elif start is not None and start > 0:
        rc = "mid_range"
    else:
        rc = "full_range"
    return {"range_class": rc, "range_start": start, "range_end": end, "raw_range": range_str}


def _generate_dummy_body(size: int, strategy: str = "x") -> bytes:
    """Synthetic POST body of exactly `size` bytes.
    strategy: 'x' = ASCII fill (default), 'zeros' = null bytes, 'json' = valid JSON envelope.
    """
    if size <= 0:
        return b""
    if strategy == "zeros":
        return b"\x00" * size
    if strategy == "json":
        prefix, suffix = b'{"_replay":true,"_pad":"', b'"}'
        overhead = len(prefix) + len(suffix)
        return (prefix + b"x" * max(0, size - overhead) + suffix) if size >= overhead else b"x" * size
    return b"x" * size  # safe ASCII fill


def _normalize_r(data: dict) -> dict:
    out = data.copy()
    arl = _parse_arl(out.get("uri", ""))
    out.update(arl)

    features = out.get("features_applied", "")
    scheme, h2_conn, h2_stream = "http", None, None
    if "aid=" in features:
        out["arlid"] = features.split("aid=")[1].split(";")[0]

    if "h2=" in features:
        scheme = "https"
        try:
            sub = features.split("h2=")[1].split(";")[0].split(":")
            if len(sub) >= 2:
                h2_conn = sub[0]
                h2_stream = int(sub[1]) if sub[1].isdigit() else None
        except Exception:
            pass
    elif "q=" in features or "ssl=" in features:
        scheme = "https"

    out["scheme"] = scheme
    out["h2_connection_id"] = h2_conn
    out["h2_stream_id"] = h2_stream
    out["resolved_connection_id"] = h2_conn if h2_conn else out.get("connection_id")


    host = arl.get("arl_resource_host", "").lower()
    for suffix in (":80", ":443"):
        if host.endswith(suffix):
            host = host[: -len(suffix)]
    out["canonical_host"] = host

    r17 = out.get("host", "").lower().split(":")[0]
    out["canonical_url"] = f"{scheme}://{r17}/{arl['arl_resource_path']}" if not arl.get("arl_parse_error") else ""
    if host and r17 and host != r17:
        logger.warning("host mismatch: ARL '%s' vs r17 '%s' (file=%s line=%s)",
                       host, r17, out.get("source_file"), out.get("source_line_no"))

    out.update(_parse_range(out.get("range", "")))

    bs, os_ = out.get("bytes_served"), out.get("object_size")
    out["preferred_size"] = bs if bs is not None else os_
    out["size_candidates"] = [bs, os_]

    # r15 flags: i=RAM, d=Disk, g=Peer, P/p=Parent (logs use both cases for parent), m=Memory (Ghost memory cache)
    cp = out.get("cache_path", "")
    out["cache_path_class"] = (
        "mem" if "m" in cp else "ram" if "i" in cp else "disk" if "d" in cp else
        "peer" if "g" in cp else "parent" if ("P" in cp or "p" in cp) else "unknown"
    )
    out["request_id_usable"] = bool(out.get("request_id", ""))
    return out


def _normalize_f(data: dict) -> dict:
    out = data.copy()
    out.update(_parse_arl(out.get("uri", "")))
    out["request_id_usable"] = bool(out.get("request_id", ""))
    return out


def _make_wave_request(r: dict) -> dict:
    """Build a wave file request entry from a normalized r record (f_record absent for prewarm)."""
    _route_map = {
        "Memory": "edge_intended", "RAM": "edge_intended", "Disk": "edge_intended",
        "Peer": "peer_intended", "Parent": "parent_intended",
    }
    cache_class = r.get("cache_path_class", "unknown")
    return {
        "request_id": r.get("request_id", ""),
        "r_record": r,
        "f_record": None,
        "cache_path_class": cache_class,
        "route_intent": _route_map.get(cache_class, "origin_intended"),
        "confidence_class": "unmatched_r",
    }


def build_wave_files(output_dir: str, wave_buckets: dict) -> None:
    """Write 4 prewarm wave JSONL files to output_dir (one connection group per file).

    edge_wave1.jsonl   -> 'd' in cache_path (Disk hits)
    peer_wave1.jsonl   -> 'g' in cache_path (Peer hits)
    parent_wave1.jsonl -> 'p' in cache_path (Parent hits)
    edge_wave2.jsonl   -> 'm' or 'i' in cache_path (Memory / RAM hits)
    """
    for name, requests in wave_buckets.items():
        batches = [{"requests": [req], "gap_ms": 0} for req in requests]
        if batches:
            batches[-1]["gap_ms"] = None
        connection = {
            "connection_group_index": 0,
            "resolved_connection_id": name,
            "start_offset_ms": 0.0,
            "request_batches": batches,
        }
        # Write as JSONL format (single line) for k6 replay compatibility
        with open(os.path.join(output_dir, f"{name}.jsonl"), "w", encoding="utf-8") as fh:
            json.dump(connection, fh)


def build_traffic_profile(output_dir: str) -> None:
    """Read normalized_r.jsonl and write traffic_profile.json to output_dir."""
    from collections import defaultdict

    in_path = os.path.join(output_dir, "normalized_r.jsonl")
    out_path = os.path.join(output_dir, "traffic_profile.json")

    make_slot = lambda: {
        "total_requests": 0,
        "cache_distribution": {"memory": 0, "ram": 0, "disk": 0, "Origin": 0, "Peer": 0, "Parent": 0, "toPeer(G)": 0, "toChild(P)": 0},
        "protocol_distribution": {"http": 0, "https": 0, "H2": 0, "H3": 0},
    }
    agg = make_slot()
    per_host: dict = defaultdict(make_slot)
    unique_hostnames: dict = {}
    wave_buckets: dict = {"edge_wave1": [], "peer_wave1": [], "parent_wave1": [], "edge_wave2": []}
    
    # Deduplicate requests: keep first occurrence of each (url, protocol, method, status, size) tuple
    seen_requests = {}  # uniqueness key -> first r_record

    with open(in_path, "r", encoding="utf-8") as fin:
        for line in fin:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            host = r.get("canonical_host") or ""
            url = r.get("canonical_url") or ""
            arl_id = r.get("arlid") or ""
            cpcode = r.get("arl_cpcode") or ""
            key = f"{host}-{cpcode}-{arl_id}" if host or cpcode else "unknown"
            if key not in unique_hostnames:
                unique_hostnames[key] = url
            cp = r.get("cache_path") or ""
            h2 = r.get("h2_stream_id") is not None
            scheme = r.get("scheme", "http")
            
            # Determine protocol type
            if h2:
                protocol = "h2"
            elif scheme == "https":
                protocol = "https"
            else:
                protocol = "http"
            
            # Build uniqueness key: (canonical_url, protocol, method, status, preferred_size)
            uniqueness_key = (
                r.get("canonical_url", ""),
                protocol,
                r.get("method", ""),
                r.get("status", ""),
                r.get("preferred_size")
            )
            
            # Keep only the first occurrence
            if uniqueness_key not in seen_requests:
                seen_requests[uniqueness_key] = r
            
            for slot in (agg, per_host[key]):
                slot["total_requests"] += 1
                cd = slot["cache_distribution"]
                if "m" in cp: cd["memory"] += 1
                if "i" in cp: cd["ram"] += 1
                if "d" in cp: cd["disk"] += 1
                if "o" in cp: cd["Origin"] += 1
                if "g" in cp: cd["Peer"] += 1
                if "p" in cp: cd["Parent"] += 1
                if "G" in cp: cd["toPeer(G)"] += 1
                if "P" in cp: cd["toChild(P)"] += 1
                pd = slot["protocol_distribution"]
                if h2:
                    pd["H2"] += 1
                elif scheme == "https":
                    pd["https"] += 1
                else:
                    pd["http"] += 1
    
    # Wave bucketing using deduplicated requests only
    for r in seen_requests.values():
        cp = r.get("cache_path") or ""
        if "m" in cp or "i" in cp:
            wave_buckets["edge_wave2"].append(_make_wave_request(r))
        elif "d" in cp:
            wave_buckets["edge_wave1"].append(_make_wave_request(r))
        elif "g" in cp:
            wave_buckets["peer_wave1"].append(_make_wave_request(r))
        elif "p" in cp or "P" in cp:
            wave_buckets["parent_wave1"].append(_make_wave_request(r))

    profile = {
        "unique_hostnames": unique_hostnames,
        "request_distribution": {"all": agg, **dict(per_host)},
    }
    with open(out_path, "w", encoding="utf-8") as fout:
        json.dump(profile, fout, indent=2)

    build_wave_files(output_dir, wave_buckets)
    build_post_body_debug(output_dir)  # runs in same pass-cycle; reads normalized_r.jsonl


def build_post_body_debug(output_dir: str, strategy: str = "x") -> None:
    """Write post_body_debug.jsonl: dummy-body validation for every POST in normalized_r.jsonl.
    Reads client_request_body_size (r56); generates and size-validates a synthetic body per record.
    """
    in_path = os.path.join(output_dir, "normalized_r.jsonl")
    out_path = os.path.join(output_dir, "post_body_debug.jsonl")
    with open(in_path, "r", encoding="utf-8") as fin, \
         open(out_path, "w", encoding="utf-8") as fout:
        for line in fin:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("method") != "POST":
                continue
            body_size = r.get("client_request_body_size")  # None when field absent or '-'
            dummy = _generate_dummy_body(body_size or 0, strategy)
            fout.write(json.dumps({
                "request_id":      r.get("request_id", ""),
                "uri":             r.get("uri", ""),
                "status":          r.get("status"),
                "client_body_size": body_size,
                "dummy_body_size": len(dummy),
                "body_source":     f"field56_{strategy}" if body_size else "fallback_empty",
                "size_match":      len(dummy) == (body_size or 0),
                "body_preview":    dummy[:64].decode("ascii", errors="replace"),
            }) + "\n")


def normalize(output_dir: str) -> None:
    """
    Read parsed_r.jsonl and parsed_f.jsonl from output_dir,
    write normalized_r.jsonl and normalized_f.jsonl to the same directory.
    """
    for fname_in, fname_out, norm_fn, event_type in [
        ("parsed_r.jsonl", "normalized_r.jsonl", _normalize_r, "r_event"),
        ("parsed_f.jsonl", "normalized_f.jsonl", _normalize_f, "f_event"),
    ]:
        in_path = os.path.join(output_dir, fname_in)
        out_path = os.path.join(output_dir, fname_out)
        with open(in_path, "r", encoding="utf-8") as fin, \
             open(out_path, "w", encoding="utf-8") as fout:
            for line in fin:
                try:
                    p = json.loads(line)
                    if p.get("status") == "accepted" and p.get("type") == event_type:
                        fout.write(json.dumps(norm_fn(p["data"])) + "\n")
                except json.JSONDecodeError as e:
                    logger.error("JSON error in %s: %s", in_path, e)
