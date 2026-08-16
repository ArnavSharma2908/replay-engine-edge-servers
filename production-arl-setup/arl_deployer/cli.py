#!/usr/bin/env python3
"""
Command-line interface for ARL Deployer.

This module provides the main CLI commands for deploying, verifying,
fixing, and rolling back ARL deployments.
"""

import logging
import sys
import uuid
from pathlib import Path
from typing import Optional
from datetime import datetime

import click
import yaml

from arl_deployer.models import (
    DeploymentConfig,
    DeploymentStatus,
    MachineConfig,
    MachineType,
    ARLFileInfo,
)
from arl_deployer.ssh_manager import SSHManager
from arl_deployer.state import StateManager
from arl_deployer.config_discovery import ConfigDiscovery
from arl_deployer.deployment import Deployer
from arl_deployer.verification import Verifier
from arl_deployer.rules import RuleEngine
from arl_deployer.logger import get_logger

# Get beautiful logger instance
logger = None


def load_config(config_file: str = 'config.yaml') -> dict:
    """Load configuration from YAML file."""
    try:
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        click.echo(f"Error: Configuration file not found: {config_file}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error loading configuration: {e}", err=True)
        sys.exit(1)


@click.group()
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging with detailed output')
@click.version_option(version='0.1.0')
@click.pass_context
def cli(ctx, verbose):
    """
    ARL Deployer - Automated Trident customer configuration deployment tool.

    Deploy customer ARL configurations to edge, parent, and peer machines
    with automatic verification and self-healing capabilities.
    """
    # Ensure context object exists
    ctx.ensure_object(dict)
    ctx.obj['verbose'] = verbose

    # Configure logging based on verbose mode
    if verbose:
        # Verbose mode: show all logs including libraries
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    else:
        # Non-verbose mode: suppress library logs, only show WARNING and above
        logging.basicConfig(
            level=logging.CRITICAL,  # Even stricter - only critical errors
            format='%(message)s'
        )
        # Specifically suppress noisy libraries
        logging.getLogger('paramiko').setLevel(logging.CRITICAL)
        logging.getLogger('paramiko.transport').setLevel(logging.CRITICAL)
        logging.getLogger('urllib3').setLevel(logging.CRITICAL)
        logging.getLogger('requests').setLevel(logging.CRITICAL)

        # Suppress paramiko warnings about exceptions
        import warnings
        warnings.filterwarnings('ignore', category=ResourceWarning)

        # Suppress tracebacks in non-verbose mode
        def custom_excepthook(exc_type, exc_value, exc_traceback):
            if issubclass(exc_type, KeyboardInterrupt):
                sys.__excepthook__(exc_type, exc_value, exc_traceback)
                return
            # Only show the error message, not the traceback
            click.echo(f"Error: {exc_value}", err=True)

        sys.excepthook = custom_excepthook

    # Set global verbose mode
    from arl_deployer.logger import set_verbose
    set_verbose(verbose)

    # Initialize global logger
    global logger
    logger = get_logger(__name__)


