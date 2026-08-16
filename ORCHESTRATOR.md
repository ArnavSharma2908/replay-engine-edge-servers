# Production Replay Orchestrator

The orchestrator script automates the complete end-to-end production replay workflow.

## Workflow Overview

The orchestrator executes these steps in sequence:

1. **Generate Traffic Shards** - Runs `production-replay` module to create shard files and traffic profile
2. **Deploy ARLs** - Parses traffic profile and deploys ARLs for all unique hostname-cpcode-arl combinations
3. **Prewarm (Optional)** - Runs prewarm k6 scripts (currently placeholder)
4. **Restart Webghost** - Restarts webghost on all edge, peer, and parent machines
5. **Execute Replay** - Runs the k6 replay script with appropriate VU count

## Prerequisites

1. **Virtual Environments**
   - `.venv` in the project root
   - `.venv` in the `production-arl-setup` directory

2. **SSH Access**
   - SSH key configured for all Ghost machines
   - Default: `/Users/arnsharm/.ssh/identity`

3. **Dependencies**
   - Python 3.8+ with production-replay module
   - `arl-deploy` CLI installed in production-arl-setup .venv
   - `k6` for replay execution

## Usage

### Option 1: Using Configuration File (Recommended)

```bash
# Edit orchestrator_config.json to set your machines
python orchestrate.py --config orchestrator_config.json
```

### Option 2: Using CLI Arguments

```bash
# Minimal setup (edge and parent only)
python orchestrate.py \
  --edge 198.18.134.240 \
  --parent 198.18.134.241

# With peers
python orchestrate.py \
  --edge 198.18.134.240 \
  --parent 198.18.134.241 \
  --peers 198.18.134.242,198.18.134.243
```

### Advanced Usage

```bash
# Enable debug logging
python orchestrate.py --config orchestrator_config.json --log-level DEBUG

# Override specific machines while keeping config defaults
python orchestrate.py \
  --config orchestrator_config.json \
  --edge 198.18.134.250
```

## Configuration File

The `orchestrator_config.json` file structure:

```json
{
  "machines": {
    "edge": "198.18.134.240",
    "parent": "198.18.134.241",
    "peers": ["198.18.134.242", "198.18.134.243"]
  },
  "paths": {
    ".venv": ".venv",
    "production_replay_src": "production-replay/src",
    "production_arl_setup": "production-arl-setup",
    "output_dir": "output",
    "traffic_profile": "output/traffic_profile.json",
    "replay_script": "production-replay/replay.js"
  },
  "ssh": {
    "ssh_key_path": "/Users/arnsharm/.ssh/identity",
    "connect_timeout": 30,
    "command_timeout": 60
  },
  "arl_setup": {
    "default_origin_host": null,
    "timeout": 300
  },
  "prewarm": {
    "enabled": false,
    "command": "# Placeholder for prewarm k6 scripts"
  },
  "replay": {
    "log_level": "requests",
    "shard_dir": "../output"
  }
}
```

## Traffic Profile Format

The orchestrator expects `output/traffic_profile.json` to contain:

```json
{
  "unique_hostnames": {
    "hostname-cpcode-arl_id": "sample_url",
    "www.example.com-491490-321127": "https://www.example.com/path/to/resource.js"
  }
}
```

### Hostname Key Format

The orchestrator parses keys in the format: `hostname-cpcode-arl_id`

- **hostname**: Can contain dots and hyphens (e.g., `www.bsp.gov.ph`)
- **cpcode**: Numeric CP code (e.g., `491490`)
- **arl_id**: Numeric ARL ID (e.g., `321127`)

Example: `www.bsp.gov.ph-491490-321127`

The orchestrator:
1. Splits by the **last two hyphens** to extract cpcode and arl_id
2. Everything before is treated as the hostname
3. Extracts origin host from the sample URL

## Step-by-Step Execution Details

### Step 1: Generate Traffic Shards

Runs:
```bash
python -m production-replay.src.main
```

Creates:
- `output/shard_*.jsonl` - Individual traffic shard files
- `output/traffic_profile.json` - Traffic analysis and unique hostnames

### Step 2: Deploy ARLs

For each unique hostname-cpcode-arl combination:

```bash
source production-arl-setup/.venv/bin/activate && \
arl-deploy deploy \
  --arl-id 321127 \
  --hostname www.bsp.gov.ph \
  --edge 198.18.134.240 \
  --parent 198.18.134.241 \
  --peers 198.18.134.242,198.18.134.243 \
  --origin-host www.bsp.gov.ph \
  --sample-url https://www.bsp.gov.ph/_catalogs/masterpage/custom/js/sweetalert2.min.js
```

