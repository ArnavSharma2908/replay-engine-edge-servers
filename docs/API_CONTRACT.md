# Local API contract

Start the read-only API from the repository root:

```bash
python -m api.server
```

The default base URL is `http://127.0.0.1:8000`. Set `API_PORT` or pass
`--port` to use another port. The dashboard can use that URL as
`VITE_API_BASE_URL`.

## `GET /api/shards`

Reads the generated files in `output/shards/`:

```json
{
  "shard_count": 2,
  "peak_concurrency": 2,
  "shards": [
    { "id": 1, "connection_count": 12, "status": "ready" }
  ]
}
```

## `GET /api/replay/status`

Reads the status written by the existing orchestrator around its k6 process:

```json
{
  "running": false,
  "requests_replayed": 12,
  "requests_total": 12
}
```

The measured comparison endpoints are also available after a k6 run:

* `GET /api/metrics?mode=origin` or `mode=edge` reads the corresponding k6
  summary from `output/replay_metrics_<mode>.json`.
* `GET /api/requests/live?limit=20` reads request telemetry when the replay
  writes `output/replay_requests.jsonl`; it returns a clear unavailable response
  until that telemetry exists rather than fabricating requests.

CORS is enabled for local dashboard development; no authentication is required.
