# ARL Deployer

Automated Trident customer configuration deployment tool with verification and self-healing capabilities.

## Overview

ARL Deployer automates the deployment of Trident customer configurations (ARL files) to edge, parent, and peer Ghost machines. It provides:

- **Automated Discovery**: Find ARL files by CPCode and hostname
- **Full Deployment**: Deploy to edge, parent, and peer machines with proper configuration
- **Automatic Verification**: Test deployments and parse Ghost access logs
- **Self-Healing**: Automatically fix common issues using YAML-based rules
- **Rollback Support**: Full rollback capability with automatic backups
- **State Tracking**: Track deployment history and status

## Features

✅ **Config Discovery** - Search for ARL files by CPCode and hostname  
✅ **SSH Pre-flight Checks** - Validate connectivity before deployment  
✅ **Intelligent Deployment** - Copy files, modify XML, restart Ghost  
✅ **Access Log Verification** - Parse f-lines and r-lines for validation  
✅ **YAML Rule Engine** - Extensible self-healing rules  
✅ **Full Rollback** - Restore previous state with backups  
✅ **CLI Interface** - Professional commands with Click framework  

## Installation

### Prerequisites

- Python 3.8 or higher
- SSH key authentication configured to target machines
- Access to Trident config server

### Install from source

```bash
# Clone or navigate to the repository
cd production-arl-setup

# Install in development mode
pip install -e .

# Or install from requirements
pip install -r requirements.txt
```

### Install XML manipulator library

```bash
cd xml-manipulator
pip install .
cd ..
```

## Configuration

### config.yaml

The main configuration file contains:

- **config_server**: Location of ARL files and dependencies
- **ghost**: Ghost machine paths and commands
- **ssh**: Connection settings and timeouts
- **deployment**: Backup and rollback settings
- **verification**: Access log parsing settings
- **logging**: Log levels and formats

Example:

```yaml
config_server:
  hostname: "198.18.99.44"
  username: "root"
  ssh_key_path: "/root/.ssh/identity"
  directories:
    arl_data: "/home/production/arl.data/"
    hd_data: "/myhome/shared/newMD/tarballs/ghostdir.hd.data/"
    waf_data: "/myhome/shared/newMD/tarballs/ghostdir.waf.data/"

ghost:
  arl_data_path: "/a/etc/ghost/arl.data/"
  arlindex_path: "/a/etc/ghost/arl.data/arlindex.xml"
  access_log_path: "/a/logs/ghost.access.log"
  trident_run: "/a/sbin/trident_run"
```

### rules.yaml

Self-healing rules for common issues. Each rule has:

- **name**: Unique identifier
- **condition**: When to trigger (log patterns, verification failures)
- **actions**: What to do (copy files, modify XML, restart Ghost)

Example rule:

```yaml
rules:
  - name: "missing_hd_data"
    description: "Copy HD data when missing or inaccessible"
    enabled: true
    priority: 1
    condition:
      type: "log_pattern"
      patterns:
        - "ERR_.*HD.*DATA"
    actions:
      - type: "copy_directory"
        source:
          type: "config_server"
          path: "{hd_data_path}"
        destination:
          path: "/a/etc/ghost/hd.data/"
        targets:
          - "edge"
      - type: "restart_ghost"
        targets:
          - "edge"
      - type: "verify"
```

## Usage

### Deploy by CPCode

```bash
arl-deploy deploy \\
  --cpcode 1608524 \\
  --hostname hubdoc-app.xero-uat.com \\
  --edge 198.18.134.246 \\
  --parent 198.18.134.247
```

### Deploy by ARL file

```bash
arl-deploy deploy \\
  --arl-file ARL_hubdoc-app.xero.com.1042719.xml \\
  --edge 198.18.134.246 \\
  --parent 198.18.134.247 \\
  --peer 198.18.134.248
```

### Deploy with custom sample URL

```bash
arl-deploy deploy \\
  --cpcode 1608524 \\
  --hostname hubdoc-app.xero-uat.com \\
  --edge 198.18.134.246 \\
  --parent 198.18.134.247 \\
  --sample-url "https://hubdoc-app.xero-uat.com/api/test"
```

### Verify existing deployment

```bash
arl-deploy verify \\
  --edge 198.18.134.246 \\
  --parent 198.18.134.247 \\
  --sample-url "https://hubdoc-app.xero-uat.com/"
```

### Rollback deployment

```bash
arl-deploy rollback deploy_20260617_143022_a1b2c3d4
```

### Check deployment status

```bash
arl-deploy status
```

## Command Reference

### `arl-deploy deploy`

Deploy ARL configuration to machines.

**Required Options:**
- `--edge IP` - Edge machine IP address
- `--parent IP` - Parent machine IP address

**ARL Selection (choose one):**
- `--cpcode CODE --hostname HOST` - Search by CPCode and hostname
- `--arl-file FILE` - Use specific ARL file

**Optional:**
- `--peer IP` - Peer machine (can specify multiple)
- `--sample-url URL` - Override sample URL for verification
- `--no-verify` - Skip verification
- `--no-heal` - Skip self-healing
- `--config FILE` - Custom config file (default: config.yaml)

### `arl-deploy verify`

Verify deployment by executing test request.

**Required:**
- `--edge IP` - Edge machine IP
- `--parent IP` - Parent machine IP
- `--sample-url URL` - URL to test

### `arl-deploy rollback`

Rollback a deployment to previous state.