@cli.command()
@click.option('--cpcode', help='Customer CPCode to search for')
@click.option('--hostname', multiple=True, help='Hostname(s) to add to arlindex (can specify multiple with --hostname host1 --hostname host2)')
@click.option('--arl-file', help='Direct path to ARL file (bypasses search)')
@click.option('--arl-id', help='ARL ID to lookup in arlindex.xml on config server')
@click.option('--edge', required=True, help='Edge machine IP address')
@click.option('--parent', required=True, help='Parent machine IP address')
@click.option('--peer', multiple=True, help='Peer machine IP address (can specify multiple)')
@click.option('--origin-host', help='Origin host for origin server configuration (used by rules)')
@click.option('--sample-url', help='Sample URL for verification')
@click.option('--config', default='config.yaml', help='Configuration file path')
@click.option('--no-verify', is_flag=True, help='Skip verification after deployment')
@click.option('--no-heal', is_flag=True, help='Skip self-healing if verification fails')
def deploy(cpcode, hostname, arl_file, arl_id, edge, parent, peer, origin_host, sample_url, config, no_verify, no_heal):
    """
    Deploy ARL configuration to edge, parent, and peer machines.

    Examples:

      \b
      # Deploy by CPCode and hostname
      arl-deploy deploy --cpcode 1608524 --hostname hubdoc-app.xero-uat.com \\
                        --edge 198.18.134.246 --parent 198.18.134.247

      \b
      # Deploy by ARL file
      arl-deploy deploy --arl-file ARL_hubdoc-app.xero.com.1042719.xml \\
                        --edge 198.18.134.246 --parent 198.18.134.247 \\
                        --peer 198.18.134.248

      \b
      # Deploy by ARL ID (looks up in arlindex.xml on config server)
      arl-deploy deploy --arl-id 1257397 \\
                        --edge 198.18.134.246 --parent 198.18.134.247
    """
    logger.step("ARL Deployment")

    # Validate inputs
    if not cpcode and not arl_file and not arl_id:
        logger.error("Must provide either --cpcode, --arl-file, or --arl-id")
        sys.exit(1)

    if cpcode and not hostname and not arl_file and not arl_id:
        logger.warning("CPCode provided without hostname. May fail if multiple matches found.")
    
    # Load configuration
    cfg = load_config(config)
    
    # Initialize managers
    ssh_config = cfg.get('ssh', {})
    ssh_key_path = ssh_config.get('ssh_key_path', '/root/.ssh/identity')
    ssh_manager = SSHManager(
        connect_timeout=ssh_config.get('connect_timeout', 10),
        command_timeout=ssh_config.get('command_timeout', 30),
        transfer_timeout=ssh_config.get('transfer_timeout', 300),
        max_retries=ssh_config.get('max_retries', 3),
        retry_delay=ssh_config.get('retry_delay', 5)
    )
    
    deployment_cfg = cfg.get('deployment', {})
    state_manager = StateManager(
        state_dir=deployment_cfg.get('state_dir', '.deployment_state'),
        backup_suffix=deployment_cfg.get('backup_suffix', '.back')
    )
    
    try:
        # Step 1: Pre-flight connectivity checks
        logger.substep("[1/6] Pre-flight connectivity checks", status='running')

        edge_machine = MachineConfig(
            hostname=edge,
            ip_address=edge,
            machine_type=MachineType.EDGE,
            ssh_key_path=ssh_key_path
        )

        parent_machine = MachineConfig(
            hostname=parent,
            ip_address=parent,
            machine_type=MachineType.PARENT,
            ssh_key_path=ssh_key_path
        )

        peer_machines = [
            MachineConfig(
                hostname=p,
                ip_address=p,
                machine_type=MachineType.PEER,
                ssh_key_path=ssh_key_path
            )
            for p in peer
        ]

        # Check edge
        success, msg = ssh_manager.check_connectivity(edge_machine)
        if success:
            logger.substep(msg, status='success')
        else:
            logger.substep(msg, status='failure')
            sys.exit(1)

        # Check parent
        success, msg = ssh_manager.check_connectivity(parent_machine)
        if success:
            logger.substep(msg, status='success')
        else:
            logger.substep(msg, status='failure')
            sys.exit(1)

        # Check peers
        for peer_machine in peer_machines:
            success, msg = ssh_manager.check_connectivity(peer_machine)
            if success:
                logger.substep(msg, status='success')
            else:
                logger.substep(msg, status='failure')
            # Don't fail on peer connectivity issues

        # Step 2: Discover/load ARL configuration
        logger.substep("[2/6] Discovering ARL configuration", status='running')

        config_discovery = ConfigDiscovery(ssh_manager)

        # Setup config server connection (used by all search methods)
        config_server_cfg = cfg.get('config_server', {})
        config_server = MachineConfig(
            hostname=config_server_cfg.get('hostname'),
            ip_address=config_server_cfg.get('hostname'),
            machine_type=MachineType.CONFIG_SERVER,
            ssh_key_path=config_server_cfg.get('ssh_key_path', '/root/.ssh/identity')
        )
        arl_data_dir = config_server_cfg.get('directories', {}).get('arl_data', '')

        if arl_id:
            # Search by ARL ID in arlindex.xml
            logger.info(f"Searching for ARL ID: {arl_id}")

            try:
                arl_info = config_discovery.find_arl_by_arlid(
                    config_server,
                    arl_data_dir,
                    arl_id
                )
            except Exception as e:
                logger.error(f"Failed to find ARL ID {arl_id}: {str(e).split(':')[-1].strip()}")
                sys.exit(1)

        elif arl_file:
            # Direct ARL file specified
            logger.info(f"Using ARL file: {arl_file}")

            # Check if it's a local file or needs to be fetched
            if Path(arl_file).exists():
                arl_info = ARLFileInfo(
                    filename=Path(arl_file).name,
                    cpcode=cpcode or "unknown",
                    hostnames=[hostname] if hostname else [],
                    local_path=arl_file
                )
            else:
                # Try to fetch from config server
                arl_info = config_discovery.get_arl_by_filename(config_server, arl_data_dir, arl_file)

        else:
            # Search by CPCode
            logger.info(f"Searching for CPCode: {cpcode}")
            if hostname:
                logger.info(f"Filtering by hostname: {hostname}")

            arl_info = config_discovery.find_arl_by_cpcode(
                config_server,
                arl_data_dir,
                cpcode,
                hostname
            )

        if not arl_info:
            logger.error("ARL configuration not found")
            sys.exit(1)

        logger.substep(f"Found ARL: {arl_info.filename}", status='success')
        
        # Resolve sample URL if not provided
        if not sample_url:
            logger.info("Resolving sample URL from traffic profile...")
            traffic_profile_cfg = cfg.get('sample_url_sources', [{}])[0]
            traffic_profile_path = traffic_profile_cfg.get('path', 'output/traffic_profile.json')

            sample_url = config_discovery.extract_sample_url_from_traffic_profile(
                traffic_profile_path,
                hostname or arl_info.hostnames[0] if arl_info.hostnames else '',
                arl_info.cpcode
            )

            if not sample_url and arl_info.hostnames:
                # Fallback: construct simple URL
                sample_url = f"https://{arl_info.hostnames[0]}/"
                logger.info(f"Using fallback sample URL: {sample_url}")

        # Step 3: Create deployment configuration
        logger.substep("[3/6] Creating deployment configuration", status='running')
        
        deployment_id = f"deploy_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:8]}"

        # Convert hostname tuple to list
        hostnames_list = list(hostname) if hostname else []

        deploy_config = DeploymentConfig(
            deployment_id=deployment_id,
            arl_file=arl_info,
            hostname=hostnames_list[0] if hostnames_list else None,  # Keep first for backwards compatibility
            hostnames=hostnames_list,  # All hostnames
            sample_url=sample_url,
            origin_host=origin_host,  # Origin host for forward configuration
            edge=edge_machine,
            parent=parent_machine,
            peers=peer_machines,
            config_server=config_server if 'config_server' in locals() else None,
            create_backups=deployment_cfg.get('create_backups', True),
            auto_rollback_on_failure=deployment_cfg.get('auto_rollback_on_failure', False),
            max_healing_attempts=deployment_cfg.get('max_healing_attempts', 3)
        )
        
        logger.substep(f"Deployment ID: {deployment_id}", status='success')

        # Create deployment state
        deploy_state = state_manager.create_deployment(deploy_config)

        # Step 4: Execute deployment
        logger.substep("[4/6] Executing deployment", status='running')

        ghost_config = cfg.get('ghost', {})
        deployer = Deployer(ssh_manager, state_manager, ghost_config, cfg)

        success = deployer.deploy_full(deploy_config, deploy_state)

        if not success:
            logger.failure("Deployment failed")
            if deploy_config.auto_rollback_on_failure:
                logger.warning("Auto-rollback enabled, rolling back...")
                state_manager.rollback_deployment(ssh_manager, deploy_state)
            sys.exit(1)

        logger.substep("Deployment completed", status='success')

        # Step 5: Verification
        if not no_verify:
            logger.substep("[5/6] Verifying deployment", status='running')
            
            if not sample_url:
                logger.warning("No sample URL available, skipping verification")
            else:
                verification_cfg = cfg.get('verification', {})
                verifier = Verifier(
                    ssh_manager,
                    log_flush_wait=verification_cfg.get('log_flush_wait', 5),
                    log_tail_lines=verification_cfg.get('log_tail_lines', 100),
                    curl_timeout=verification_cfg.get('curl_timeout', 30)
                )

                access_log_path = ghost_config.get('access_log_path', '/a/logs/ghost.access.log')
                result = verifier.verify_deployment(deploy_config, sample_url, access_log_path)

                deploy_state.verification_result = result
                state_manager.save_state(deploy_state)

                if result.passed:
                    logger.substep(f"Verification passed - Forward IP: {result.actual_forward_ip}", status='success')
                else:
                    logger.substep(f"Verification failed - {result.failure_reason}", status='failure')

                    # Show detailed log lines for debugging
                    if result.f_line_data:
                        logger.info(f"F-line: {result.f_line_data.raw_line}")
                    else:
                        logger.warning("No f-line found in ghost.access.log")

                    if result.r_line_data:
                        logger.info(f"R-line: {result.r_line_data.raw_line}")
                    else:
                        logger.warning("No r-line found in ghost.access.log")

                # Step 6: Self-healing if verification failed
                if not result.passed and not no_heal:
                    logger.substep("[6/6] Self-healing", status='running')
                    
                    rule_engine = RuleEngine(
                        ssh_manager,
                        verifier,
                        rules_file='rules.yaml',
                        ghost_config=ghost_config
                    )
                    
                    healed = rule_engine.execute_healing(deploy_config, deploy_state, result)

                    if healed:
                        logger.success("Self-healing successful, deployment verified")
                    else:
                        logger.failure("Self-healing failed, manual intervention required")
                        sys.exit(2)
                elif not result.passed:
                    logger.failure("Verification failed (self-healing disabled)")
                    sys.exit(2)

        print()
        logger.success(f"Deployment completed successfully - ID: {deployment_id}")

    finally:
        ssh_manager.close_all()


