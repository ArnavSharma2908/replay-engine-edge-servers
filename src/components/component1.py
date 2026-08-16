"""Component 1 — Schema Registry: single source of truth for r/f field positions."""


def _safe_int(v: str):
    return int(v) if v and v != "-" else None


def _safe_float(v: str):
    # None (not 0.0) prevents a missing timestamp from being read as epoch 1970.
    return float(v) if v and v != "-" else None


def _safe_str(v: str) -> str:
    return v if v != "-" else ""


def get_schemas() -> dict:
    """Return {"r": field_map, "f": field_map} for injection into Component 2.

    Keys are 1-based positions relative to the type token ('r' or 'f').
    Values are (logical_name, cast_fn) pairs.
    Formula: tokens[type_token_index + position - 1]
    """
    r = {
        2:  ("event_time",       _safe_float),  # Unix epoch of request
        3:  ("ssl_overhead_ms",  _safe_int),    # SSL handshake (ms)
        7:  ("object_size",      _safe_int),    # Cached object size (bytes)
        8:  ("bytes_served",     _safe_int),    # Bytes served to client
        11: ("method",           _safe_str),    # HTTP method
        12: ("uri",              _safe_str),    # ARL URI
        13: ("status",           _safe_int),    # HTTP status to client
        15: ("cache_path",       _safe_str),    # Cache-path flags (i=RAM, d=Disk, g=Peer, P=Parent)
        16: ("content_type",     _safe_str),    # Content-Type
        17: ("host",             _safe_str),    # Host header from client
        18: ("cookie",           _safe_str),    # Cookie header
        19: ("referer",          _safe_str),    # Referer header
        20: ("user_agent",       _safe_str),    # User-Agent header
        31: ("request_id",              _safe_str),    # Primary correlation key (matches f28)
        33: ("range",                  _safe_str),    # Byte-range string
        56: ("client_request_body_size", _safe_int),  # POST/PUT/PATCH body bytes (r56)
        70: ("features_applied",       _safe_str),    # Feature string (h2=, q=, ssl=)
        80: ("connection_id",    _safe_str),    # H1 TCP connection ID
    }
    f = {
        2:  ("event_time",     _safe_float),   # Unix epoch when forward leg completed
        6:  ("turnaround_ms",  _safe_int),     # Origin TTFB (ms)
        7:  ("transfer_ms",    _safe_int),     # Origin byte-transfer time (ms)
        8:  ("bytes_received", _safe_int),     # Bytes received from origin
        10: ("forward_target", _safe_str),     # Next-hop IP
        12: ("method",         _safe_str),     # HTTP method forwarded
        13: ("uri",            _safe_str),     # ARL URI forwarded
        14: ("status",         _safe_int),     # HTTP status from upstream
        18: ("object_status",  _safe_str),     # Route-path flags (o=origin, g=peer, p/P=parent)
        28: ("request_id",     _safe_str),     # Primary correlation key (matches r31)
        41: ("range",          _safe_str),     # Byte-range forwarded
        62: ("forward_host",   _safe_str),     # Host header on forward leg
    }
    return {"r": r, "f": f}