**Arguments:**
- `DEPLOYMENT_ID` - ID from deployment output

### `arl-deploy status`

List all deployments and their status.

## How It Works

### Deployment Flow

1. **Pre-flight Checks**
   - Validate SSH connectivity to all machines
   - Verify config server accessibility

2. **Config Discovery**
   - Search for ARL file by CPCode/hostname
   - Extract metadata and dependencies
   - Resolve sample URL from traffic profile

3. **Edge Deployment**
   - Copy ARL file to `/a/etc/ghost/arl.data/`
   - Copy dependencies (hd.data, waf.data) if needed
   - Add DNS simulation to force forwarding to parent
   - Update `arlindex.xml` with hostname mappings
   - Restart Ghost

4. **Parent/Peer Deployment**
   - Copy ARL file and dependencies
   - Update `arlindex.xml`
   - Restart Ghost

5. **Verification**
   - Execute curl request from edge
   - Wait for log flush
   - Parse `ghost.access.log` for f-line and r-line
   - Validate forward IP matches parent

6. **Self-Healing** (if verification fails)
   - Match failure against rule conditions
   - Execute rule actions (copy files, modify XML, restart)
   - Re-run verification
   - Iterate up to configured max attempts

### Access Log Parsing

The verifier parses Ghost access logs to validate deployment:

**F-line** (forward line):
```
f timestamp ... forward_ip client_ip method arl status_code ...
                    ^
                    Must match parent IP
```

**R-line** (response line):
```
r timestamp ... status_code ... error_code ...
                                    ^
                                    Check for errors
```

### Self-Healing Rules

Rules automatically fix common issues:

| Rule | Condition | Actions |
|------|-----------|---------|
| `missing_hd_data` | ERR.*HD.*DATA in logs | Copy HD data, restart Ghost, verify |
| `missing_waf_data` | ERR.*WAF.*DATA in logs | Copy WAF data, restart Ghost, verify |
| `ghost_not_accepting_connections` | No f-line + port stats show 0 accepts | Force restart Ghost, verify |
| `dns_simulation_failure` | Wrong forward IP | Fix DNS simulation tags, restart, verify |
| `arl_not_in_arlindex` | No config found for host | Add to arlindex.xml, restart, verify |

## Architecture

```
arl_deployer/
├── cli.py              # Click CLI interface
├── models.py           # Data classes and enums
├── ssh_manager.py      # SSH/SCP operations with retry logic
├── state.py            # State tracking and rollback
├── config_discovery.py # ARL file search and analysis
├── deployment.py       # Deployment orchestration
├── verification.py     # Access log parsing and verification
└── rules.py            # YAML rule engine for self-healing
```

## Troubleshooting

### Deployment fails with "SSH connection error"

- Verify SSH key is configured: `ls -la /root/.ssh/identity`
- Test manual SSH: `ssh -i /root/.ssh/identity root@<machine_ip>`
- Check config.yaml `ssh_key_path` setting

### Verification fails with "No f-line found"

- Check if Ghost is running: `ps aux | grep ghost`
- Verify ARL file is in arlindex.xml
- Check cache.log for errors: `tail /a/logs/cache.log`
- Run self-healing: deployment will auto-trigger rules

### Wrong forward IP in verification

- Check DNS simulation tags in ARL file
- Verify parent IP is correct in deployment command
- Self-healing rule `dns_simulation_failure` should fix this

### Missing hd.data or waf.data errors

- Self-healing rules will automatically copy dependencies
- Manually verify config server paths in config.yaml
- Check network connectivity to config server

## Development

### Running Tests

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests
pytest

# With coverage
pytest --cov=arl_deployer --cov-report=html
```

### Code Quality

```bash
# Format code
black arl_deployer/

# Lint
flake8 arl_deployer/

# Type checking
mypy arl_deployer/
```

### Adding New Rules

1. Edit `rules.yaml`
2. Add rule with name, condition, and actions
3. Test with a deployment that triggers the condition
4. Verify rule execution in logs

Example:

```yaml
- name: "my_custom_rule"
  description: "Fix custom issue"
  enabled: true
  priority: 2
  condition:
    type: "log_pattern"
    patterns:
      - "MY_CUSTOM_ERROR"
  actions:
    - type: "copy_file"
      source: "/path/to/fix"
      destination: "/a/etc/ghost/fix"
      targets: ["edge"]
    - type: "restart_ghost"
      targets: ["edge"]
    - type: "verify"
```

## Project Structure

```
production-arl-setup/
├── arl_deployer/           # Main package
│   ├── __init__.py
│   ├── cli.py              # CLI commands
│   ├── models.py           # Data models
│   ├── ssh_manager.py      # SSH operations
│   ├── state.py            # State management
│   ├── config_discovery.py # Config search
│   ├── deployment.py       # Deployment logic
│   ├── verification.py     # Verification logic
│   └── rules.py            # Rule engine
├── xml-manipulator/        # XML manipulation library
├── config.yaml             # Main configuration
├── rules.yaml              # Self-healing rules
├── setup.py                # Package setup
├── requirements.txt        # Dependencies
├── pyproject.toml          # Modern packaging config
└── README.md               # This file
```

## License

MIT License - Trident QA Team

## Contributing

1. Follow existing code style (Black formatter)
2. Add tests for new features
3. Update documentation
4. Ensure all tests pass

## Support

For issues or questions, contact the Trident QA team.
