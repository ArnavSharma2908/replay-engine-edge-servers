#!/usr/bin/env python3
"""
Production Replay Orchestrator

This script orchestrates the complete end-to-end production replay workflow:
1. Runs production-replay module to generate traffic shards and profile
2. Deploys ARLs for all unique hostname-cpcode-arl combinations
3. Runs prewarm k6 scripts (placeholder)
4. Restarts webghost on all edge, peer, and parent machines
5. Executes the actual replay script with k6

Usage:
    python orchestrate.py --config orchestrator_config.json
    python orchestrate.py --edge 198.18.134.240 --parent 198.18.134.241 --peers 198.18.134.242,198.18.134.243
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from ghost_restart import GhostRestartManager
from api.replay_status import count_requests, write_status


# ANSI color codes
class Colors:
    """ANSI color codes for terminal output."""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'

    # Regular colors
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    CYAN = '\033[36m'
    MAGENTA = '\033[35m'
    GRAY = '\033[90m'

    # Bright colors
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'


# Unicode symbols for beautiful output
class Symbols:
    """Unicode symbols for beautiful CLI output."""
    CHECK = '✓'
    CROSS = '✗'
    WARNING = '⚠'
    ARROW = '→'
    BULLET = '•'
    CHEVRON = '›'

    # Box drawing
    BOX_TOP_LEFT = '╭'
    BOX_TOP_RIGHT = '╮'
    BOX_BOTTOM_LEFT = '╰'
    BOX_BOTTOM_RIGHT = '╯'
    BOX_HORIZONTAL = '─'
    BOX_VERTICAL = '│'


class OrchestratorLogger:
    """Beautiful logger for orchestrator with color support."""

    def __init__(self, verbose: bool = False, use_colors: bool = True):
        self.verbose = verbose
        self.use_colors = use_colors and sys.stdout.isatty()

        # Set up underlying Python logger for verbose mode
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG if verbose else logging.INFO)

        if verbose:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

    def parrot_banner(self):
        """Display a colorful ASCII parrot banner."""
        if not self.use_colors:
            # Simple text version
            print("\n" + "=" * 80)
            print("PARROT - Production AccessLog Replay Replay(:]) Orchestration Tool")
            print("=" * 80 + "\n")
            return

        # ASCII art for "PARROT" text
        parrot_text = [
            " ____   _    ____  ____   ___ _____ ",
            "|  _ \\ / \\  |  _ \\|  _ \\ / _ \\_   _|",
            "| |_) / _ \\ | |_) | |_) | | | || |  ",
            "|  __/ ___ \\|  _ <|  _ <| |_| || |  ",
            "|_| /_/   \\_\\_| \\_\\_| \\_\\\\___/ |_|  ",
        ]

        # Colorful ASCII art parrot with color coding
        # Format: (line, [(start, end, color), ...])
        parrot_lines = [
            ("  .-.-.", [(0, 8, Colors.BRIGHT_RED)]),
            ("    /  (o \\", [(0, 7, Colors.BRIGHT_RED), (7, 8, Colors.GRAY), (8, 11, Colors.BRIGHT_YELLOW)]),
            ("   |    )  |", [(0, 8, Colors.BRIGHT_RED), (8, 12, Colors.BRIGHT_YELLOW)]),
            ("    \\_ /  /", [(0, 4, Colors.BRIGHT_RED), (4, 11, Colors.BRIGHT_GREEN)]),
            ("     /   |", [(0, 10, Colors.BRIGHT_GREEN)]),
            ("    /    |", [(0, 10, Colors.BRIGHT_GREEN)]),
            (".           |     |       _", [(0, 30, Colors.BRIGHT_BLUE)]),
            ("             \\    \\_____// )", [(0, 30, Colors.BRIGHT_BLUE)]),
            ("             \\          /", [(0, 30, Colors.BRIGHT_BLUE)]),
            ("              `----.---'", [(0, 30, Colors.BRIGHT_BLUE)]),
            ("               ||", [(0, 30, Colors.BRIGHT_YELLOW)]),
            ("              _||_", [(0, 30, Colors.BRIGHT_YELLOW)]),
            ("              ((  ))", [(0, 30, Colors.BRIGHT_YELLOW)]),
            ("              ======", [(0, 30, Colors.YELLOW)]),
        ]

        print()
        # Print "PARROT" ASCII text
        for line in parrot_text:
            centered_line = line.center(90)
            print(self._colorize(centered_line, Colors.BRIGHT_CYAN, bold=True))

        print()

        # Print colorful parrot
        for line, color_ranges in parrot_lines:
            colored_line = ""
            last_end = 0
            for start, end, color in color_ranges:
                if start > last_end:
                    colored_line += line[last_end:start]
                colored_line += self._colorize(line[start:end], color, bold=True)
                last_end = end
            if last_end < len(line):
                colored_line += line[last_end:]

            # Center the colored line
            padding = (80 - len(line)) // 2
            print(" " * padding + colored_line)

        # Print subtitle
        print()
        subtitle = "Production AccessLog Replay(Replay :)) Orchestration Tool"
        print(self._colorize(subtitle.center(90), Colors.CYAN))
        print()
        print(self._colorize("=" * 90, Colors.BRIGHT_BLUE))
        print()

    def _colorize(self, text: str, color: str, bold: bool = False) -> str:
        """Add color to text if colors are enabled."""
        if not self.use_colors:
            return text
        prefix = Colors.BOLD if bold else ''
        return f"{prefix}{color}{text}{Colors.RESET}"

    def banner(self, message: str, subtitle: str = ""):
        """Log a beautiful banner for major sections."""
        width = 80
        if self.use_colors:
            print()
            print(self._colorize(f"{Symbols.BOX_TOP_LEFT}{Symbols.BOX_HORIZONTAL * (width-2)}{Symbols.BOX_TOP_RIGHT}", Colors.BRIGHT_BLUE, bold=True))
            msg_line = f"{Symbols.BOX_VERTICAL} {message.center(width-4)} {Symbols.BOX_VERTICAL}"
            print(self._colorize(msg_line, Colors.BRIGHT_BLUE, bold=True))
            if subtitle:
                sub_line = f"{Symbols.BOX_VERTICAL} {subtitle.center(width-4)} {Symbols.BOX_VERTICAL}"
                print(self._colorize(sub_line, Colors.CYAN))
            print(self._colorize(f"{Symbols.BOX_BOTTOM_LEFT}{Symbols.BOX_HORIZONTAL * (width-2)}{Symbols.BOX_BOTTOM_RIGHT}", Colors.BRIGHT_BLUE, bold=True))
            print()
        else:
            print(f"\n{'='*width}")
            print(message)
            if subtitle:
                print(subtitle)
            print(f"{'='*width}\n")

    def step(self, message: str, step_num: int = None, total_steps: int = None):
        """Log a major step heading with parrot icon."""
        if self.use_colors:
            print()
            if step_num and total_steps:
                step_prefix = self._colorize(f"[{step_num}/{total_steps}]", Colors.BRIGHT_YELLOW, bold=True)
                parrot_icon = "🦜"
                step_msg = self._colorize(f"{message}", Colors.BRIGHT_CYAN, bold=True)
                print(f"{step_prefix} {parrot_icon} {step_msg}")
            else:
                parrot_icon = "🦜"
                step_msg = self._colorize(f"{message}", Colors.BRIGHT_CYAN, bold=True)
                print(f"{parrot_icon} {step_msg}")
        else:
            if step_num and total_steps:
                print(f"\n[{step_num}/{total_steps}] 🦜 {message}")
            else:
                print(f"\n🦜 {message}")

    def success(self, message: str):
        """Log a success message."""
        if self.use_colors:
            symbol = self._colorize(Symbols.CHECK, Colors.BRIGHT_GREEN, bold=True)
            colored_msg = self._colorize(message, Colors.GREEN)
            print(f"{symbol} {colored_msg}")
        else:
            print(f"✓ {message}")

    def failure(self, message: str):
        """Log a failure message."""
        if self.use_colors:
            symbol = self._colorize(Symbols.CROSS, Colors.BRIGHT_RED, bold=True)
            colored_msg = self._colorize(message, Colors.RED)
            print(f"{symbol} {colored_msg}")
        else:
            print(f"✗ {message}")

    def warning(self, message: str):
        """Log a warning message."""
        if self.use_colors:
            symbol = self._colorize(Symbols.WARNING, Colors.BRIGHT_YELLOW, bold=True)
            colored_msg = self._colorize(message, Colors.YELLOW)
            print(f"{symbol} {colored_msg}")
        else:
            print(f"⚠ {message}")

        if self.verbose:
            self.logger.warning(message)

    def info(self, message: str):
        """Log an info message (shown only in verbose mode)."""
        if self.verbose:
            self.logger.info(message)
        elif self.use_colors:
            dim_msg = self._colorize(f"  {Symbols.BULLET} {message}", Colors.GRAY)
            print(dim_msg)
        else:
            print(f"  • {message}")

    def error(self, message: str):
        """Log an error message."""
        self.failure(message)
        if self.verbose:
            self.logger.error(message)

    def substep(self, message: str, status: Optional[str] = None):
        """Log a substep with optional status indicator."""
        indent = "  "

        if status == 'success':
            if self.use_colors:
                symbol = self._colorize(Symbols.CHECK, Colors.BRIGHT_GREEN)
                colored_msg = self._colorize(message, Colors.GREEN)
                print(f"{indent}{symbol} {colored_msg}")
            else:
                print(f"{indent}✓ {message}")
        elif status == 'failure':
            if self.use_colors:
                symbol = self._colorize(Symbols.CROSS, Colors.BRIGHT_RED)
                colored_msg = self._colorize(message, Colors.RED)
                print(f"{indent}{symbol} {colored_msg}")
            else:
                print(f"{indent}✗ {message}")
        elif status == 'running':
            if self.use_colors:
                symbol = self._colorize(Symbols.CHEVRON, Colors.CYAN)
                colored_msg = self._colorize(message, Colors.CYAN)
                print(f"{indent}{symbol} {colored_msg}")
            else:
                print(f"{indent}› {message}")
        else:
            if self.use_colors:
                symbol = self._colorize(Symbols.BULLET, Colors.GRAY)
                print(f"{indent}{symbol} {message}")
            else:
                print(f"{indent}• {message}")

    def divider(self):
        """Print a subtle divider line."""
        if self.use_colors:
            print(self._colorize(f"  {Symbols.BOX_HORIZONTAL * 76}", Colors.GRAY))
        else:
            print(f"  {'-' * 76}")


# Global logger instance
logger = None


@dataclass
class MachineConfig:
    """Configuration for Ghost machines"""
    edge: str
    parent: str
    peers: List[str]

    def all_machines(self) -> List[str]:
        """Return all machines in the deployment"""
        return [self.edge, self.parent] + self.peers


@dataclass
class ARLDeployment:
    """Configuration for a single ARL deployment"""
    hostnames: List[str]  # All hostnames for this ARL
    cpcode: str
    arl_id: str
    sample_urls: List[str]  # Sample URLs for each hostname
    origin_host: Optional[str] = None


class OrchestratorConfig:
    """Orchestrator configuration loaded from file or CLI"""

    def __init__(self, config_path: Optional[Path] = None, cli_args: Optional[dict] = None):
        self.config_path = config_path
        self.config = self._load_config(config_path, cli_args)

    def _load_config(self, config_path: Optional[Path], cli_args: Optional[dict]) -> dict:
        """Load configuration from file or use CLI arguments"""
        # Default configuration
        default_config = {
            "machines": {
                "edge": "",
                "parent": "",
                "peers": []
            },
            "paths": {
                "venv": ".venv",
                "production_replay_src": "src",
                "production_arl_setup": "production-arl-setup",
                "production_arl_setup_venv": ".venv",
                "production_arl_setup_config": "production-arl-setup/config.yaml",
                "output_dir": "output",
                "traffic_profile": "output/traffic_profile.json",
                "replay_script": "replay.js"
            },
            "ssh": {
                "ssh_key_path": "/Users/arnsharm/.ssh/identity",
                "connect_timeout": 30,
                "command_timeout": 60
            },
            "arl_setup": {
                "default_origin_host": None,
                "timeout": 3000
            },
            "prewarm": {
                "enabled": False,
                "command": "# Placeholder for prewarm k6 scripts - update this command when ready"
            },
            "replay": {
                "log_level": "requests",
                "shard_dir": "./output/shards"
            }
        }

        # Load from config file if provided
        if config_path and config_path.exists():
            logger.info(f"Loading orchestrator config from {config_path}")
            with open(config_path) as f:
                config = json.load(f)
        else:
            logger.info("Using CLI arguments for configuration")
            config = default_config

        # Override machine configuration with CLI arguments if provided
        if cli_args:
            if "edge" in cli_args and cli_args["edge"]:
                config["machines"]["edge"] = cli_args["edge"]
            if "parent" in cli_args and cli_args["parent"]:
                config["machines"]["parent"] = cli_args["parent"]
            if "peers" in cli_args and cli_args["peers"]:
                config["machines"]["peers"] = cli_args["peers"]

        return config

    @property
    def machines(self) -> MachineConfig:
        """Get machine configuration"""
        m = self.config["machines"]
        return MachineConfig(
            edge=m["edge"],
            parent=m["parent"],
            peers=m.get("peers", [])
        )

    @property
    def output_dir(self) -> Path:
        """Get output directory path"""
        return Path(self.config["paths"]["output_dir"])

    @property
    def traffic_profile_path(self) -> Path:
        """Get traffic profile JSON path"""
        return Path(self.config["paths"]["traffic_profile"])

    @property
    def replay_script_path(self) -> Path:
        """Get k6 replay script path"""
        return Path(self.config["paths"]["replay_script"])

    @property
    def shard_dir(self) -> Path:
        """Get shard directory path"""
        # Check if shard_dir is explicitly configured in replay section
        replay_config = self.config.get("replay", {})
        shard_dir_config = replay_config.get("shard_dir", "")

        # If shard_dir is relative (like "../output/shards" for k6), convert to absolute
        # For orchestrator, we want the actual directory where shards are generated
        if shard_dir_config:
            # Remove leading "../" if present (k6 relative path)
            shard_dir_cleaned = shard_dir_config.lstrip("../")
            return Path(shard_dir_cleaned)

        # Default to output_dir/shards
        return self.output_dir / "shards"


class ProductionReplayOrchestrator:
    """Orchestrates the complete production replay workflow"""

    TOTAL_STEPS = 5

    def __init__(self, config: OrchestratorConfig, steps_to_run: Optional[List[int]] = None):
        self.config = config
        self.arl_deployments: List[ARLDeployment] = []
        self.steps_to_run = steps_to_run or list(range(1, self.TOTAL_STEPS + 1))

    def run(self):
        """Execute the complete workflow"""
        logger.parrot_banner()

        # Show which steps will run
        if self.steps_to_run != list(range(1, self.TOTAL_STEPS + 1)):
            logger.info(f"Running steps: {', '.join(map(str, self.steps_to_run))}")
            logger.info(f"Skipping steps: {', '.join(map(str, set(range(1, self.TOTAL_STEPS + 1)) - set(self.steps_to_run)))}")
            print()

        try:
            # Step 1: Generate traffic shards and profile
            if 1 in self.steps_to_run:
                self.step1_generate_traffic()

            # Step 2: Parse traffic profile and deploy ARLs
            if 2 in self.steps_to_run:
                self.step2_deploy_arls()

            # Step 3: Run prewarm scripts (placeholder)
            if 3 in self.steps_to_run:
                self.step3_prewarm()

            # # Step 4: Restart webghost on all machines
            # if 4 in self.steps_to_run:
            #     self.step4_restart_webghost()

            # Step 5: Execute replay
            if 4 in self.steps_to_run:
                self.step5_execute_replay()

            # Success summary
            print()
            if logger.use_colors:
                success_msg = "🦜 ✓ Orchestration Completed Successfully! 🦜"
                print(logger._colorize("=" * 80, Colors.BRIGHT_GREEN, bold=True))
                print(logger._colorize(success_msg.center(80), Colors.BRIGHT_GREEN, bold=True))
                print(logger._colorize("All steps executed without errors".center(80), Colors.GREEN))
                print(logger._colorize("=" * 80, Colors.BRIGHT_GREEN, bold=True))
            else:
                print("=" * 80)
                print("🦜 ✓ Orchestration Completed Successfully! 🦜")
                print("All steps executed without errors")
                print("=" * 80)
            print()

        except KeyboardInterrupt:
            print()
            logger.warning("Orchestration interrupted by user (Ctrl+C)")
            logger.info("Exiting gracefully...")
            sys.exit(130)  # Standard exit code for SIGINT

        except Exception as e:
            logger.error(f"Orchestration failed: {e}")
            if logger.verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)

    def step1_generate_traffic(self):
        """Step 1: Run production-replay module to create output files"""
        logger.step("Generating Traffic Shards and Profile", step_num=1, total_steps=self.TOTAL_STEPS)

        cmd = [sys.executable, "-m", "src.main"]
        logger.substep(f"Command: {' '.join(cmd)}", status='running')

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.substep("Traffic generation failed", status='failure')
            logger.error(f"Exit code: {result.returncode}")
            logger.info(f"STDOUT: {result.stdout}")
            logger.info(f"STDERR: {result.stderr}")
            raise RuntimeError("Step 1 failed: Traffic generation")

        logger.substep("Traffic generation completed", status='success')

        # Verify output files exist
        if not self.config.traffic_profile_path.exists():
            logger.failure(f"Traffic profile not found: {self.config.traffic_profile_path}")
            raise RuntimeError(f"Traffic profile not found: {self.config.traffic_profile_path}")

        logger.success(f"Traffic profile created: {self.config.traffic_profile_path}")

        # Count shard files in shard directory
        shard_files = list(self.config.shard_dir.glob("shard_*.jsonl"))
        logger.info(f"Generated {len(shard_files)} shard files in {self.config.shard_dir}")

    def step2_deploy_arls(self):
        """Step 2: Parse traffic profile and deploy ARLs for unique ARL IDs only"""
        logger.step("Deploying ARLs for Unique ARL IDs", step_num=2, total_steps=self.TOTAL_STEPS)

        # Check if traffic profile exists
        if not self.config.traffic_profile_path.exists():
            logger.failure(f"Traffic profile not found: {self.config.traffic_profile_path}")
            raise RuntimeError(f"Traffic profile not found at {self.config.traffic_profile_path}")

        # Parse traffic profile
        logger.substep("Parsing traffic profile", status='running')
        with open(self.config.traffic_profile_path) as f:
            traffic_profile = json.load(f)

        # Try both case variations for backwards compatibility
        unique_hostnames = traffic_profile.get("unique_hostnames", traffic_profile.get("unique_hostnames", {}))
        logger.substep(f"Found {len(unique_hostnames)} hostname-cpcode entries", status='success')

        if not unique_hostnames:
            logger.warning("No unique hostnames found in traffic profile. Skipping ARL deployment.")
            return

        # Parse each entry and group by ARL ID (collect all hostnames for same ARL)
        arl_map = {}  # arl_id -> {hostnames: [], cpcodes: set(), sample_urls: []}
        for key, sample_url in unique_hostnames.items():
            parsed = self._parse_hostname_key(key)
            if parsed:
                hostname, cpcode, arl_id = parsed
                if arl_id not in arl_map:
                    arl_map[arl_id] = {
                        "hostnames": [],
                        "cpcodes": set(),
                        "sample_urls": [],
                        "arl_id": arl_id
                    }
                arl_map[arl_id]["hostnames"].append(hostname)
                arl_map[arl_id]["cpcodes"].add(cpcode)
                arl_map[arl_id]["sample_urls"].append(sample_url)

        # Build ARLDeployment objects with all hostnames
        self.arl_deployments = []
        for arl_id, data in arl_map.items():
            # All entries should have same cpcode, take first
            cpcode = list(data["cpcodes"])[0]

            origin_host = self._extract_origin_host(data["sample_urls"][0], data["hostnames"][0])

            deployment = ARLDeployment(
                hostnames=data["hostnames"],
                cpcode=cpcode,
                arl_id=arl_id,
                sample_urls=data["sample_urls"],
                origin_host=origin_host
            )
            self.arl_deployments.append(deployment)

        logger.info(f"Identified {len(self.arl_deployments)} unique ARL IDs to deploy")
        logger.divider()

        # Deploy each ARL
        machines = self.config.machines
        arl_setup_dir = Path(self.config.config["paths"]["production_arl_setup"])

        for idx, deployment in enumerate(self.arl_deployments, 1):
            hostnames_str = ", ".join(deployment.hostnames)
            logger.substep(
                f"[{idx}/{len(self.arl_deployments)}] Deploying ARL {deployment.arl_id} for {hostnames_str}",
                status='running'
            )
            self._deploy_single_arl(deployment, machines, arl_setup_dir)
            print()  # Add spacing between deployments

        logger.success(f"All {len(self.arl_deployments)} ARL deployments completed")

    def _parse_hostname_key(self, key: str) -> Optional[tuple]:
        """
        Parse hostname key format: "hostname-cpcode-arl_id"
        Example: "www.bsp.gov.ph-491490-321127"

        Returns:
            Tuple of (hostname, cpcode, arl_id) or None if invalid
        """
        # Split by '-' and take last two parts as cpcode and arl_id
        # Everything before is hostname
        parts = key.rsplit('-', 2)

        if len(parts) != 3:
            logger.warning(f"Invalid hostname key format: {key} (expected 'hostname-cpcode-arl_id')")
            return None

        hostname, cpcode, arl_id = parts

        # Validate that cpcode and arl_id are numeric
        if not (cpcode.isdigit() and arl_id.isdigit()):
            logger.warning(f"Invalid cpcode or arl_id for {key} (must be numeric)")
            return None

        return (hostname, cpcode, arl_id)

    def _extract_origin_host(self, sample_url: str, hostname: str) -> Optional[str]:
        """Extract origin host from sample URL or use default"""
        # For now, use the hostname from the URL as origin host
        # This can be overridden in config if needed
        default_origin = self.config.config.get("arl_setup", {}).get("default_origin_host")
        if default_origin:
            return default_origin

        # Extract hostname from URL
        match = re.match(r'https?://([^/]+)', sample_url)
        if match:
            return match.group(1)

        return hostname

    def _deploy_single_arl(self, deployment: ARLDeployment, machines: MachineConfig, arl_setup_dir: Path):
        """Deploy a single ARL using arl-deploy tool"""

        # Get paths from config
        arl_venv_path = self.config.config["paths"].get("production_arl_setup_venv", ".venv")
        arl_config_path = self.config.config["paths"].get("production_arl_setup_config", "production-arl-setup/config.yaml")

        # Convert to absolute paths
        arl_setup_dir = arl_setup_dir.resolve()
        activate_script = Path(arl_venv_path).resolve() / "bin" / "activate"
        config_file = Path(arl_config_path).resolve()

        # Build arl-deploy command
        cmd_parts = [
            "arl-deploy deploy",
            f"--config {config_file}",
            f"--arl-id {deployment.arl_id}",
            f"--edge {machines.edge}",
            f"--parent {machines.parent}",
        ]

        # Add all hostnames (supports multiple --hostname flags)
        for hostname in deployment.hostnames:
            cmd_parts.append(f"--hostname {hostname}")

        # Add peers if present
        if machines.peers:
            peers_str = ",".join(machines.peers)
            cmd_parts.append(f"--peers {peers_str}")

        # Add origin host
        if deployment.origin_host:
            cmd_parts.append(f"--origin-host {deployment.origin_host}")

        # Add sample URL (properly escaped) - use first one
        if deployment.sample_urls:
            cmd_parts.append(f'--sample-url "{deployment.sample_urls[0]}"')

        # Join the arl-deploy command
        arl_deploy_cmd = " ".join(cmd_parts)

        # Full command with source venv
        full_cmd = f"source {activate_script} && {arl_deploy_cmd}"

        # Always show the full command
        logger.info(f"Command: {arl_deploy_cmd}")
        print()  # Add spacing before arl-deploy output

        # Execute deployment - stream output to console in real-time
        result = subprocess.run(
            ["bash", "-c", full_cmd],
            capture_output=False,  # Let output stream to console
            text=True,
            timeout=600
        )

        print()  # Add spacing after arl-deploy output

        if result.returncode != 0:
            logger.substep(f"ARL {deployment.arl_id} deployment failed", status='failure')
            raise RuntimeError(f"ARL deployment failed for {', '.join(deployment.hostnames)}")

        logger.substep(f"ARL {deployment.arl_id} deployed successfully", status='success')

    def step3_prewarm(self):
        """Step 3: Run prewarm k6 scripts with Wave 1 and Wave 2"""
        logger.step("Running Prewarm K6 Scripts - Wave 1 & 2", step_num=3, total_steps=self.TOTAL_STEPS)

        machines = self.config.machines
        
        # Verify at least one machine is configured
        if not machines.edge and not machines.parent and not machines.peers:
            logger.warning("No machines configured for prewarm. Skipping.")
            return

        # ===== PRE-FLIGHT CHECK: Verify Ghost is running on all machines =====
        logger.substep("Pre-flight check: Verifying Ghost is running on all machines", status='running')
        logger.divider()
        
        ghost_ready = self._verify_ghost_ready(machines)
        if not ghost_ready:
            logger.failure("Ghost is not ready on one or more machines. Cannot proceed with prewarm.")
            raise RuntimeError("Ghost pre-flight check failed")
        
        logger.success("All machines passed Ghost pre-flight check")
        logger.divider()

        # Load traffic profile to extract unique hostnames
        if not self.config.traffic_profile_path.exists():
            logger.failure(f"Traffic profile not found: {self.config.traffic_profile_path}")
            raise RuntimeError(f"Traffic profile not found at {self.config.traffic_profile_path}")

        with open(self.config.traffic_profile_path) as f:
            traffic_profile = json.load(f)

        unique_hostnames = traffic_profile.get("unique_hostnames", {})
        if not unique_hostnames:
            logger.warning("No unique hostnames found in traffic profile. Skipping prewarm.")
            return

        # Extract unique hostnames (both from keys and URLs)
        unique_hosts = self._extract_unique_hosts(unique_hostnames)
        logger.info(f"Found {len(unique_hosts)} unique hostnames: {', '.join(sorted(unique_hosts)[:5])}")
        if len(unique_hosts) > 5:
            logger.info(f"  ... and {len(unique_hosts) - 5} more")

        # Build host mappings for each wave
        host_mappings_edge = self._build_host_mappings(unique_hosts, machines.edge)
        host_mappings_parent = self._build_host_mappings(unique_hosts, machines.edge) # All requests are going to edge
        host_mappings_peer = self._build_host_mappings(unique_hosts, machines.peers[0] if machines.peers else None)

        logger.divider()

        # Track results for all waves
        all_results = {}

        try:
            # ===== WAVE 1: Prewarm (edge, parent, peer in parallel) =====
            logger.substep("WAVE 1: Running prewarm k6 scripts in parallel", status='running')
            print()

            wave1_results = self._run_wave1(
                machines, unique_hosts,
                host_mappings_edge, host_mappings_parent, host_mappings_peer
            )
            all_results.update(wave1_results)

            # ===== Restart Ghost =====
            logger.substep("Restarting Ghost on all machines", status='running')
            self._restart_ghost_for_prewarm(machines)
            logger.substep("Ghost restart completed", status='success')
            print()

            # ===== WAVE 2: Single k6 run with edge_wave2 =====
            logger.substep("WAVE 2: Running edge_wave2.jsonl", status='running')
            print()

            wave2_results = self._run_wave2(machines, unique_hosts, host_mappings_edge)
            all_results.update(wave2_results)

        except KeyboardInterrupt:
            print()
            logger.warning("Prewarm interrupted by user (Ctrl+C)")
            # Report any partial results
            if all_results:
                self._report_prewarm_summary(all_results)
            raise

        # ===== Report Summary =====
        logger.divider()
        self._report_prewarm_summary(all_results)

        # ===== Parse Failures =====
        failed_runs = {k: v for k, v in all_results.items() if not v['passed'] and not v.get('skipped', False)}
        if failed_runs:
            logger.divider()
            self._parse_failed_runs(failed_runs)

    def _verify_ghost_ready(self, machines: MachineConfig, max_retries: int = 5) -> bool:
        """Verify Ghost is running and accepting connections on all machines. Restart if not ready."""
        ssh_key = self.config.config.get("ssh", {}).get("ssh_key_path", "/Users/umidha/.ssh/identity")
        all_ready = True

        # Check edge
        if machines.edge:
            logger.info(f"Checking Ghost on edge: {machines.edge}")
            if not self._check_ghost_on_machine(machines.edge, ssh_key):
                logger.warning(f"  Ghost not ready on edge: {machines.edge}. Attempting restart...")
                self._restart_webghost_on_machine(machines.edge, ssh_key)
                # Wait and retry after restart
                if not self._wait_for_ghost_ready(machines.edge, ssh_key, max_retries):
                    logger.substep(f"Ghost not ready on edge: {machines.edge}", status='failure')
                    all_ready = False
                else:
                    logger.substep(f"Ghost ready on edge: {machines.edge}", status='success')
            else:
                logger.substep(f"Ghost ready on edge: {machines.edge}", status='success')

        # Check parent
        if machines.parent:
            logger.info(f"Checking Ghost on parent: {machines.parent}")
            if not self._check_ghost_on_machine(machines.parent, ssh_key):
                logger.warning(f"  Ghost not ready on parent: {machines.parent}. Attempting restart...")
                self._restart_webghost_on_machine(machines.parent, ssh_key)
                # Wait and retry after restart
                if not self._wait_for_ghost_ready(machines.parent, ssh_key, max_retries):
                    logger.substep(f"Ghost not ready on parent: {machines.parent}", status='failure')
                    all_ready = False
                else:
                    logger.substep(f"Ghost ready on parent: {machines.parent}", status='success')
            else:
                logger.substep(f"Ghost ready on parent: {machines.parent}", status='success')

        # Check peers
        for peer in machines.peers:
            logger.info(f"Checking Ghost on peer: {peer}")
            if not self._check_ghost_on_machine(peer, ssh_key):
                logger.warning(f"  Ghost not ready on peer: {peer}. Attempting restart...")
                self._restart_webghost_on_machine(peer, ssh_key)
                # Wait and retry after restart
                if not self._wait_for_ghost_ready(peer, ssh_key, max_retries):
                    logger.substep(f"Ghost not ready on peer: {peer}", status='failure')
                    all_ready = False
                else:
                    logger.substep(f"Ghost ready on peer: {peer}", status='success')
            else:
                logger.substep(f"Ghost ready on peer: {peer}", status='success')

        return all_ready

    def _wait_for_ghost_ready(self, machine: str, ssh_key: str, max_retries: int = 5, retry_delay: int = 3) -> bool:
        """Wait for Ghost to be ready after restart with retry logic."""
        for attempt in range(1, max_retries + 1):
            logger.info(f"  Checking Ghost readiness on {machine} (attempt {attempt}/{max_retries})...")
            if self._check_ghost_on_machine(machine, ssh_key):
                logger.info(f"  ✓ Ghost is ready on {machine}")
                return True
            
            if attempt < max_retries:
                logger.info(f"  Waiting {retry_delay}s before retry...")
                time.sleep(retry_delay)
        
        logger.warning(f"  Ghost failed to become ready on {machine} after {max_retries} retries")
        return False

    def _check_ghost_on_machine(self, machine: str, ssh_key: str) -> bool:
        """Check if Ghost is running and accepting connections on a machine."""
        try:
            # Check if Ghost process is running
            ps_cmd = "ps aux | grep -E '[/]a/bin/ghost|[w]ebghost' | wc -l"
            ssh_cmd = [
                "ssh", "-F", "/dev/null", "-i", ssh_key,
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                f"root@{machine}",
                ps_cmd
            ]

            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15)
            process_count = int(result.stdout.strip())

            if process_count == 0:
                logger.info(f"  Ghost process not running on {machine}")
                return False

            # Check if Ghost is accepting connections (Port Stats)
            cache_log_cmd = "tail -100 /a/logs/cache.log | grep -i 'Ports Stats' | tail -1"
            ssh_cmd = [
                "ssh", "-F", "/dev/null", "-i", ssh_key,
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                f"root@{machine}",
                cache_log_cmd
            ]

            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15)
            port_stats = result.stdout.strip()

            if not port_stats:
                logger.info(f"  No port stats found in cache.log on {machine}")
                return False

            # Check if Ghost is accepting connections (not all zeros)
            if "accepts per port:0:0, 80:0, 443:0" in port_stats:
                logger.info(f"  Ghost not accepting connections on {machine}")
                return False

            logger.info(f"  ✓ Ghost is accepting connections on {machine}")
            return True

        except subprocess.TimeoutExpired:
            logger.warning(f"  Timeout checking Ghost on {machine}")
            return False
        except Exception as e:
            logger.warning(f"  Error checking Ghost on {machine}: {e}")
            return False

    def _extract_unique_hosts(self, unique_hostnames: Dict) -> set:
        """Extract unique hostnames from traffic profile."""
        unique_hosts = set()

        for key, url in unique_hostnames.items():
            # Extract hostname from key
            parts = key.rsplit('-', 2)  # hostname-cpcode-arl_id
            if len(parts) >= 1:
                hostname = parts[0]
                unique_hosts.add(hostname)

            # Extract hostname from URL
            match = re.match(r'https?://([^/]+)', url)
            if match:
                url_host = match.group(1)
                unique_hosts.add(url_host)

        return unique_hosts

    def _build_host_mappings(self, hosts: set, ip: Optional[str]) -> Dict[str, str]:
        """Build host-to-IP mappings for k6."""
        if not ip or not hosts:
            return {}
        return {host: ip for host in hosts}

    def _run_wave1(self, machines: MachineConfig, unique_hosts: set,
                   host_mappings_edge: Dict, host_mappings_parent: Dict,
                   host_mappings_peer: Dict) -> Dict[str, Dict]:
        """Run Wave 1: prewarm k6 scripts in parallel (edge, parent, peer)."""
        results = {}
        futures = {}

        with ThreadPoolExecutor(max_workers=3) as executor:
            # Edge Wave 1
            if machines.edge:
                wave_file = self.config.output_dir / "edge_wave1.jsonl"
                if wave_file.exists():
                    future = executor.submit(
                        self._run_k6_wave,
                        wave_name="edge-wave1",
                        wave_file=wave_file,
                        host_mappings=host_mappings_edge,
                        output_dir=self.config.output_dir
                    )
                    futures[future] = "edge-wave1"

            # Parent Wave 1
            # if machines.parent:
            #     wave_file = self.config.output_dir / "parent_wave1.jsonl"
            #     if wave_file.exists():
            #         future = executor.submit(
            #             self._run_k6_wave,
            #             wave_name="parent-wave1",
            #             wave_file=wave_file,
            #             host_mappings=host_mappings_parent,
            #             output_dir=self.config.output_dir
            #         )
            #         futures[future] = "parent-wave1"

            # # Peer Wave 1
            # if machines.peers:
            #     wave_file = self.config.output_dir / "peer_wave1.jsonl"
            #     if wave_file.exists():
            #         future = executor.submit(

            #             self._run_k6_wave,
            #             wave_name="peer-wave1",
            #             wave_file=wave_file,
            #             host_mappings=host_mappings_peer,
            #             output_dir=self.config.output_dir
            #         )
            #         futures[future] = "peer-wave1"

            # Wait for all futures to complete
            for future in as_completed(futures):
                wave_name = futures[future]
                try:
                    result = future.result()
                    results[wave_name] = result
                except Exception as e:
                    logger.error(f"Exception in {wave_name}: {e}")
                    results[wave_name] = {
                        'passed': False,
                        'error': str(e),
                        'wave_file': None,
                        'log_file': None
                    }

        return results

    def _run_wave2(self, machines: MachineConfig, unique_hosts: set,
                   host_mappings_edge: Dict) -> Dict[str, Dict]:
        """Run Wave 2: single k6 run with edge_wave2.jsonl."""
        results = {}

        if machines.edge:
            wave_file = self.config.output_dir / "edge_wave2.jsonl"
            if wave_file.exists():
                result = self._run_k6_wave(
                    wave_name="edge-wave2",
                    wave_file=wave_file,
                    host_mappings=host_mappings_edge,
                    output_dir=self.config.output_dir
                )
                results["edge-wave2"] = result
            else:
                logger.warning(f"edge_wave2.jsonl not found: {wave_file}")

        return results

    def _run_k6_wave(self, wave_name: str, wave_file: Path, host_mappings: Dict,
                     output_dir: Path) -> Dict:
        """Run a single k6 wave and capture results."""
        logger.substep(f"Running {wave_name}", status='running')

        # Verify wave file exists and count requests
        if not wave_file.exists():
            logger.warning(f"Wave file not found: {wave_file}")
            return {
                'passed': False,
                'error': f'Wave file not found: {wave_file}',
                'wave_file': str(wave_file),
                'log_file': None
            }

        try:
            request_count = self._count_requests_in_jsonl(wave_file)
        except Exception as e:
            logger.error(f"Error counting requests in {wave_file}: {e}")
            return {
                'passed': False,
                'error': f'Error counting requests: {e}',
                'wave_file': str(wave_file),
                'log_file': None
            }

        # Skip if no requests
        if request_count == 0:
            logger.info(f"  Requests: 0 - Skipping {wave_name} (no requests to run)")
            logger.substep(f"{wave_name} - Skipped (0 requests)", status='success')
            return {
                'passed': True,
                'skipped': True,
                'reason': 'No requests in wave file',
                'wave_file': str(wave_file),
                'log_file': None,
                'request_count': 0
            }

        # Calculate NUM_VUS (requests / 100, minimum 1)
        num_vus = max(1, request_count // 100)
        logger.info(f"  Requests: {request_count}, NUM_VUS: {num_vus}")

        # Create log file for this wave
        log_file = output_dir / f"k6_{wave_name}.log"

        # Build k6 command
        host_mappings_json = json.dumps(host_mappings) if host_mappings else "{}"

        cmd = [
            "k6", "run",
            f"--env", f"WAVE_FILE={wave_file}",
            f"--env", f"HOST_MAPPINGS={host_mappings_json}",
            f"--env", f"NUM_VUS={num_vus}",
            "verify-prewarm.js"
        ]

        logger.info(f"  Command: k6 run --env WAVE_FILE={wave_file} --env HOST_MAPPINGS='{host_mappings_json}' --env NUM_VUS={num_vus} verify-prewarm.js")

        # Execute k6 command with output capture
        try:
            with open(log_file, 'w') as log_f:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=3600  # 1 hour timeout
                )

                # Write both stdout and stderr to log file
                log_f.write(result.stdout)
                if result.stderr:
                    log_f.write("\n--- STDERR ---\n")
                    log_f.write(result.stderr)

            # Determine if passed or failed
            passed = result.returncode == 0

            if passed:
                logger.substep(f"{wave_name} - Passed", status='success')
            else:
                logger.substep(f"{wave_name} - Failed (exit code: {result.returncode})", status='failure')

            return {
                'passed': passed,
                'exit_code': result.returncode,
                'wave_file': str(wave_file),
                'log_file': str(log_file),
                'request_count': request_count,
                'num_vus': num_vus
            }

        except subprocess.TimeoutExpired:
            logger.substep(f"{wave_name} - Timeout", status='failure')
            return {
                'passed': False,
                'error': 'K6 execution timeout',
                'wave_file': str(wave_file),
                'log_file': str(log_file)
            }
        except Exception as e:
            logger.error(f"Error executing k6 for {wave_name}: {e}")
            return {
                'passed': False,
                'error': str(e),
                'wave_file': str(wave_file),
                'log_file': str(log_file)
            }

    def _count_requests_in_jsonl(self, file_path: Path) -> int:
        """
        Count the number of requests in a wave file.
        Wave files are single JSON objects with request_batches array.
        Format: {"request_batches": [{"requests": [...]}, ...]}
        """
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
            
            # Count total requests across all batches
            request_count = 0
            request_batches = data.get('request_batches', [])
            
            if isinstance(request_batches, list):
                for batch in request_batches:
                    if isinstance(batch, dict):
                        requests = batch.get('requests', [])
                        if isinstance(requests, list):
                            request_count += len(requests)
            
            return request_count
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Error parsing wave file {file_path}: {e}")
            return 0

    def _restart_ghost_for_prewarm(self, machines: MachineConfig):
        """Restart Ghost on all machines for prewarm."""
        ssh_key = self.config.config.get("ssh", {}).get("ssh_key_path", "/Users/umidha/.ssh/identity")
        
        # Restart on edge
        if machines.edge:
            logger.info(f"  Restarting Ghost on edge: {machines.edge}")
            self._restart_webghost_on_machine(machines.edge, ssh_key)

        # Restart on parent
        if machines.parent:
            logger.info(f"  Restarting Ghost on parent: {machines.parent}")
            self._restart_webghost_on_machine(machines.parent, ssh_key)

        # Restart on peers
        for peer in machines.peers:
            logger.info(f"  Restarting Ghost on peer: {peer}")
            self._restart_webghost_on_machine(peer, ssh_key)

    def _report_prewarm_summary(self, results: Dict[str, Dict]):
        """Report a summary of prewarm test results."""
        logger.substep("Prewarm Test Summary", status='running')
        print()

        passed_count = sum(1 for r in results.values() if r['passed'] and not r.get('skipped', False))
        skipped_count = sum(1 for r in results.values() if r.get('skipped', False))
        failed_count = len(results) - passed_count - skipped_count

        # Print results
        for wave_name, result in sorted(results.items()):
            if result.get('skipped', False):
                status_str = "⊘ Skipped"
                if logger.use_colors:
                    status_colored = logger._colorize("⊘ Skipped", Colors.BRIGHT_YELLOW)
                    print(f"  {wave_name.ljust(20)} - {status_colored} (0 requests)")
                else:
                    print(f"  {wave_name.ljust(20)} - {status_str} (0 requests)")
            elif result['passed']:
                status_str = "✓ Passed"
                if logger.use_colors:
                    status_colored = logger._colorize("✓ Passed", Colors.BRIGHT_GREEN)
                    print(f"  {wave_name.ljust(20)} - {status_colored}")
                else:
                    print(f"  {wave_name.ljust(20)} - {status_str}")
            else:
                status_str = "✗ Failed"
                if logger.use_colors:
                    status_colored = logger._colorize("✗ Failed", Colors.BRIGHT_RED)
                    print(f"  {wave_name.ljust(20)} - {status_colored}")
                else:
                    print(f"  {wave_name.ljust(20)} - {status_str}")

                if 'error' in result:
                    logger.info(f"    Error: {result['error']}")

        print()
        logger.info(f"Total: {len(results)} waves, {passed_count} passed, {skipped_count} skipped, {failed_count} failed")

        if failed_count == 0:
            if skipped_count > 0:
                logger.success(f"All {passed_count} active prewarm tests passed! ({skipped_count} skipped)")
            else:
                logger.success("All prewarm tests passed!")
        else:
            logger.warning(f"{failed_count} prewarm test(s) failed - see details above")

    def _parse_failed_runs(self, failed_runs: Dict[str, Dict]):
        """Parse failures from failed k6 runs using parse_k6_failures.py."""
        logger.substep("Parsing K6 Failures", status='running')

        for wave_name, result in failed_runs.items():
            # Skip waves that were skipped (0 requests)
            if result.get('skipped', False):
                continue

            if not result.get('log_file') or not Path(result['log_file']).exists():
                logger.info(f"  {wave_name}: No log file to parse")
                continue

            log_file = result['log_file']
            logger.info(f"  Parsing failures from {wave_name}: {log_file}")

            try:
                cmd = [sys.executable, "parse_k6_failures.py", log_file]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=60
                )

                if result.returncode == 0:
                    logger.info(f"    ✓ Parsed successfully")
                    if result.stdout:
                        # Print first few lines of output
                        lines = result.stdout.strip().split('\n')
                        for line in lines[:5]:
                            logger.info(f"      {line}")
                        if len(lines) > 5:
                            logger.info(f"      ... and {len(lines) - 5} more lines")
                else:
                    logger.warning(f"    Parse failed: {result.stderr}")

            except Exception as e:
                logger.warning(f"  Error parsing {wave_name}: {e}")


    def step4_restart_webghost(self):
        """Step 4: Restart webghost on all edge, peer, and parent machines"""
        logger.step("Restarting Webghost on All Machines", step_num=4, total_steps=self.TOTAL_STEPS)

        machines = self.config.machines.all_machines()
        logger.info(f"Restarting webghost on {len(machines)} machines")
        logger.divider()

        ssh_key = self.config.config.get("ssh", {}).get("ssh_key_path", "/Users/arnsharm/.ssh/identity")

        for idx, machine in enumerate(machines, 1):
            logger.substep(f"[{idx}/{len(machines)}] Restarting webghost on {machine}", status='running')
            self._restart_webghost_on_machine(machine, ssh_key)

        logger.success(f"Webghost restarted on all {len(machines)} machines")

    def _restart_webghost_on_machine(self, machine: str, ssh_key: str):
        """Restart webghost on a single machine using the unified GhostRestartManager."""

        def runner(cmd: str, timeout: int):
            ssh_cmd = [
                "ssh",
                "-F", "/dev/null",  # Ignore user SSH config file
                "-i", ssh_key,
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=30",
                f"root@{machine}",
                cmd,
            ]
            try:
                # Increase timeout to 120 seconds for Ghost restart commands
                ssh_timeout = max(timeout, 120)
                result = subprocess.run(
                    ssh_cmd, capture_output=True, text=True, timeout=ssh_timeout
                )
                return result.returncode, result.stdout, result.stderr
            except subprocess.TimeoutExpired as e:
                # Ensure stderr is a string, not bytes
                stderr_str = (e.stderr if isinstance(e.stderr, str) else (e.stderr.decode('utf-8', errors='replace') if e.stderr else ""))
                return 124, e.stdout or "", stderr_str + " [timeout]"

        manager = GhostRestartManager(runner, machine_label=machine, logger=logger)

        logger.info(f"Restarting webghost on {machine}")

        if not manager.restart():
            logger.substep(f"Restart failed on {machine}", status='failure')
            raise RuntimeError(f"Webghost restart failed on {machine}")

        logger.substep(f"Webghost restarted on {machine}", status='success')

    def step5_execute_replay(self):
        """Step 5: Execute the actual replay script with k6"""
        logger.step("Executing Replay Script with K6", step_num=5, total_steps=self.TOTAL_STEPS)

        # Count shard files to determine VUs
        logger.substep("Counting shard files", status='running')
        shard_files = list(self.config.shard_dir.glob("shard_*.jsonl"))
        num_vus = len(shard_files)

        if num_vus == 0:
            logger.failure(f"No shard files found in shard directory: {self.config.shard_dir}")
            raise RuntimeError(f"No shard files found for replay in {self.config.shard_dir}")

        logger.substep(f"Found {num_vus} shard files → TEST_VUS={num_vus}", status='success')

        # Load traffic profile to extract unique hostnames for host mappings
        logger.substep("Loading traffic profile for host mappings", status='running')
        if not self.config.traffic_profile_path.exists():
            logger.failure(f"Traffic profile not found: {self.config.traffic_profile_path}")
            raise RuntimeError(f"Traffic profile not found at {self.config.traffic_profile_path}")

        with open(self.config.traffic_profile_path) as f:
            traffic_profile = json.load(f)

        unique_hostnames = traffic_profile.get("unique_hostnames", {})
        if not unique_hostnames:
            logger.warning("No unique hostnames found in traffic profile. Proceeding without host mappings.")
            host_mappings = {}
        else:
            # Extract unique hostnames (both from keys and URLs)
            unique_hosts = self._extract_unique_hosts(unique_hostnames)
            logger.substep(f"Found {len(unique_hosts)} unique hostnames", status='success')
            
            # Build host mappings for edge server (all replay traffic goes to edge)
            machines = self.config.machines
            host_mappings = self._build_host_mappings(unique_hosts, machines.edge)
            
            if host_mappings:
                logger.info(f"Host mappings: {len(host_mappings)} hostnames → {machines.edge}")
            else:
                logger.warning("No host mappings created")

        replay_config = self.config.config.get("replay", {})
        log_level = replay_config.get("log_level", "requests")
        shard_dir = replay_config.get("shard_dir", "../output")

        # Build k6 command with host mappings
        host_mappings_json = json.dumps(host_mappings) if host_mappings else "{}"
        
        env_vars = {
            "TEST_VUS": str(num_vus),
            "LOG_LEVEL": log_level,
            "SHARD_DIR": shard_dir,
            "HOST_MAPPINGS": host_mappings_json
        }

        # Build k6 command - environment flags must come AFTER 'run'
        cmd = [
            "k6", "run",
            "-e", f"TEST_VUS={num_vus}",
            "-e", f"LOG_LEVEL={log_level}",
            "-e", f"SHARD_DIR={shard_dir}",
            "-e", f"HOST_MAPPINGS={host_mappings_json}",
            str(self.config.replay_script_path)
        ]

        logger.info(f"Environment: TEST_VUS={num_vus}, LOG_LEVEL={log_level}, HOST_MAPPINGS={host_mappings_json[:100]}{'...' if len(host_mappings_json) > 100 else ''}")
        logger.info(f"Command: k6 run -e TEST_VUS={num_vus} -e LOG_LEVEL={log_level} -e SHARD_DIR={shard_dir} -e HOST_MAPPINGS=<{len(host_mappings)} mappings> {self.config.replay_script_path.name}")
        logger.divider()

        # Set environment variables and execute
        import os
        env = os.environ.copy()
        env.update({k: v for k, v in env_vars.items()})
        status_file = self.config.output_dir / "replay_status.json"
        requests_total = count_requests(self.config.shard_dir)
        write_status(
            status_file,
            running=True,
            requests_replayed=0,
            requests_total=requests_total,
            pid=os.getpid(),
        )

        # Change to replay script directory
        replay_dir = self.config.replay_script_path.parent
        logger.substep(f"Executing k6 from {replay_dir}", status='running')

        try:
            print()  # Add spacing before k6 output
            result = subprocess.run(
                cmd,
                cwd=replay_dir,
                env=env,
                capture_output=False,  # Stream output to console
                text=True
            )
            print()  # Add spacing after k6 output
        except Exception:
            write_status(
                status_file,
                running=False,
                requests_replayed=0,
                requests_total=requests_total,
            )
            raise

        if result.returncode != 0:
            write_status(
                status_file,
                running=False,
                requests_replayed=0,
                requests_total=requests_total,
            )
            logger.failure(f"Replay execution failed with exit code {result.returncode}")
            raise RuntimeError("Step 5 failed: Replay execution")

        write_status(
            status_file,
            running=False,
            requests_replayed=requests_total,
            requests_total=requests_total,
        )
        logger.success("Replay execution completed successfully")


def main():
    parser = argparse.ArgumentParser(
        description="""Orchestrate production replay workflow