@cli.command()
@click.option('--edge', required=True, help='Edge machine IP address')
@click.option('--parent', required=True, help='Parent machine IP address')
@click.option('--sample-url', required=True, help='Sample URL to test')
@click.option('--config', default='config.yaml', help='Configuration file path')
def verify(edge, parent, sample_url, config):
    """
    Verify an existing deployment by testing a request.
    
    Example:
    
      \b
      arl-deploy verify --edge 198.18.134.246 --parent 198.18.134.247 \\
                        --sample-url "https://hubdoc-app.xero-uat.com/"
    """
    click.echo("Running verification...")
    
    cfg = load_config(config)
    
    # Get SSH key path from config
    ssh_key_path = cfg.get('ssh', {}).get('ssh_key_path', '/root/.ssh/identity')
    
    # Initialize managers
    ssh_config = cfg.get('ssh', {})
    ssh_manager = SSHManager(
        connect_timeout=ssh_config.get('connect_timeout', 10),
        command_timeout=ssh_config.get('command_timeout', 30)
    )
    
    try:
        edge_machine = MachineConfig(
            hostname=edge,
            ip_address=edge,
            machine_type=MachineType.EDGE,
            ssh_key_path=ssh_key_path
        )
        
        parent_machine = MachineConfig(
            hostname=parent,
            ip_address=parent,
            machine_type=MachineType.PARENT,
            ssh_key_path=ssh_key_path
        )
        
        # Create minimal deployment config for verification
        deploy_config = DeploymentConfig(
            deployment_id="verify_" + str(uuid.uuid4())[:8],
            arl_file=ARLFileInfo(filename="", cpcode="", hostnames=[]),
            sample_url=sample_url,
            edge=edge_machine,
            parent=parent_machine
        )
        
        verification_cfg = cfg.get('verification', {})
        ghost_config = cfg.get('ghost', {})
        
        verifier = Verifier(
            ssh_manager,
            log_flush_wait=verification_cfg.get('log_flush_wait', 5),
            log_tail_lines=verification_cfg.get('log_tail_lines', 100),
            curl_timeout=verification_cfg.get('curl_timeout', 30)
        )
        
        access_log_path = ghost_config.get('access_log_path', '/a/logs/ghost.access.log')
        result = verifier.verify_deployment(deploy_config, sample_url, access_log_path)
        
        click.echo(f"\n{result}")
        
        if result.passed:
            sys.exit(0)
        else:
            sys.exit(1)
            
    finally:
        ssh_manager.close_all()