### Step 3: Prewarm (Placeholder)

Currently disabled by default. To enable:

1. Set `"enabled": true` in config under `"prewarm"`
2. Provide the actual prewarm command:
   ```json
   "prewarm": {
     "enabled": true,
     "command": "k6 run prewarm.js"
   }
   ```

### Step 4: Restart Webghost

Executes on each machine:
```bash
ssh root@<machine> \
  "/a/sbin/trident_run unstickystop webghost -r 'Production replay orchestration' && \
   /a/sbin/trident_run stickystop webghost"
```

Includes:
- 5-second wait after restart
- Process verification check

### Step 5: Execute Replay

Counts shard files and runs:
```bash
cd production-replay
TEST_VUS=<num_shards> LOG_LEVEL=requests k6 -e SHARD_DIR="../output" run replay.js
```

## Troubleshooting

### Traffic Generation Fails

Check:
- Input files are in the correct location
- production-replay module is properly installed
- Python environment is activated

### ARL Deployment Fails

Check:
- `production-arl-setup/.venv` is activated
- `arl-deploy` CLI is installed: `pip list | grep arl-deployer`
- SSH key has access to all machines
- Hostname key format is correct in traffic_profile.json

### Webghost Restart Fails

Check:
- SSH connectivity: `ssh -i ~/.ssh/identity root@<machine> "echo test"`
- Ghost is installed: `ssh root@<machine> "ls -la /a/bin/ghost"`
- Verify manually: `ssh root@<machine> "ps aux | grep ghost"`

### Replay Execution Fails

Check:
- k6 is installed: `which k6`
- Shard files exist: `ls output/shard_*.jsonl`
- replay.js script exists and is valid
- Edge machine is reachable from replay client

## Logging

The orchestrator provides detailed logging at each step:

```
2026-06-20 11:30:00 - INFO - ================================================================================
2026-06-20 11:30:00 - INFO - STEP 1: Generating Traffic Shards and Profile
2026-06-20 11:30:00 - INFO - ================================================================================
2026-06-20 11:30:05 - INFO - ✓ Traffic profile created: output/traffic_profile.json
...
```

Enable debug logging for more details:
```bash
python orchestrate.py --config orchestrator_config.json --log-level DEBUG
```

## Example Complete Run

```bash
# 1. Ensure all prerequisites are met
ls -la .venv production-arl-setup/.venv
which k6

# 2. Update configuration
vim orchestrator_config.json

# 3. Run orchestration
python orchestrate.py --config orchestrator_config.json

# Output:
# ================================================================================
# Starting Production Replay Orchestration
# ================================================================================
# 
# ================================================================================
# STEP 1: Generating Traffic Shards and Profile
# ================================================================================
# ...
# ✓ Traffic profile created: output/traffic_profile.json
# 
# ================================================================================
# STEP 2: Deploying ARLs for Unique Hostname-CPCode-ARL Combinations
# ================================================================================
# Found 2 unique hostname-cpcode-arl combinations
# [1/2] Deploying ARL 321127 for www.bsp.gov.ph
# ✓ ARL 321127 deployed successfully for www.bsp.gov.ph
# [2/2] Deploying ARL 321127 for www.BSP.gov.ph
# ✓ ARL 321127 deployed successfully for www.BSP.gov.ph
# 
# ================================================================================
# STEP 3: Running Prewarm K6 Scripts
# ================================================================================
# Prewarm step is disabled. Skipping.
# 
# ================================================================================
# STEP 4: Restarting Webghost on All Machines
# ================================================================================
# Restarting webghost on 4 machines: [...]
# ✓ Webghost restarted on all machines
# 
# ================================================================================
# STEP 5: Executing Replay Script with K6
# ================================================================================
# Found 256 shard files, setting TEST_VUS=256
# [k6 output streaming...]
# ✓ Replay execution completed successfully
# 
# ================================================================================
# Production Replay Orchestration Completed Successfully
# ================================================================================
```

## Integration with Existing Tools

The orchestrator integrates with:

1. **production-replay** - Traffic shard generation
   - Called as Python module: `python -m production-replay.src.main`

2. **arl-deploy** - ARL deployment tool
   - Uses virtual environment in `production-arl-setup/`
   - Calls CLI: `arl-deploy deploy ...`

3. **k6** - Load testing and replay
   - Automatically sets VU count based on shard count
   - Passes SHARD_DIR environment variable

## Future Enhancements

- [ ] Implement actual prewarm k6 scripts
- [ ] Add validation step after ARL deployment
- [ ] Support for custom origin host mapping file
- [ ] Parallel ARL deployments for faster execution
- [ ] Rollback capability on failure
- [ ] Post-replay validation and reporting
