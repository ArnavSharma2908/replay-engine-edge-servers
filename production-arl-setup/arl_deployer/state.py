"""
State management for deployments.

This module handles deployment state tracking, backup management, and rollback capabilities.
"""

import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from dataclasses import asdict

from arl_deployer.models import (
    DeploymentState,
    DeploymentConfig,
    DeploymentStatus,
    BackupInfo,
    MachineConfig,
)
from arl_deployer.ssh_manager import SSHManager

logger = logging.getLogger(__name__)


class StateManager:
    """
    Manages deployment state, backups, and rollback operations.
    
    Responsibilities:
    - Track deployment state and history
    - Manage file backups before modifications
    - Enable full rollback of deployments
    - Persist state to disk for recovery
    """
    
    def __init__(self, state_dir: str = ".deployment_state", backup_suffix: str = ".back"):
        """
        Initialize State Manager.
        
        Args:
            state_dir: Directory to store deployment state files
            backup_suffix: Suffix for backup files
        """
        self.state_dir = Path(state_dir)
        self.backup_suffix = backup_suffix
        
        # Ensure state directory exists
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"State Manager initialized with state dir: {self.state_dir}")
    
    def _get_state_file_path(self, deployment_id: str) -> Path:
        """Get the path to a deployment's state file."""
        return self.state_dir / f"{deployment_id}.json"
    
    def create_deployment(self, config: DeploymentConfig) -> DeploymentState:
        """
        Create a new deployment state.
        
        Args:
            config: Deployment configuration
            
        Returns:
            Newly created DeploymentState
        """
        state = DeploymentState(
            config=config,
            status=DeploymentStatus.NOT_STARTED,
            start_time=datetime.now()
        )
        
        # Save initial state
        self.save_state(state)
        
        logger.info(f"Created deployment state: {config.deployment_id}")
        return state
    
    def save_state(self, state: DeploymentState) -> bool:
        """
        Save deployment state to disk.
        
        Args:
            state: Deployment state to save
            
        Returns:
            True if successful
        """
        try:
            state_file = self._get_state_file_path(state.config.deployment_id)
            
            # Convert state to JSON
            state_data = state.to_dict()
            
            # Write to file
            with open(state_file, 'w') as f:
                json.dump(state_data, f, indent=2, default=str)
            
            logger.debug(f"Saved state to {state_file}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
            return False
    
    def load_state(self, deployment_id: str) -> Optional[Dict[str, Any]]:
        """
        Load deployment state from disk as a dictionary.

        Note: Returns dict instead of DeploymentState to avoid complex reconstruction.
        Use load_state_for_rollback() if you need full state reconstruction.

        Args:
            deployment_id: ID of deployment to load

        Returns:
            State dictionary if found, None otherwise
        """
        try:
            state_file = self._get_state_file_path(deployment_id)

            if not state_file.exists():
                logger.warning(f"State file not found: {state_file}")
                return None

            with open(state_file, 'r') as f:
                state_data = json.load(f)

            logger.debug(f"Loaded state from {state_file}")
            return state_data

        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            return None

    def load_state_for_rollback(
        self,
        deployment_id: str,
        ssh_key_path: str = None
    ) -> Optional[DeploymentState]:
        """
        Load and reconstruct full DeploymentState for rollback operations.

        This reconstructs all nested objects needed for rollback.

        Args:
            deployment_id: ID of deployment to load
            ssh_key_path: SSH key path to use for machines (from config)

        Returns:
            DeploymentState if found and valid, None otherwise
        """
        try:
            state_data = self.load_state(deployment_id)
            if not state_data:
                return None

            # Reconstruct config field by field
            # For rollback, we only need minimal config reconstruction
            from arl_deployer.models import MachineConfig, MachineType, ARLFileInfo

            # Use provided SSH key or default
            ssh_key = ssh_key_path or '/root/.ssh/identity'

            # Parse machine strings (format: "type@ip")
            def parse_machine_str(machine_str: str, machine_type: MachineType) -> Optional[MachineConfig]:
                try:
                    # Format is "type@ip"
                    parts = machine_str.split('@')
                    if len(parts) == 2:
                        ip = parts[1]
                        return MachineConfig(
                            hostname=ip,
                            ip_address=ip,
                            machine_type=machine_type,
                            username='root',
                            ssh_key_path=ssh_key
                        )
                except Exception as e:
                    logger.warning(f"Failed to parse machine string '{machine_str}': {e}")
                return None

            # Reconstruct edge
            edge_str = state_data.get('edge', '')
            edge = parse_machine_str(edge_str, MachineType.EDGE)

            # Reconstruct parent
            parent_str = state_data.get('parent', '')
            parent = parse_machine_str(parent_str, MachineType.PARENT)

            # Reconstruct peers
            peers = []
            for peer_str in state_data.get('peers', []):
                peer = parse_machine_str(peer_str, MachineType.PEER)
                if peer:
                    peers.append(peer)

            # Minimal ARLFileInfo reconstruction
            arl_file = ARLFileInfo(
                filename=state_data.get('arl_file', '').split(' ')[0],
                cpcode='unknown',
                hostnames=[]
            )

            # Reconstruct DeploymentConfig
            config = DeploymentConfig(
                deployment_id=deployment_id,
                arl_file=arl_file,
                edge=edge,
                parent=parent,
                peers=peers
            )

            # Reconstruct BackupInfo list
            backups = []
            for backup_data in state_data.get('backups', []):
                backup = BackupInfo(
                    original_path=backup_data['original_path'],
                    backup_path=backup_data['backup_path'],
                    machine=backup_data['machine'],
                    timestamp=datetime.fromisoformat(backup_data['timestamp'])
                )
                backups.append(backup)

            # Reconstruct DeploymentState
            state = DeploymentState(
                config=config,
                status=DeploymentStatus(state_data.get('status', 'not_started')),
                start_time=datetime.fromisoformat(state_data['start_time']) if state_data.get('start_time') else None,
                end_time=datetime.fromisoformat(state_data['end_time']) if state_data.get('end_time') else None,
                backups=backups,
                deployed_files=state_data.get('deployed_files', []),
                errors=state_data.get('errors', []),
                healing_attempts=state_data.get('healing_attempts', 0)
            )

            logger.info(f"Reconstructed deployment state for rollback: {deployment_id}")
            return state

        except Exception as e:
            logger.error(f"Failed to reconstruct state for rollback: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None
    
    def list_deployments(self) -> List[Dict[str, Any]]:
        """
        List all known deployments.
        
        Returns:
            List of deployment summaries
        """
        deployments = []
        
        for state_file in self.state_dir.glob("*.json"):
            try:
                with open(state_file, 'r') as f:
                    state_data = json.load(f)
                
                deployments.append({
                    'deployment_id': state_data.get('deployment_id'),
                    'status': state_data.get('status'),
                    'start_time': state_data.get('start_time'),
                    'end_time': state_data.get('end_time'),
                    'arl_file': state_data.get('arl_file'),
                })
            except Exception as e:
                logger.warning(f"Failed to load state file {state_file}: {e}")
        
        return sorted(deployments, key=lambda x: x.get('start_time', ''), reverse=True)
    
    def create_backup(
        self,
        ssh_manager: SSHManager,
        machine: MachineConfig,
        remote_path: str,
        state: DeploymentState
    ) -> Optional[BackupInfo]:
        """
        Create a backup of a remote file.
        
        Args:
            ssh_manager: SSH manager for remote operations
            machine: Machine to backup from
            remote_path: Path to file to backup
            state: Deployment state to track backup in
            
        Returns:
            BackupInfo if successful, None otherwise
        """
        try:
            # Check if file exists
            if not ssh_manager.file_exists(machine, remote_path):
                logger.warning(f"File not found, skipping backup: {machine}:{remote_path}")
                return None
            
            # Generate backup path with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{remote_path}{self.backup_suffix}.{timestamp}"
            
            # Copy file to backup location
            exit_code, stdout, stderr = ssh_manager.execute_command(
                machine,
                f"cp -p {remote_path} {backup_path}"
            )
            
            if exit_code != 0:
                logger.error(f"Failed to create backup: {stderr}")
                return None
            
            # Create backup info
            backup_info = BackupInfo(
                original_path=remote_path,
                backup_path=backup_path,
                machine=str(machine),
                timestamp=datetime.now()
            )
            
            # Add to state
            state.backups.append(backup_info)
            self.save_state(state)
            
            logger.info(f"✓ Created backup: {machine}:{remote_path} -> {backup_path}")
            return backup_info
            
        except Exception as e:
            logger.error(f"Failed to create backup of {machine}:{remote_path}: {e}")
            return None
    
    def restore_backup(
        self,
        ssh_manager: SSHManager,
        machine: MachineConfig,
        backup_info: BackupInfo
    ) -> bool:
        """
        Restore a file from backup.
        
        Args:
            ssh_manager: SSH manager for remote operations
            machine: Machine to restore to
            backup_info: Backup information
            
        Returns:
            True if successful
        """
        try:
            # Check if backup exists
            if not ssh_manager.file_exists(machine, backup_info.backup_path):
                logger.error(f"Backup file not found: {machine}:{backup_info.backup_path}")
                return False
            
            # Restore file
            exit_code, stdout, stderr = ssh_manager.execute_command(
                machine,
                f"cp -p {backup_info.backup_path} {backup_info.original_path}"
            )
            
            if exit_code != 0:
                logger.error(f"Failed to restore backup: {stderr}")
                return False
            
            logger.info(
                f"✓ Restored backup: {machine}:{backup_info.backup_path} "
                f"-> {backup_info.original_path}"
            )
            return True
            
        except Exception as e:
            logger.error(f"Failed to restore backup {backup_info.backup_path}: {e}")
            return False
    
    def rollback_deployment(
        self,
        ssh_manager: SSHManager,
        state: DeploymentState
    ) -> bool:
        """
        Rollback a deployment by restoring all backups.
        
        Args:
            ssh_manager: SSH manager for remote operations
            state: Deployment state to rollback
            
        Returns:
            True if all rollbacks successful
        """
        logger.info(f"Starting rollback of deployment {state.config.deployment_id}")
        
        success_count = 0
        failure_count = 0
        
        # Restore all backups in reverse order
        for backup_info in reversed(state.backups):
            # Find the machine config
            machine = None
            for m in state.config.get_all_machines():
                if str(m) == backup_info.machine:
                    machine = m
                    break
            
            if not machine:
                logger.warning(
                    f"Machine not found for backup: {backup_info.machine}. "
                    f"Skipping {backup_info.original_path}"
                )
                failure_count += 1
                continue
            
            # Restore the backup
            if self.restore_backup(ssh_manager, machine, backup_info):
                success_count += 1
            else:
                failure_count += 1
        
        # Update state
        state.status = DeploymentStatus.ROLLED_BACK
        state.end_time = datetime.now()
        self.save_state(state)
        
        logger.info(
            f"Rollback complete: {success_count} successful, {failure_count} failed"
        )
        
        return failure_count == 0
    
    def remove_deployed_files(
        self,
        ssh_manager: SSHManager,
        state: DeploymentState
    ) -> bool:
        """
        Remove files that were deployed (not backups, but new files).
        
        Args:
            ssh_manager: SSH manager for remote operations
            state: Deployment state
            
        Returns:
            True if all removals successful
        """
        success_count = 0
        failure_count = 0
        
        for file_info in state.deployed_files:
            machine_str = file_info.get('machine')
            file_path = file_info.get('path')
            
            if not machine_str or not file_path:
                continue
            
            # Find the machine config
            machine = None
            for m in state.config.get_all_machines():
                if str(m) == machine_str:
                    machine = m
                    break
            
            if not machine:
                logger.warning(f"Machine not found: {machine_str}")
                failure_count += 1
                continue
            
            # Remove the file
            try:
                exit_code, stdout, stderr = ssh_manager.execute_command(
                    machine,
                    f"rm -f {file_path}"
                )
                
                if exit_code == 0:
                    logger.info(f"✓ Removed deployed file: {machine}:{file_path}")
                    success_count += 1
                else:
                    logger.error(f"✗ Failed to remove {machine}:{file_path}: {stderr}")
                    failure_count += 1
                    
            except Exception as e:
                logger.error(f"✗ Failed to remove {machine}:{file_path}: {e}")
                failure_count += 1
        
        logger.info(
            f"File removal complete: {success_count} successful, {failure_count} failed"
        )
        
        return failure_count == 0
    
    def restart_ghost_on_all_machines(
        self,
        ssh_manager: SSHManager,
        state: DeploymentState,
        trident_run_path: str = "/a/sbin/trident_run"
    ) -> bool:
        """
        Restart Ghost on all machines after rollback using proper sequence.

        Proper sequence:
        1. Check Ghost status
        2. If not running, execute unstickystop with stop reason
        3. Execute restart
        4. Wait for Ghost to be ready (Port Stats in cache.log)

        Args:
            ssh_manager: SSH manager for remote operations
            state: Deployment state
            trident_run_path: Path to trident_run command

        Returns:
            True if all restarts successful
        """
        success_count = 0
        failure_count = 0

        for machine in state.config.get_ghost_machines():
            try:
                # Step 1: Check Ghost status
                logger.info(f"Checking Ghost status on {machine}")
                check_cmd = f"{trident_run_path} check webghost"
                exit_code, stdout, stderr = ssh_manager.execute_command(
                    machine, check_cmd, timeout=30
                )

                is_running = 'Running' in stdout
                stop_reason = 'unknown'

                if not is_running:
                    # Extract stop reason
                    import re
                    match = re.search(r'\(stop reasons?: ([^)]+)\)', stdout)
                    if match:
                        stop_reason = match.group(1).strip()

                    # Step 2: Execute unstickystop
                    logger.info(f"Ghost not running, executing unstickystop with reason: {stop_reason}")
                    unstickystop_cmd = f"{trident_run_path} unstickystop webghost -r {stop_reason}"
                    ssh_manager.execute_command(machine, unstickystop_cmd, timeout=60)

                # Step 3: Execute restart
                logger.info(f"Restarting Ghost on {machine}")
                restart_cmd = f"{trident_run_path} restart webghost"
                exit_code, stdout, stderr = ssh_manager.execute_command(
                    machine, restart_cmd, timeout=60
                )

                if exit_code != 0:
                    logger.error(f"✗ Ghost restart failed on {machine}: {stderr}")
                    failure_count += 1
                    continue

                # Step 4: Wait for Ghost to be ready
                logger.info(f"Waiting for Ghost to be ready on {machine}...")
                cache_log_path = "/a/logs/cache.log"
                import time
                timeout = 60
                start_time = time.time()

                while time.time() - start_time < timeout:
                    tail_cmd = f"tail -n 50 {cache_log_path} | grep -i 'Ports Stats'"
                    exit_code, stdout, stderr = ssh_manager.execute_command(
                        machine, tail_cmd, timeout=10
                    )

                    if exit_code == 0 and 'Ports Stats' in stdout:
                        logger.info(f"✓ Ghost restarted and ready on {machine}")
                        success_count += 1
                        break

                    time.sleep(2)
                else:
                    logger.error(f"✗ Timeout waiting for Ghost to be ready on {machine}")
                    failure_count += 1

            except Exception as e:
                logger.error(f"✗ Failed to restart Ghost on {machine}: {e}")
                failure_count += 1

        logger.info(
            f"Ghost restart complete: {success_count} successful, {failure_count} failed"
        )

        return failure_count == 0
    
    def track_deployed_file(
        self,
        state: DeploymentState,
        machine: MachineConfig,
        file_path: str
    ):
        """
        Track a newly deployed file.
        
        Args:
            state: Deployment state
            machine: Machine where file was deployed
            file_path: Path to deployed file
        """
        state.deployed_files.append({
            'machine': str(machine),
            'path': file_path,
            'timestamp': datetime.now().isoformat()
        })
        self.save_state(state)