@cli.command()
@click.option('--cpcode', help='Customer CPCode (for rule context)')
@click.option('--hostname', help='Hostname (for rule context and verification)')
@click.option('--arl-file', help='ARL file name (for rule context)')
@click.option('--origin-host', help='Origin host for origin server configuration (used by rules)')
@click.option('--edge', required=True, help='Edge machine IP address')
@click.option('--parent', required=True, help='Parent machine IP address')
@click.option('--sample-url', required=True, help='Sample URL to test')
@click.option('--peer', multiple=True, help='Peer machine IP address (can specify multiple)')
@click.option('--max-attempts', default=3, type=int, help='Maximum healing attempts (default: 3)')
@click.option('--config', default='config.yaml', help='Configuration file path')
@click.option('--rules-file', default='rules.yaml', help='Rules file path')
def fix(cpcode, hostname, arl_file, origin_host, edge, parent, sample_url, peer, max_attempts, config, rules_file):
    """
    Verify deployment and automatically fix issues using self-healing rules.

    This command runs verification and if it fails, applies self-healing rules
    to automatically fix common issues like missing dependencies, configuration
    errors, and Ghost process problems.

    Examples:

      \b
      # Basic verify and auto-fix
      arl-deploy fix --edge 198.18.134.246 --parent 198.18.134.247 \\
                     --sample-url "https://hubdoc-app.xero-uat.com/test"

      \b
      # With hostname and origin-host (required for some rules)
      arl-deploy fix --hostname hubdoc-app.xero.com \\
                     --origin-host origin.xero.com \\
                     --edge 198.18.134.246 --parent 198.18.134.247 \\
                     --sample-url "https://hubdoc-app.xero.com/test"

      \b
      # With ARL file context for rules
      arl-deploy fix --arl-file ARL_hubdoc-app.xero.com.1042719.xml \\
                     --cpcode 1042719 --hostname hubdoc-app.xero.com \\
                     --edge 198.18.134.246 --parent 198.18.134.247 \\
                     --sample-url "https://hubdoc-app.xero.com/test"

      \b
      # With peer machines and custom max attempts
      arl-deploy fix --edge 198.18.134.246 --parent 198.18.134.247 \\
                     --peer 198.18.134.248 --peer 198.18.134.249 \\
                     --sample-url "https://example.com/" --max-attempts 5
    """
    click.echo("=" * 70)
    click.echo("ARL Deployer - Verify and Fix")
    click.echo("=" * 70)

    cfg = load_config(config)

    # Get SSH key path from config
    ssh_key_path = cfg.get('ssh', {}).get('ssh_key_path', '/root/.ssh/identity')

    # Initialize managers
    ssh_config = cfg.get('ssh', {})
    ssh_manager = SSHManager(
        connect_timeout=ssh_config.get('connect_timeout', 10),
        command_timeout=ssh_config.get('command_timeout', 30)
    )

    ghost_config = cfg.get('ghost', {})
    deployment_cfg = cfg.get('deployment', {})
    state_manager = StateManager(
        state_dir=deployment_cfg.get('state_dir', '.deployment_state')
    )

    try:
        # Create machine configs
        edge_machine = MachineConfig(
            hostname=edge,
            ip_address=edge,
            machine_type=MachineType.EDGE,
            ssh_key_path=ssh_key_path
        )

        parent_machine = MachineConfig(
            hostname=parent,
            ip_address=parent,
            machine_type=MachineType.PARENT,
            ssh_key_path=ssh_key_path
        )

        peer_machines = []
        for peer_ip in peer:
            peer_machines.append(MachineConfig(
                hostname=peer_ip,
                ip_address=peer_ip,
                machine_type=MachineType.PEER,
                ssh_key_path=ssh_key_path
            ))

        # Create config server machine
        cs_cfg = cfg.get('config_server', {})
        config_server = MachineConfig(
            hostname=cs_cfg.get('hostname', ''),
            ip_address=cs_cfg.get('hostname', ''),
            machine_type=MachineType.CONFIG_SERVER,
            ssh_key_path=cs_cfg.get('ssh_key_path', ssh_key_path)
        )

        # Create ARL file info from provided options
        arl_info = ARLFileInfo(
            filename=arl_file or "",
            cpcode=cpcode or "",
            hostnames=[hostname] if hostname else []
        )

        # Create deployment config
        deploy_config = DeploymentConfig(
            deployment_id="fix_" + str(uuid.uuid4())[:8],
            arl_file=arl_info,
            hostname=hostname,
            sample_url=sample_url,
            origin_host=origin_host,
            edge=edge_machine,
            parent=parent_machine,
            peers=peer_machines,
            config_server=config_server,
            max_healing_attempts=max_attempts
        )

        # Create deployment state
        from arl_deployer.models import DeploymentState
        deploy_state = DeploymentState(config=deploy_config)

        # Initialize verifier
        verification_config = cfg.get('verification', {})
        verifier = Verifier(
            ssh_manager,
            log_flush_wait=verification_config.get('log_flush_wait', 5),
            log_tail_lines=verification_config.get('log_tail_lines', 100),
            curl_timeout=verification_config.get('curl_timeout', 30)
        )

        # Initialize rule engine
        rule_engine = RuleEngine(
            ssh_manager=ssh_manager,
            verifier=verifier,
            rules_file=rules_file,
            ghost_config=ghost_config
        )

        click.echo("\nStep 1: Running initial verification...")
        click.echo("-" * 70)

        result = verifier.verify_deployment(
            deploy_config,
            sample_url,
            ghost_config.get('access_log_path', '/a/logs/ghost.access.log')
        )

        if result.passed:
            click.echo("\n" + "=" * 70)
            click.echo("✓ Verification PASSED - No fixes needed")
            click.echo("=" * 70)
            return

        click.echo(f"\n✗ Verification FAILED: {result.failure_reason}")

        click.echo("\nStep 2: Applying self-healing rules...")
        click.echo("-" * 70)

        # Run self-healing
        healing_success = rule_engine.execute_healing(deploy_config, deploy_state, result)

        click.echo("\n" + "=" * 70)
        if healing_success:
            click.echo("✓ HEALING SUCCESSFUL")
            click.echo(f"  Total healing attempts: {deploy_state.healing_attempts}")
            click.echo(f"  Final verification: PASSED")
        else:
            click.echo("✗ HEALING FAILED")
            click.echo(f"  Total healing attempts: {deploy_state.healing_attempts}")
            if deploy_state.errors:
                click.echo("\n  Errors encountered:")
                for error in deploy_state.errors:
                    click.echo(f"    - {error}")
            click.echo("\n  Suggestions:")
            for fix in result.suggested_fixes:
                click.echo(f"    - {fix}")
            sys.exit(1)

        click.echo("=" * 70)

    except Exception as e:
        click.echo(f"\n✗ Error during fix: {e}", err=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        ssh_manager.close_all()


@cli.command()
@click.argument('deployment-id')
@click.option('--config', default='config.yaml', help='Configuration file path')
def rollback(deployment_id, config):
    """
    Rollback a deployment to its previous state.

    This will:
    1. Restore all backed up files
    2. Restart Ghost on all machines
    3. Mark deployment as rolled back

    Example:

      \b
      arl-deploy rollback deploy_20260617_143022_a1b2c3d4
    """
    click.echo(f"🔄 Rolling back deployment: {deployment_id}\n")

    cfg = load_config(config)

    deployment_cfg = cfg.get('deployment', {})
    ghost_cfg = cfg.get('ghost', {})
    ssh_config = cfg.get('ssh', {})

    state_manager = StateManager(
        state_dir=deployment_cfg.get('state_dir', '.deployment_state'),
        backup_suffix=deployment_cfg.get('backup_suffix', '.back')
    )

    # Get SSH key path from config
    ssh_key_path = ssh_config.get('ssh_key_path', '/root/.ssh/identity')

    # Load deployment state with full reconstruction
    state = state_manager.load_state_for_rollback(deployment_id, ssh_key_path=ssh_key_path)

    if not state:
        click.echo(f"❌ Error: Deployment not found or invalid: {deployment_id}", err=True)
        sys.exit(1)

    # Check if deployment can be rolled back
    if state.status == DeploymentStatus.ROLLED_BACK:
        click.echo(f"⚠️  Deployment already rolled back", err=True)
        sys.exit(1)

    if state.status == DeploymentStatus.NOT_STARTED:
        click.echo(f"⚠️  Deployment not started, nothing to rollback", err=True)
        sys.exit(1)

    # Initialize SSH manager
    ssh_manager = SSHManager(
        connect_timeout=ssh_config.get('connect_timeout', 10),
        command_timeout=ssh_config.get('command_timeout', 30),
        max_retries=ssh_config.get('max_retries', 3)
    )

    try:
        click.echo(f"Deployment status: {state.status.value}")
        click.echo(f"Backups to restore: {len(state.backups)}")
        click.echo(f"Machines: Edge={state.config.edge.ip_address}, Parent={state.config.parent.ip_address}\n")

        # Confirm rollback
        if not click.confirm("⚠️  This will restore backed up files and restart Ghost. Continue?"):
            click.echo("Rollback cancelled")
            return

        # Execute rollback
        click.echo("\n📦 Step 1: Restoring backed up files...")
        rollback_success = state_manager.rollback_deployment(ssh_manager, state)

        if not rollback_success:
            click.echo("⚠️  Some backups failed to restore (check logs)")

        click.echo("\n🔄 Step 2: Restarting Ghost on all machines...")
        restart_success = state_manager.restart_ghost_on_all_machines(
            ssh_manager,
            state,
            trident_run_path=ghost_cfg.get('trident_run', '/a/sbin/trident_run')
        )

        if not restart_success:
            click.echo("⚠️  Some Ghost restarts failed (check logs)")

        # Final status
        click.echo("\n" + "="*60)
        if rollback_success and restart_success:
            click.echo("✅ Rollback completed successfully!")
        elif rollback_success:
            click.echo("⚠️  Rollback completed with Ghost restart issues")
        else:
            click.echo("❌ Rollback completed with errors")

        click.echo(f"Status: {state.status.value}")
        click.echo("="*60)

    except Exception as e:
        click.echo(f"\n❌ Rollback failed: {e}", err=True)
        import traceback
        click.echo(traceback.format_exc(), err=True)
        sys.exit(1)
    finally:
        ssh_manager.close_all()


@cli.command()
@click.option('--config', default='config.yaml', help='Configuration file path')
def status(config):
    """List all deployments and their status."""
    cfg = load_config(config)
    
    deployment_cfg = cfg.get('deployment', {})
    state_manager = StateManager(
        state_dir=deployment_cfg.get('state_dir', '.deployment_state')
    )
    
    deployments = state_manager.list_deployments()
    
    if not deployments:
        click.echo("No deployments found")
        return
    
    click.echo(f"\nFound {len(deployments)} deployment(s):\n")
    
    for d in deployments:
        click.echo(f"  ID: {d.get('deployment_id')}")
        click.echo(f"  Status: {d.get('status')}")
        click.echo(f"  ARL: {d.get('arl_file')}")
        click.echo(f"  Start: {d.get('start_time')}")
        if d.get('end_time'):
            click.echo(f"  End: {d.get('end_time')}")
        click.echo()


@cli.command()
@click.argument('step', type=click.Choice([
    'copy-arl',
    'copy-hd-data',
    'copy-waf-data',
    'copy-pearl-data',
    'update-arlindex',
    'disable-forward-cert-verify',
    'add-dns-simulation',
    'restart-ghost',
    'check-ghost-status'
], case_sensitive=False))
@click.option('--machine', required=True, help='Target machine IP address')
@click.option('--machine-type', type=click.Choice(['edge', 'parent', 'peer']), required=True, help='Machine type')
@click.option('--arl-file', help='Path to ARL file (required for copy-arl, add-dns-simulation)')
@click.option('--parent-ip', help='Parent IP address (required for add-dns-simulation)')
@click.option('--hostname', help='Hostname for arlindex update')
@click.option('--config', default='config.yaml', help='Configuration file path')
def exec(step, machine, machine_type, arl_file, parent_ip, hostname, config):
    """
    Execute individual deployment steps on a specific machine.

    Available steps:
      copy-arl                      - Copy ARL file to machine
      copy-hd-data                  - Copy entire hd.data directory to machine
      copy-waf-data                 - Copy entire waf.data directory to machine
      copy-pearl-data               - Copy entire pearl.data directory to machine
      update-arlindex               - Update arlindex.xml with ARL file entry
      disable-forward-cert-verify   - Comment out ssl-forward-cert-verify-2 tags in ARL
      add-dns-simulation            - Add DNS simulation to ARL file (edge only)
      restart-ghost                 - Restart Ghost service
      check-ghost-status            - Check if Ghost is running and accepting connections

    Examples:

      \b
      # Copy HD data to edge machine
      arl-deploy exec copy-hd-data --machine 198.18.134.246 --machine-type edge

      \b
      # Copy ARL file to parent
      arl-deploy exec copy-arl --machine 198.18.134.247 --machine-type parent \\
                               --arl-file ARL_example.xml

      \b
      # Restart Ghost on edge
      arl-deploy exec restart-ghost --machine 198.18.134.246 --machine-type edge

      \b
      # Check Ghost status
      arl-deploy exec check-ghost-status --machine 198.18.134.246 --machine-type edge
    """
    click.echo(f"Executing step: {step} on {machine_type}@{machine}")
    click.echo("=" * 70)

    cfg = load_config(config)

    # Validate step-specific requirements
    if step in ['copy-arl', 'add-dns-simulation', 'disable-forward-cert-verify'] and not arl_file:
        click.echo(f"Error: --arl-file required for step '{step}'", err=True)
        sys.exit(1)

    if step == 'add-dns-simulation':
        if not parent_ip:
            click.echo(f"Error: --parent-ip required for step 'add-dns-simulation'", err=True)
            sys.exit(1)
        if machine_type != 'edge':
            click.echo(f"Warning: DNS simulation is typically only added to edge machines", err=True)

    if step == 'update-arlindex' and not (arl_file or hostname):
        click.echo(f"Error: --arl-file or --hostname required for step 'update-arlindex'", err=True)
        sys.exit(1)

    # Get SSH key path from config
    ssh_key_path = cfg.get('ssh', {}).get('ssh_key_path', '/root/.ssh/identity')

    # Initialize SSH manager
    ssh_config = cfg.get('ssh', {})
    ssh_manager = SSHManager(
        connect_timeout=ssh_config.get('connect_timeout', 10),
        command_timeout=ssh_config.get('command_timeout', 30)
    )

    # Initialize deployer
    ghost_config = cfg.get('ghost', {})
    state_manager = StateManager(
        state_dir=cfg.get('deployment', {}).get('state_dir', '.deployment_state')
    )
    deployer = Deployer(ssh_manager, state_manager, ghost_config, cfg)

    # Initialize verifier (for status checks)
    verification_config = cfg.get('verification', {})
    verifier = Verifier(
        ssh_manager,
        log_flush_wait=verification_config.get('log_flush_wait', 5),
        log_tail_lines=verification_config.get('log_tail_lines', 100),
        curl_timeout=verification_config.get('curl_timeout', 30)
    )

    # Create machine config
    machine_type_enum = MachineType[machine_type.upper()]
    machine_config = MachineConfig(
        hostname=machine,
        ip_address=machine,
        machine_type=machine_type_enum,
        ssh_key_path=ssh_key_path
    )

    # Create config server machine if needed
    config_server = None
    if step in ['copy-hd-data', 'copy-waf-data', 'copy-pearl-data']:
        cs_cfg = cfg.get('config_server', {})
        config_server = MachineConfig(
            hostname=cs_cfg.get('hostname', ''),
            ip_address=cs_cfg.get('hostname', ''),
            machine_type=MachineType.CONFIG_SERVER,
            ssh_key_path=cs_cfg.get('ssh_key_path', ssh_key_path)
        )

    try:
        # Execute the requested step
        if step == 'copy-arl':
            click.echo(f"Copying ARL file: {arl_file}")
            arl_filename = Path(arl_file).name
            remote_path = f"{ghost_config['arl_data_path']}/{arl_filename}"

            if ssh_manager.upload_file(machine_config, arl_file, remote_path):
                click.echo(f"✓ ARL file copied to {remote_path}")
            else:
                click.echo(f"✗ Failed to copy ARL file", err=True)
                sys.exit(1)

        elif step == 'copy-hd-data':
            click.echo("Copying hd.data directory...")

            # Create temporary deployment config
            from arl_deployer.models import DeploymentConfig, ARLFileInfo, DeploymentState
            temp_config = DeploymentConfig(
                deployment_id="exec_hd_data",
                arl_file=ARLFileInfo(filename="temp", cpcode="", hostnames=[]),
                edge=machine_config,
                parent=machine_config,
                config_server=config_server
            )
            temp_state = DeploymentState(config=temp_config)

            if deployer._copy_dependency_directory(
                temp_config,
                temp_state,
                machine_config,
                'hd_data',
                ghost_config['hd_data_path']
            ):
                click.echo(f"✓ HD data copied successfully")
            else:
                click.echo(f"✗ Failed to copy HD data", err=True)
                sys.exit(1)

        elif step == 'copy-waf-data':
            click.echo("Copying waf.data directory...")

            from arl_deployer.models import DeploymentConfig, ARLFileInfo, DeploymentState
            temp_config = DeploymentConfig(
                deployment_id="exec_waf_data",
                arl_file=ARLFileInfo(filename="temp", cpcode="", hostnames=[]),
                edge=machine_config,
                parent=machine_config,
                config_server=config_server
            )
            temp_state = DeploymentState(config=temp_config)

            if deployer._copy_dependency_directory(
                temp_config,
                temp_state,
                machine_config,
                'waf_data',
                ghost_config['waf_data_path']
            ):
                click.echo(f"✓ WAF data copied successfully")
            else:
                click.echo(f"✗ Failed to copy WAF data", err=True)
                sys.exit(1)

        elif step == 'copy-pearl-data':
            click.echo("Copying pearl.data directory...")

            from arl_deployer.models import DeploymentConfig, ARLFileInfo, DeploymentState
            temp_config = DeploymentConfig(
                deployment_id="exec_pearl_data",
                arl_file=ARLFileInfo(filename="temp", cpcode="", hostnames=[]),
                edge=machine_config,
                parent=machine_config,
                config_server=config_server
            )
            temp_state = DeploymentState(config=temp_config)

            if deployer._copy_dependency_directory(
                temp_config,
                temp_state,
                machine_config,
                'pearl_data',
                ghost_config['pearl_data_path']
            ):
                click.echo(f"✓ Pearl data copied successfully")
            else:
                click.echo(f"✗ Failed to copy Pearl data", err=True)
                sys.exit(1)

        elif step == 'update-arlindex':
            click.echo("Updating arlindex.xml...")

            from arl_deployer.models import ARLFileInfo, DeploymentState, DeploymentConfig
            arl_filename = Path(arl_file).name if arl_file else "unknown.xml"
            arl_info = ARLFileInfo(
                filename=arl_filename,
                cpcode="",
                hostnames=[hostname] if hostname else []
            )

            temp_config = DeploymentConfig(
                deployment_id="exec_arlindex",
                arl_file=arl_info,
                edge=machine_config,
                parent=machine_config
            )
            temp_state = DeploymentState(config=temp_config)

            if deployer._update_arlindex(machine_config, arl_info, hostname, None, temp_state):
                click.echo(f"✓ arlindex.xml updated")
            else:
                click.echo(f"✗ Failed to update arlindex.xml", err=True)
                sys.exit(1)

        elif step == 'disable-forward-cert-verify':
            click.echo("Disabling forward certificate verification in ARL file...")

            arl_filename = Path(arl_file).name
            remote_arl_path = f"{ghost_config['arl_data_path']}/{arl_filename}"

            from arl_deployer.models import DeploymentState, DeploymentConfig, ARLFileInfo
            temp_config = DeploymentConfig(
                deployment_id="exec_disable_cert_verify",
                arl_file=ARLFileInfo(filename=arl_filename, cpcode="", hostnames=[]),
                edge=machine_config,
                parent=machine_config
            )
            temp_state = DeploymentState(config=temp_config)

            if deployer._disable_forward_cert_verify(machine_config, remote_arl_path, temp_state):
                click.echo(f"✓ Forward cert verify disabled")
            else:
                click.echo(f"✗ Failed to disable forward cert verify", err=True)
                sys.exit(1)

        elif step == 'add-dns-simulation':
            click.echo(f"Adding DNS simulation to ARL file (parent IP: {parent_ip})...")

            arl_filename = Path(arl_file).name
            remote_arl_path = f"{ghost_config['arl_data_path']}/{arl_filename}"

            from arl_deployer.models import DeploymentState, DeploymentConfig, ARLFileInfo
            temp_config = DeploymentConfig(
                deployment_id="exec_dns_sim",
                arl_file=ARLFileInfo(filename=arl_filename, cpcode="", hostnames=[]),
                edge=machine_config,
                parent=machine_config
            )
            temp_state = DeploymentState(config=temp_config)

            if deployer._add_dns_simulation_to_arl(machine_config, remote_arl_path, parent_ip, temp_state):
                click.echo(f"✓ DNS simulation added")
            else:
                click.echo(f"✗ Failed to add DNS simulation", err=True)
                sys.exit(1)

        elif step == 'restart-ghost':
            click.echo("Restarting Ghost...")

            if deployer._restart_ghost(machine_config):
                click.echo(f"✓ Ghost restarted successfully")
            else:
                click.echo(f"✗ Failed to restart Ghost", err=True)
                sys.exit(1)

        elif step == 'check-ghost-status':
            click.echo("Checking Ghost status...")

            status = verifier.check_ghost_status(machine_config)

            click.echo(f"\nGhost Status on {machine}:")
            click.echo(f"  Binary exists: {'✓' if status['binary_exists'] else '✗'}")
            click.echo(f"  Process running: {'✓' if status['process_running'] else '✗'}")
            click.echo(f"  Accepting connections: {'✓' if status['accepting_connections'] else '✗'}")

            if not all(status.values()):
                sys.exit(1)

        click.echo("\n" + "=" * 70)
        click.echo(f"✓ Step '{step}' completed successfully")
        click.echo("=" * 70)

    except Exception as e:
        click.echo(f"\n✗ Step failed: {e}", err=True)
        sys.exit(1)
    finally:
        ssh_manager.close_all()


if __name__ == '__main__':
    cli()