Steps:
  1. Generate Traffic Shards and Profile
  2. Deploy ARLs for Unique ARL IDs
  3. Run Prewarm K6 Scripts (placeholder)
  4. Restart Webghost on All Machines
  5. Execute Replay Script with K6
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all steps
  python orchestrate.py --config orchestrator_config.json

  # Run only steps 1 and 2 (generate traffic and deploy ARLs)
  python orchestrate.py --config orchestrator_config.json --steps 1,2

  # Skip steps 3 and 4 (skip prewarm and restart)
  python orchestrate.py --config orchestrator_config.json --skip-steps 3,4

  # Using CLI arguments
  python orchestrate.py --edge 198.18.134.240 --parent 198.18.134.241 --peers 198.18.134.242,198.18.134.243

  # Minimal setup (edge and parent only)
  python orchestrate.py --edge 198.18.134.240 --parent 198.18.134.241

  # Enable verbose logging
  python orchestrate.py --config orchestrator_config.json --verbose
        """
    )

    parser.add_argument(
        "--config",
        type=Path,
        help="Path to orchestrator configuration JSON file"
    )

    parser.add_argument(
        "--edge",
        type=str,
        help="Edge machine IP address"
    )

    parser.add_argument(
        "--parent",
        type=str,
        help="Parent machine IP address"
    )

    parser.add_argument(
        "--peers",
        type=str,
        help="Comma-separated list of peer machine IP addresses"
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging with detailed information"
    )

    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output"
    )

    parser.add_argument(
        "--steps",
        type=str,
        help="Comma-separated list of steps to run (e.g., '1,2' or '1,2,5')"
    )

    parser.add_argument(
        "--skip-steps",
        type=str,
        help="Comma-separated list of steps to skip (e.g., '3,4')"
    )

    args = parser.parse_args()

    # Initialize global logger
    global logger
    logger = OrchestratorLogger(verbose=args.verbose, use_colors=not args.no_color)

    # Validate arguments
    if not args.config and not (args.edge and args.parent):
        parser.error("Either --config or both --edge and --parent must be provided")

    # Validate step arguments
    if args.steps and args.skip_steps:
        parser.error("Cannot use both --steps and --skip-steps at the same time")

    # Parse steps to run
    steps_to_run = None
    if args.steps:
        try:
            steps_to_run = [int(s.strip()) for s in args.steps.split(',')]
            # Validate step numbers
            invalid_steps = [s for s in steps_to_run if s < 1 or s > 5]
            if invalid_steps:
                parser.error(f"Invalid step numbers: {invalid_steps}. Steps must be 1-5.")
        except ValueError:
            parser.error("--steps must be comma-separated integers (e.g., '1,2,5')")

    if args.skip_steps:
        try:
            skip_steps = [int(s.strip()) for s in args.skip_steps.split(',')]
            # Validate step numbers
            invalid_steps = [s for s in skip_steps if s < 1 or s > 5]
            if invalid_steps:
                parser.error(f"Invalid step numbers: {invalid_steps}. Steps must be 1-5.")
            # Calculate steps to run (all steps minus skipped ones)
            steps_to_run = [s for s in range(1, 6) if s not in skip_steps]
        except ValueError:
            parser.error("--skip-steps must be comma-separated integers (e.g., '3,4')")

    # Parse CLI arguments
    cli_args = {}
    if args.edge:
        cli_args["edge"] = args.edge
    if args.parent:
        cli_args["parent"] = args.parent
    if args.peers:
        cli_args["peers"] = [p.strip() for p in args.peers.split(",")]

    # Load configuration
    config = OrchestratorConfig(
        config_path=args.config,
        cli_args=cli_args
    )

    # Run orchestration
    orchestrator = ProductionReplayOrchestrator(config, steps_to_run)
    orchestrator.run()


if __name__ == "__main__":
    main()