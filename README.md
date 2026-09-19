# Production Replay Orchestrator

Complete end-to-end orchestration tool for production traffic replay testing.

## Quick Start

```bash
# Using CLI arguments
./.venv/bin/python orchestrate.py \
  --edge 198.18.134.240 \
  --parent 198.18.134.241 \
  --peers 198.18.134.242,198.18.134.243

# Using configuration file
./.venv/bin/python orchestrate.py --config orchestrator_config.json

# With verbose logging
./.venv/bin/python orchestrate.py --config orchestrator_config.json --verbose
```

## What It Does

The orchestrator automates these steps:

1. **Generate Traffic Shards** - Runs `production-replay` module to create shard files
2. **Deploy ARLs** - Deploys unique ARL IDs to all machines using `arl-deploy`
3. **Prewarm (Optional)** - Runs prewarm k6 scripts
4. **Restart Webghost** - Restarts webghost on all edge, peer, and parent machines
5. **Execute Replay** - Runs k6 replay script with appropriate VU count

## Features

- ✓ Beautiful color-coded logging with progress indicators
- ✓ Automatic deduplication of ARL IDs (deploys each ARL only once)
- ✓ Live output streaming from sub-commands (arl-deploy)
- ✓ Full command visibility for transparency
- ✓ Graceful error handling with clear error messages
- ✓ Verbose mode for detailed debugging

## Configuration

Edit `orchestrator_config.json`:

```json
{
  "machines": {
    "edge": "198.18.134.240",
    "parent": "198.18.134.241",
    "peers": ["198.18.134.242", "198.18.134.243"]
  },
  "prewarm": {
    "enabled": false,
    "command": "k6 run prewarm.js"
  }
}
```

## Output Example

```
╭──────────────────────────────────────────────────────────────────────────────╮
│                       Production Replay Orchestration                        │
│                        End-to-End Workflow Automation                        │
╰──────────────────────────────────────────────────────────────────────────────╯

[1/5] ▶ Generating Traffic Shards and Profile
  › Command: python -m production-replay.src.main
  ✓ Traffic generation completed
✓ Traffic profile created: output/traffic_profile.json
  • Generated 257 shard files in output

[2/5] ▶ Deploying ARLs for Unique ARL IDs
  › Parsing traffic profile
  ✓ Found 2 hostname-cpcode-arl entries
  • Identified 1 unique ARL IDs to deploy
  ────────────────────────────────────────────────────────────────────────────
  › [1/1] Deploying ARL 321127 for www.bsp.gov.ph
  • Command: arl-deploy deploy --arl-id 321127 --hostname www.bsp.gov.ph --edge 198.18.134.240 --parent 198.18.134.241 --sample-url "https://www.bsp.gov.ph/..."

    [arl-deploy output streams here...]

  ✓ ARL 321127 deployed successfully

✓ All 1 ARL deployments completed

[3/5] ▶ Running Prewarm K6 Scripts
⚠ Prewarm step is disabled. Skipping.

[4/5] ▶ Restarting Webghost on All Machines
  • Restarting webghost on 4 machines
  ────────────────────────────────────────────────────────────────────────────
  › [1/4] Restarting webghost on 198.18.134.240
  ✓ Webghost restarted on 198.18.134.240
  ...
✓ Webghost restarted on all 4 machines

[5/5] ▶ Executing Replay Script with K6
  › Counting shard files
  ✓ Found 257 shard files → TEST_VUS=257
  › Executing k6 from production-replay

  [k6 output streams here...]

✓ Replay execution completed successfully

╭──────────────────────────────────────────────────────────────────────────────╮
│                   Orchestration Completed Successfully                       │
│                      All steps executed without errors                       │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Documentation

- [ORCHESTRATOR.md](ORCHESTRATOR.md) - Detailed documentation
- [orchestrator_config.json](orchestrator_config.json) - Sample configuration

## Requirements

- Python 3.8+
- Virtual environments in `.venv` and `production-arl-setup/.venv`
- k6 installed for replay execution
- SSH access to all Ghost machines

## Local dashboard API

<<<<<<< HEAD
The dashboard is a Vite/React frontend. Run the backend API and the frontend in
separate terminals from the repository root.

### 1. Generate the real pipeline data

Activate the project virtual environment, then run the existing pipeline:

```powershell
.\.venv\Scripts\Activate.ps1
python -m src.main
```

This reads access logs from `input/` and writes the shard output to
`output/shards/`. The dashboard uses this output through the API; it does not
use hard-coded shard totals.

### 2. Start the API

In a second terminal, from the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m api.server
```

The API listens at `http://127.0.0.1:8000` by default and exposes:

- `GET /api/shards` — real shard count, peak concurrency, and connection counts
- `GET /api/replay/status` — replay progress from the latest orchestrator run

The API enables CORS for the Vite development server. Verify it directly with:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/shards
Invoke-RestMethod http://127.0.0.1:8000/api/replay/status
```

### 3. Start the frontend

In a third terminal, enter the dashboard directory:

```powershell
cd dashboard
npm install
```

Create `dashboard/.env` with the API base URL:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

Start Vite:

```powershell
npm run dev
```

Open the URL printed by Vite, normally `http://localhost:5173`.

Every displayed value is now sourced from generated shard files or measured
replay telemetry. If a k6 summary or request telemetry file is not available,
the corresponding panel shows `unavailable` rather than inventing a value.
Run separate origin and edge replays to populate both comparison series.

### Optional: run the full replay

For a complete k6 replay, configure the machines in
`orchestrator_config.json`, then run:

```powershell
python orchestrate.py --config orchestrator_config.json
```

The orchestrator updates `output/replay_status.json` while k6 runs and writes
mode-specific k6 summaries such as `output/replay_metrics_origin.json`.
Set `"mode": "origin"` or `"mode": "edge"` under the `replay` configuration
before each run. The API automatically serves measured results to the
dashboard.
=======
The additive read-only API serves the generated shard output and replay status:

```bash
python -m api.server
```

It listens on `http://127.0.0.1:8000` by default. Set
`VITE_API_BASE_URL=http://127.0.0.1:8000` in the dashboard, or override the
port with `API_PORT`/`--port`.
>>>>>>> arnavsharma2908-add-readonly-api
