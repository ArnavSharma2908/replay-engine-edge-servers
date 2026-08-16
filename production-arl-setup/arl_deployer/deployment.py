"""
Deployment orchestration module.

This module coordinates the deployment of ARL files to edge, parent, and peer machines,
including file copying, XML modification, and Ghost restarts.
"""

import logging
import os
from pathlib import Path
from typing import List, Optional, Set
from datetime import datetime
import xml.etree.ElementTree as ET

from xmlmaniplib.xml_manipulator import XMLManipulator

from arl_deployer.models import (
    DeploymentConfig,
    DeploymentState,
    DeploymentStatus,
    MachineConfig,
    MachineType,
    ARLFileInfo,
)
from arl_deployer.ssh_manager import SSHManager
from arl_deployer.state import StateManager
from arl_deployer.logger import get_logger
from arl_deployer.arl_transformer import ARLTransformer, ARLTransformError

try:
    from ghost_restart import GhostRestartManager
except ModuleNotFoundError:  # pragma: no cover - path setup for standalone module
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
    from ghost_restart import GhostRestartManager

logger = get_logger(__name__)


class DeploymentError(Exception):
    """Raised when deployment fails."""
    pass


class Deployer:
    """
    Orchestrates deployment of ARL files to Ghost machines.
    
    Responsibilities:
    - Deploy to edge (ARL + dependencies + arlindex + DNS simulation)
    - Deploy to parent (ARL + dependencies + arlindex)
    - Deploy to peers (ARL + dependencies + arlindex)
    - Restart Ghost on all machines
    """
    
    def __init__(
        self,
        ssh_manager: SSHManager,
        state_manager: StateManager,
        ghost_config: dict,
        full_config: dict = None
    ):
        """
        Initialize Deployer.
        
        Args:
            ssh_manager: SSH manager for remote operations
            state_manager: State manager for tracking
            ghost_config: Ghost configuration from config.yaml
            full_config: Full configuration dict (optional, for config_server access)
        """
        self.ssh_manager = ssh_manager
        self.state_manager = state_manager
        self.ghost_config = ghost_config
        self.full_config = full_config or {}
        self.xml_manipulator = XMLManipulator()
    
    def deploy_full(
        self,
        config: DeploymentConfig,
        state: DeploymentState
    ) -> bool:
        """
        Execute full deployment to all machines.
        
        Args:
            config: Deployment configuration
            state: Deployment state to track progress
            
        Returns:
            True if successful
        """
        logger.info(f"Starting deployment: {config.deployment_id}")
        state.status = DeploymentStatus.IN_PROGRESS
        self.state_manager.save_state(state)

        try:
            # Step 1: Deploy to edge
            logger.substep(f"Deploying to edge: {config.edge.ip_address}", status='running')
            if not self._deploy_to_edge(config, state):
                raise DeploymentError("Edge deployment failed")
            logger.substep(f"Edge deployment complete", status='success')

            # Step 2: Deploy to parent
            logger.substep(f"Deploying to parent: {config.parent.ip_address}", status='running')
            if not self._deploy_to_parent(config, state):
                raise DeploymentError("Parent deployment failed")
            logger.substep(f"Parent deployment complete", status='success')

            # Step 3: Deploy to peers (if any)
            if config.peers:
                for idx, peer in enumerate(config.peers, 1):
                    logger.substep(f"Deploying to peer {idx}/{len(config.peers)}: {peer.ip_address}", status='running')
                    if not self._deploy_to_peer(config, state, peer):
                        logger.warning(f"Peer deployment failed for {peer}")
                        # Continue with other peers
                    else:
                        logger.substep(f"Peer {idx} deployment complete", status='success')

            # Mark as deployed
            state.status = DeploymentStatus.DEPLOYED
            state.end_time = datetime.now()
            self.state_manager.save_state(state)

            return True
            
        except DeploymentError as e:
            logger.failure(f"Deployment failed: {e}")
            state.status = DeploymentStatus.FAILED
            state.errors.append(str(e))
            state.end_time = datetime.now()
            self.state_manager.save_state(state)
            return False
    
    def _deploy_to_edge(
        self,
        config: DeploymentConfig,
        state: DeploymentState
    ) -> bool:
        """
        Deploy ARL configuration to edge machine.

        Steps:
        0a. Enable access log file writing in baseline.xml
        0b. Disable SSL forward cert verify in baseline-hacks.xml
        0c. Comment out parent IP from region-service-ips in ghost.xml
        1. Copy ARL file
        2. Copy dependencies (hd.data, waf.data)
        3. Modify ARL file (add DNS simulation)
        4. Update arlindex.xml
        5. Restart Ghost

        Args:
            config: Deployment configuration
            state: Deployment state

        Returns:
            True if successful
        """
        edge = config.edge
        arl_file = config.arl_file

        try:
            # Step 0a: Enable access log file writing
            logger.info(f"Enabling access log file writing")
            baseline_xml_path = '/a/etc/ghost/baseline.data/baseline.xml'

            if not self._enable_access_log_file_write(edge, baseline_xml_path):
                logger.warning(f"Failed to enable access log file writing (non-fatal)")

            # Step 0b: Disable SSL forward cert verify and G2G settings
            logger.info(f"Disabling SSL forward cert verify")
            if not self._disable_ssl_forward_cert_verify(edge, config):
                logger.warning(f"Failed to disable SSL forward cert verify (non-fatal)")

            # Step 0c: Comment out parent IP from region-service-ips
            logger.info(f"Commenting out parent IP from region-service-ips")
            if not self._comment_region_service_ips(edge, config):
                logger.warning(f"Failed to comment region-service-ips (non-fatal)")

            # Step 1: Copy ARL file
            remote_arl_path = os.path.join(
                self.ghost_config['arl_data_path'],
                arl_file.filename
            )

            logger.info(f"Copying ARL file to edge")
            
            if not self.ssh_manager.upload_file(edge, arl_file.local_path, remote_arl_path):
                raise DeploymentError(f"Failed to upload ARL file to edge")

            self.state_manager.track_deployed_file(state, edge, remote_arl_path)

            # Step 1b: Transform ARL for replay mode (replace match conditions with header-based cpcode matching)
            logger.info(f"Transforming ARL for replay mode (header-based cpcode matching)")
            if not self._transform_arl_for_replay(edge, remote_arl_path, state):
                logger.warning("ARL transformation failed - continuing without replay mode support")

            # Step 2: Copy dependencies if needed
            if arl_file.requires_hd_data:
                logger.info(f"Copying HD data directory")
                if not self._copy_dependency_directory(
                    config,
                    state,
                    edge,
                    'hd_data',
                    self.ghost_config['hd_data_path']
                ):
                    logger.warning("HD data copy failed - will attempt via rules engine")
            
            if arl_file.requires_waf_data:
                logger.info(f"Copying WAF data")
                if not self._copy_dependency_directory(
                    config,
                    state,
                    edge,
                    'waf_data',
                    self.ghost_config['waf_data_path']
                ):
                    logger.warning("WAF data copy failed - will attempt via rules engine")

            if arl_file.requires_pearl_data:
                logger.info(f"Copying Pearl data")
                if not self._copy_dependency_directory(
                    config,
                    state,
                    edge,
                    'pearl_data',
                    self.ghost_config['pearl_data_path']
                ):
                    logger.warning("Pearl data copy failed - will attempt via rules engine")

            # Step 3: Disable forward cert verify in ARL file
            logger.info(f"Disabling forward cert verify")
            if not self._disable_forward_cert_verify(edge, remote_arl_path, state):
                logger.warning("Failed to disable forward cert verify - manual fix may be needed")

            # Step 3a: Add cpcode header metadata
            logger.info(f"Adding cpcode header metadata")
            if not self._add_cpcode_header_to_arl(edge, remote_arl_path, state):
                logger.warning("Failed to add cpcode header metadata - manual fix may be needed")

            # Step 3b: Comment out query-arguments tags
            logger.info(f"Commenting out query-arguments tags")
            if not self._comment_query_arguments(edge, remote_arl_path, state):
                logger.warning("Failed to comment query-arguments - manual fix may be needed")

            # Step 3c: Add prewarm metadata to baseline-hacks.xml
            #logger.info(f"Adding prewarm metadata to edge baseline-hacks.xml")
            #if not self._add_prewarm_metadata_to_baseline_hacks(edge, "edge", state):
            #    logger.warning("Failed to add prewarm metadata - manual fix may be needed")

            # Step 4: Modify ARL file to add DNS simulation for parent
            logger.info(f"Adding DNS simulation")
            if not self._add_dns_simulation_to_arl(edge, remote_arl_path, config.parent.ip_address, state):
                logger.warning("DNS simulation addition failed - manual fix may be needed")

            # Step 5: Update arlindex.xml
            logger.info(f"Updating arlindex.xml")
            if not self._update_arlindex(edge, arl_file, config.hostname, config.sample_url, state):
                raise DeploymentError("Failed to update arlindex.xml on edge")

            # Step 6: Restart Ghost
            logger.info(f"Restarting Ghost")
            if not self._restart_ghost(edge):
                raise DeploymentError("Failed to restart Ghost on edge")
            
            logger.debug(f"Edge deployment completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"✗ Edge deployment failed: {e}")
            state.errors.append(f"Edge deployment: {e}")
            return False
    
    def _deploy_to_parent(
        self,
        config: DeploymentConfig,
        state: DeploymentState
    ) -> bool:
        """
        Deploy ARL configuration to parent machine.

        Steps:
        1. Copy ARL file
        2. Copy dependencies (hd.data, waf.data)
        3. Disable forward cert verify in ARL file
        4. Add template.xml tags to ARL file (parent-specific)
        5. Update arlindex.xml
        6. Restart Ghost

        Args:
            config: Deployment configuration
            state: Deployment state

        Returns:
            True if successful
        """
        logger.info(f"=== Deploying to Parent: {config.parent} ===")
        parent = config.parent
        arl_file = config.arl_file

        try:
            # Step 1: Copy ARL file
            remote_arl_path = os.path.join(
                self.ghost_config['arl_data_path'],
                arl_file.filename
            )

            logger.info(f"Copying ARL file")

            if not self.ssh_manager.upload_file(parent, arl_file.local_path, remote_arl_path):
                raise DeploymentError(f"Failed to upload ARL file to parent")

            self.state_manager.track_deployed_file(state, parent, remote_arl_path)

            # Step 2: Copy dependencies if needed
            if arl_file.requires_hd_data:
                logger.info(f"Copying HD data directory")
                if not self._copy_dependency_directory(
                    config,
                    state,
                    parent,
                    'hd_data',
                    self.ghost_config['hd_data_path']
                ):
                    logger.warning("HD data copy failed - will attempt via rules engine")

            if arl_file.requires_waf_data:
                logger.info(f"Copying WAF data")
                if not self._copy_dependency_directory(
                    config,
                    state,
                    parent,
                    'waf_data',
                    self.ghost_config['waf_data_path']
                ):
                    logger.warning("WAF data copy failed - will attempt via rules engine")

            if arl_file.requires_pearl_data:
                logger.info(f"Step 2c: Copying Pearl data to {parent}")
                if not self._copy_dependency_directory(
                    config,
                    state,
                    parent,
                    'pearl_data',
                    self.ghost_config['pearl_data_path']
                ):
                    logger.warning("Pearl data copy failed - will attempt via rules engine")

            # Step 3: Disable forward cert verify in ARL file
            logger.info(f"Disabling forward cert verify")
            if not self._disable_forward_cert_verify(parent, remote_arl_path, state):
                logger.warning("Failed to disable forward cert verify - manual fix may be needed")

            # Step 4: Add template.xml tags to parent ARL file
            logger.info(f"Step 4: Adding template.xml tags to parent ARL file")
            if not self._add_template_tags_to_parent_arl(parent, remote_arl_path, parent.ip_address, state):
                logger.warning("Failed to add template tags - manual fix may be needed")

            # Step 4b: Add replay status/size handling to baseline-hacks.xml
            logger.info(f"Adding replay status/size handling to parent baseline-hacks.xml")
            if not self._add_replay_handling_to_parent_baseline(parent, state):
                logger.warning("Failed to add replay handling - manual fix may be needed")

            # Step 4c: Add prewarm metadata to baseline-hacks.xml
            #logger.info(f"Adding prewarm metadata to parent baseline-hacks.xml")
            #if not self._add_prewarm_metadata_to_baseline_hacks(parent, "parent", state):
            #    logger.warning("Failed to add prewarm metadata - manual fix may be needed")

            # Step 5: Update arlindex.xml
            logger.info(f"Updating arlindex.xml")
            if not self._update_arlindex(parent, arl_file, config.hostname, config.sample_url, state):
                raise DeploymentError("Failed to update arlindex.xml on parent")

            # Step 6: Restart Ghost
            logger.info(f"Step 6: Restarting Ghost on {parent}")
            if not self._restart_ghost(parent):
                raise DeploymentError("Failed to restart Ghost on parent")

            logger.info(f"✓ Parent deployment completed successfully")
            return True

        except Exception as e:
            logger.error(f"✗ Parent deployment failed: {e}")
            state.errors.append(f"Parent deployment: {e}")
            return False
    
    def _deploy_to_peer(
        self,
        config: DeploymentConfig,
        state: DeploymentState,
        peer: MachineConfig
    ) -> bool:
        """
        Deploy ARL configuration to a peer machine.
        
        Similar to parent deployment.
        
        Args:
            config: Deployment configuration
            state: Deployment state
            peer: Peer machine configuration
            
        Returns:
            True if successful
        """
        logger.info(f"=== Deploying to Peer: {peer} ===")
        arl_file = config.arl_file
        
        try:
            # Copy ARL file
            remote_arl_path = os.path.join(
                self.ghost_config['arl_data_path'],
                arl_file.filename
            )
            
            logger.info(f"Copying ARL file to {peer}:{remote_arl_path}")
            
            if not self.ssh_manager.upload_file(peer, arl_file.local_path, remote_arl_path):
                raise DeploymentError(f"Failed to upload ARL file to peer")
            
            self.state_manager.track_deployed_file(state, peer, remote_arl_path)
            
            # Copy dependencies
            if arl_file.requires_hd_data:
                logger.info(f"Copying HD data directory to {peer}")
                if not self._copy_dependency_directory(
                    config,
                    state,
                    peer,
                    'hd_data',
                    self.ghost_config['hd_data_path']
                ):
                    logger.warning("HD data copy failed - will attempt via rules engine")
            
            if arl_file.requires_waf_data:
                logger.info(f"Copying WAF data to {peer}")
                if not self._copy_dependency_directory(
                    config, state, peer, 'waf_data', self.ghost_config['waf_data_path']
                ):
                    logger.warning("WAF data copy failed - will attempt via rules engine")

            if arl_file.requires_pearl_data:
                logger.info(f"Copying Pearl data to {peer}")
                if not self._copy_dependency_directory(
                    config, state, peer, 'pearl_data', self.ghost_config['pearl_data_path']
                ):
                    logger.warning("Pearl data copy failed - will attempt via rules engine")

            # Disable forward cert verify in ARL file
            remote_arl_path = os.path.join(
                self.ghost_config['arl_data_path'],
                arl_file.filename
            )
            logger.info(f"Disabling forward cert verify in ARL file")
            if not self._disable_forward_cert_verify(peer, remote_arl_path, state):
                logger.warning("Failed to disable forward cert verify - manual fix may be needed")

            # Update arlindex.xml
            if not self._update_arlindex(peer, arl_file, config.hostname, config.sample_url, state):
                raise DeploymentError("Failed to update arlindex.xml on peer")

            # Restart Ghost
            if not self._restart_ghost(peer):
                raise DeploymentError("Failed to restart Ghost on peer")
            
            logger.info(f"✓ Peer deployment completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"✗ Peer deployment failed: {e}")
            state.errors.append(f"Peer {peer} deployment: {e}")
            return False
    
    def _copy_dependency_directory(
        self,
        config: DeploymentConfig,
        state: DeploymentState,
        target_machine: MachineConfig,
        dep_type: str,
        target_path: str
    ) -> bool:
        """
        Copy a dependency directory from config server to target machine.
        
        Args:
            config: Deployment configuration
            state: Deployment state
            target_machine: Target machine to copy to
            dep_type: Dependency type ('hd_data' or 'waf_data')
            target_path: Target directory path
            
        Returns:
            True if successful
        """
        if not config.config_server:
            logger.warning(f"Config server not configured, skipping {dep_type} copy")
            return False
        
        try:
            # Get source path from config server directories
            if not config.config_server:
                logger.warning(f"Config server not configured, skipping {dep_type} copy")
                return False
            
            # Access config_server.directories section from full config
            source_dir = self.full_config.get('config_server', {}).get('directories', {}).get(dep_type)
            if not source_dir:
                logger.warning(f"Source path for {dep_type} not configured in config.yaml")
                return False
            
            # Use SFTP directory sync instead of rsync
            # This avoids needing SSH keys on the remote machines
            logger.info(f"Syncing {dep_type} from config server to {target_machine}")
            
            try:
                self.ssh_manager.sync_directory(
                    source_machine=config.config_server,
                    source_path=source_dir,
                    target_machine=target_machine,
                    target_path=target_path
                )
                logger.info(f"✓ {dep_type} copied successfully")
                return True
            except Exception as e:
                logger.error(f"✗ {dep_type} copy failed: {e}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to copy {dep_type}: {e}")
            return False
    
    def _extract_hd_files_from_arl(self, arl_file_path: str) -> Set[str]:
        """
        Extract HD file names referenced in an ARL file.
        
        Args:
            arl_file_path: Path to ARL file
            
        Returns:
            Set of HD filenames referenced in the ARL
        """
        hd_files = set()
        try:
            tree = ET.parse(arl_file_path)
            root = tree.getroot()
            
            # Look for <insert type="hd" file="..."/>  elements
            for elem in root.iter():
                if elem.attrib.get('type') == 'hd' and 'file' in elem.attrib:
                    hd_files.add(elem.attrib['file'])
                    
            logger.debug(f"Found {len(hd_files)} HD file(s) in ARL: {hd_files}")
        except Exception as e:
            logger.warning(f"Failed to parse ARL file for HD references: {e}")
            
        return hd_files
    
    def _copy_required_hd_files(
        self,
        config: DeploymentConfig,
        state: DeploymentState,
        target_machine: MachineConfig,
        arl_file: 'ARLFileInfo'
    ) -> bool:
        """
        Copy only the HD files referenced in the ARL file.
        
        Args:
            config: Deployment configuration
            state: Deployment state
            target_machine: Target machine
            arl_file: ARL file info with local path
            
        Returns:
            True if all files copied successfully
        """
        try:
            # Extract HD files from ARL
            hd_files = self._extract_hd_files_from_arl(arl_file.local_path)
            
            if not hd_files:
                logger.info("No HD files referenced in ARL")
                return True
                
            logger.info(f"Copying {len(hd_files)} HD file(s): {', '.join(hd_files)}")
            
            # Get source and target directories
            source_dir = self.full_config.get('config_server', {}).get('directories', {}).get('hd_data')
            target_dir = self.ghost_config.get('hd_data_path', '/a/etc/ghost/hd.data/')
            
            if not source_dir:
                logger.error("HD data source directory not configured")
                return False
            
            # Copy each HD file
            success_count = 0
            for hd_file in hd_files:
                source_path = f"{source_dir.rstrip('/')}/{hd_file}"
                target_path = f"{target_dir.rstrip('/')}/{hd_file}"
                
                try:
                    # Download from config server to temp file
                    import tempfile
                    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.xml') as tmp:
                        temp_path = tmp.name
                    
                    if self.ssh_manager.download_file(config.config_server, source_path, temp_path):
                        # Upload to target machine
                        if self.ssh_manager.upload_file(target_machine, temp_path, target_path):
                            logger.info(f"  ✓ Copied {hd_file}")
                            success_count += 1
                        else:
                            logger.warning(f"  ✗ Failed to upload {hd_file} to {target_machine}")
                    else:
                        logger.warning(f"  ✗ Failed to download {hd_file} from config server")
                    
                    # Clean up temp file
                    import os
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                        
                except Exception as e:
                    logger.warning(f"  ✗ Failed to copy {hd_file}: {e}")
            
            if success_count == len(hd_files):
                logger.info(f"✓ All {success_count} HD files copied successfully")
                return True
            elif success_count > 0:
                logger.warning(f"Partial success: {success_count}/{len(hd_files)} HD files copied")
                return True
            else:
                logger.error("Failed to copy any HD files")
                return False
                
        except Exception as e:
            logger.error(f"Failed to copy HD files: {e}")
            return False
    
    def _disable_forward_cert_verify(
        self,
        machine: MachineConfig,
        arl_file_path: str,
        state: DeploymentState
    ) -> bool:
        """
        Disable forward certificate verification in ARL file by commenting out
        all tags starting with 'security:ssl-forward-cert-verify-2'.

        Args:
            machine: Machine where ARL file is located
            arl_file_path: Path to ARL file
            state: Deployment state

        Returns:
            True if successful
        """
        local_temp = None
        try:
            # Create backup first
            self.state_manager.create_backup(self.ssh_manager, machine, arl_file_path, state)

            # Download ARL file for modification
            filename = Path(arl_file_path).name
            local_temp = f"/tmp/{filename}.temp"

            self.ssh_manager.download_file(machine, arl_file_path, local_temp)

            # Fix file permissions after download (ensure readable)
            import os
            import stat
            os.chmod(local_temp, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)  # 644

            # Reset xml_manipulator state before use
            self.xml_manipulator.current_element = None
            self.xml_manipulator.open_tags = []
            self.xml_manipulator.comment_added = False

            # Comment out all tags starting with security:ssl-forward-cert-verify-2
            tag_patterns_to_comment = [
                "security:ssl-forward-cert-verify-2"
            ]

            self.xml_manipulator.update_xml_with_patterns(
                local_temp,
                tag_patterns_to_comment=tag_patterns_to_comment
            )

            # Upload modified file back
            self.ssh_manager.upload_file(machine, local_temp, arl_file_path)

            # Clean up temp file
            Path(local_temp).unlink(missing_ok=True)

            logger.debug(f"Disabled forward cert verify in ARL file on {machine}")
            return True

        except Exception as e:
            logger.error(f"Failed to disable forward cert verify: {e}")
            # If file ends with .temp, it means we failed during processing - clean up
            if local_temp and Path(local_temp).exists():
                Path(local_temp).unlink(missing_ok=True)
            return False

    def _transform_arl_for_replay(
        self,
        machine: MachineConfig,
        arl_file_path: str,
        state: DeploymentState
    ) -> bool:
        """
        Transform ARL file to use header-based cpcode matching for replay mode.
        Replaces nested match conditions with x-replay-expected-cpcode header matching.

        Args:
            machine: Machine where ARL file is located
            arl_file_path: Path to ARL file
            state: Deployment state

        Returns:
            True if successful
        """
        try:
            logger.info(f"Starting ARL transformation for replay mode on {machine}")

            # Create backup first
            self.state_manager.create_backup(self.ssh_manager, machine, arl_file_path, state)

            # Download ARL file for transformation
            filename = Path(arl_file_path).name
            local_temp = f"/tmp/{filename}.replay-transform"

            if not self.ssh_manager.download_file(machine, arl_file_path, local_temp):
                raise ARLTransformError(f"Failed to download ARL file for transformation")

            # Fix file permissions after download
            import os
            import stat
            os.chmod(local_temp, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)  # 644

            # Transform the ARL file
            transformer = ARLTransformer()
            result = transformer.transform_arl_for_replay(local_temp, backup=False)

            logger.info(
                f"✓ ARL transformation complete: "
                f"{result['matches_transformed']} matches transformed, "
                f"covering {result['cpcodes_found']} unique cpcodes"
            )

            # Upload modified file back
            if not self.ssh_manager.upload_file(machine, local_temp, arl_file_path):
                raise ARLTransformError(f"Failed to upload transformed ARL file")

            # Clean up temp file
            Path(local_temp).unlink(missing_ok=True)

            return True

        except ARLTransformError as e:
            logger.error(f"ARL transformation failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during ARL transformation: {e}")
            return False

    def _add_cpcode_header_to_arl(
        self,
        machine: MachineConfig,
        arl_file_path: str,
        state: DeploymentState
    ) -> bool:
        """
        Add cpcode header metadata to ARL file.

        Args:
            machine: Machine where ARL file is located
            arl_file_path: Path to ARL file
            state: Deployment state

        Returns:
            True if successful
        """
        try:
            # Create backup first
            self.state_manager.create_backup(self.ssh_manager, machine, arl_file_path, state)

            # Download ARL file for modification
            filename = Path(arl_file_path).name
            local_temp = f"/tmp/{filename}.cpcode_header.temp"

            self.ssh_manager.download_file(machine, arl_file_path, local_temp)

            # Fix file permissions after download (ensure readable)
            import os
            import stat
            os.chmod(local_temp, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)  # 644

            # Add cpcode header tags
            tags_to_add = [
                "<edgeservices:modify-outgoing-response.add-header>",
                "<edge-only>on</edge-only>",
                "<name>X-Trident-Cpcode</name>",
                "<value>%(AK_CPCODE)</value>",
                "<status>on</status>",
                "</edgeservices:modify-outgoing-response.add-header>"
            ]

            # Reset xml_manipulator state before use
            self.xml_manipulator.current_element = None
            self.xml_manipulator.open_tags = []
            self.xml_manipulator.comment_added = False

            self.xml_manipulator.update_xml(
                local_temp,
                [],  # No tags to comment
                tags_to_add
            )

            # Upload modified file back
            self.ssh_manager.upload_file(machine, local_temp, arl_file_path)

            # Clean up temp file
            Path(local_temp).unlink(missing_ok=True)

            logger.debug(f"Added cpcode header metadata to ARL file on {machine}")
            return True

        except Exception as e:
            logger.error(f"Failed to add cpcode header metadata: {e}")
            return False

    def _comment_query_arguments(
        self,
        machine: MachineConfig,
        arl_file_path: str,
        state: DeploymentState
    ) -> bool:
        """
        Comment out cache:key.query-arguments tags in ARL file.

        Args:
            machine: Machine where ARL file is located
            arl_file_path: Path to ARL file
            state: Deployment state

        Returns:
            True if successful
        """
        try:
            # Create backup first
            self.state_manager.create_backup(self.ssh_manager, machine, arl_file_path, state)

            # Download ARL file for modification
            filename = Path(arl_file_path).name
            local_temp = f"/tmp/{filename}.query_args.temp"

            self.ssh_manager.download_file(machine, arl_file_path, local_temp)

            # Fix file permissions after download (ensure readable)
            import os
            import stat
            os.chmod(local_temp, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)  # 644

            # Reset xml_manipulator state before use
            self.xml_manipulator.current_element = None
            self.xml_manipulator.open_tags = []
            self.xml_manipulator.comment_added = False

            # Comment out query-arguments tags using pattern matching
            tag_patterns_to_comment = [
                "cache:key.query-arguments"
            ]

            self.xml_manipulator.update_xml_with_patterns(
                local_temp,
                tag_patterns_to_comment=tag_patterns_to_comment
            )

            # Upload modified file back
            self.ssh_manager.upload_file(machine, local_temp, arl_file_path)

            # Clean up temp file
            Path(local_temp).unlink(missing_ok=True)

            logger.debug(f"Commented out query-arguments tags in ARL file on {machine}")
            return True

        except Exception as e:
            logger.error(f"Failed to comment query-arguments: {e}")
            return False

    def _add_dns_simulation_to_arl(
        self,
        machine: MachineConfig,
        arl_file_path: str,
        parent_ip: str,
        state: DeploymentState
    ) -> bool:
        """
        Add DNS simulation lookup to ARL file to force forwarding to parent.

        Args:
            machine: Machine where ARL file is located
            arl_file_path: Path to ARL file
            parent_ip: Parent IP address
            state: Deployment state

        Returns:
            True if successful
        """
        try:
            # Create backup first
            self.state_manager.create_backup(self.ssh_manager, machine, arl_file_path, state)

            # Download ARL file for modification
            filename = Path(arl_file_path).name
            local_temp = f"/tmp/{filename}.temp"

            self.ssh_manager.download_file(machine, arl_file_path, local_temp)

            # Fix file permissions after download (ensure readable)
            import os
            import stat
            os.chmod(local_temp, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)  # 644

            # Add DNS simulation tags
            tags_to_add = [
                "<network:dns.simulate-lookup>",
                "<status>on</status>",
                f"<addr>{parent_ip}</addr>",
                "</network:dns.simulate-lookup>"
            ]

            # Reset xml_manipulator state before use
            self.xml_manipulator.current_element = None
            self.xml_manipulator.open_tags = []
            self.xml_manipulator.comment_added = False

            self.xml_manipulator.update_xml(
                local_temp,
                [],  # No tags to comment
                tags_to_add
            )

            # Upload modified file back
            self.ssh_manager.upload_file(machine, local_temp, arl_file_path)

            # Clean up temp file
            Path(local_temp).unlink(missing_ok=True)

            logger.debug(f"Added DNS simulation to ARL file on {machine}")
            return True

        except Exception as e:
            logger.error(f"Failed to add DNS simulation: {e}")
            return False

    def _add_template_tags_to_parent_arl(
        self,
        machine: MachineConfig,
        arl_file_path: str,
        parent_ip: str,
        state: DeploymentState
    ) -> bool:
        """
        Replace parent ARL file with template.xml content.
        This replaces the entire ARL file with the origin server configuration from template.xml.

        Args:
            machine: Parent machine where ARL file is located
            arl_file_path: Path to ARL file on parent
            parent_ip: Parent machine IP address (replaces ORIGIN placeholder)
            state: Deployment state

        Returns:
            True if successful
        """
        try:
            # Create backup first
            self.state_manager.create_backup(self.ssh_manager, machine, arl_file_path, state)

            # Read template.xml content directly
            template_path = Path(__file__).parent.parent / "template.xml"
            if not template_path.exists():
                logger.error(f"template.xml not found at {template_path}")
                return False

            # Read template.xml
            with open(template_path, 'r') as f:
                template_content = f.read()

            # Replace ORIGIN placeholder with parent IP
            modified_content = template_content.replace('ORIGIN', parent_ip)

            # Write to temp file
            filename = Path(arl_file_path).name
            local_temp = f"/tmp/{filename}.parent_template.temp"

            with open(local_temp, 'w') as f:
                f.write(modified_content)

            # Fix file permissions after creating (ensure readable)
            import os
            import stat
            os.chmod(local_temp, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)  # 644

            # Upload modified file back (replacing the original)
            self.ssh_manager.upload_file(machine, local_temp, arl_file_path)

            # Clean up temp file
            Path(local_temp).unlink(missing_ok=True)

            logger.info(f"✓ Replaced parent ARL file with template.xml content on {machine}")
            return True

        except Exception as e:
            logger.error(f"Failed to replace ARL with template: {e}")
            return False

    def _add_replay_handling_to_parent_baseline(
        self,
        machine: MachineConfig,
        state: DeploymentState
    ) -> bool:
        """
        Add replay status/size handling tags to parent baseline-hacks.xml.

        Args:
            machine: Parent machine
            state: Deployment state

        Returns:
            True if successful
        """
        try:
            baseline_hacks_path = '/a/etc/ghost/baseline.data/baseline-hacks.xml'
            
            # Create backup first
            self.state_manager.create_backup(self.ssh_manager, machine, baseline_hacks_path, state)

            # Download baseline-hacks.xml for modification
            local_temp = f"/tmp/baseline-hacks_{machine.ip_address}.replay.temp"

            self.ssh_manager.download_file(machine, baseline_hacks_path, local_temp)

            # Fix file permissions after download (ensure readable)
            import os
            import stat
            os.chmod(local_temp, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)  # 644

            # Add replay handling tags
            tags_to_add = [
                "<edgeservices:modify-outgoing-response.client-status-code-override>%(REPLAY_STATUS)</edgeservices:modify-outgoing-response.client-status-code-override>",
                "",
                "<assign:extract-value>",
                "<location>Client_Request_Header</location>",
                "<location-id>x-replay-expected-status</location-id>",
                "<location-id-prefix-match>off</location-id-prefix-match>",
                "<variable-name>REPLAY_STATUS</variable-name>",
                "<hidden>off</hidden>",
                "<sensitive>off</sensitive>",
                "</assign:extract-value>",
                "",
                "<assign:extract-value>",
                "<location>Client_Request_Header</location>",
                "<location-id>x-replay-expected-size</location-id>",
                "<location-id-prefix-match>off</location-id-prefix-match>",
                "<variable-name>REPLAY_SIZE</variable-name>",
                "<hidden>off</hidden>",
                "<sensitive>off</sensitive>",
                "</assign:extract-value>",
                "",
                "<!-- Compute Range end = REPLAY_SIZE - 1 -->",
                "<assign:variable>",
                "<name>REPLAY_RANGE_END</name>",
                "<value>%(REPLAY_SIZE)</value>",
                "<max-length>8192</max-length>",
                "<sensitive>off</sensitive>",
                "<hidden>default</hidden>",
                "<accept-new-line>off</accept-new-line>",
                "<transform>",
                "<subtract>1</subtract>",
                "</transform>",
                "</assign:variable>",
                "",
                "<edgeservices:modify-outgoing-request.add-header>",
                "<status>on</status>",
                "<name>Range</name>",
                "<value>bytes=0-%(REPLAY_RANGE_END)</value>",
                "<edge-only>off</edge-only>",
                "</edgeservices:modify-outgoing-request.add-header>",
                "",
                "<edgeservices:modify-outgoing-request.remove-header>",
                "<status>on</status>",
                "<name>Accept-Encoding</name>",
                "<edge-only>off</edge-only>",
                "</edgeservices:modify-outgoing-request.remove-header>",
                "",
                "<cache:no-store>on</cache:no-store>"
            ]

            # Reset xml_manipulator state before use
            self.xml_manipulator.current_element = None
            self.xml_manipulator.open_tags = []
            self.xml_manipulator.comment_added = False

            self.xml_manipulator.update_xml(
                local_temp,
                [],  # No tags to comment
                tags_to_add
            )

            # Upload modified file back
            self.ssh_manager.upload_file(machine, local_temp, baseline_hacks_path)

            # Clean up temp file
            Path(local_temp).unlink(missing_ok=True)

            logger.info(f"✓ Added replay status/size handling to parent baseline-hacks.xml on {machine}")
            return True

        except Exception as e:
            logger.error(f"Failed to add replay handling to parent baseline-hacks.xml: {e}")
            return False
    
    def _reload_ghost_config(
        self,
        machine: MachineConfig,
        config_type: str,
        filename: str = None
    ) -> bool:
        """
        Reload Ghost configuration without restarting by using TRIDENT_META_UPDATE.

        Args:
            machine: Target machine
            config_type: Type of config ('baseline', 'baseline-hacks', 'arl')
            filename: Filename (required for ARL files)

        Returns:
            True if successful
        """
        if config_type == 'baseline':
            url = "http://127.0.0.1/7/7/7/baseline.data/stager.trident.com/usr/local/trident/etc/ghost/baseline.data/baseline.xml"
            method = "TRIDENT_STRICT_META_UPDATE"
        elif config_type == 'baseline-hacks':
            url = "http://127.0.0.1/7/7/7/baseline.data/stager.trident.com/usr/local/trident/etc/ghost/baseline.data/baseline-hacks.xml"
            method = "TRIDENT_STRICT_META_UPDATE"
        elif config_type == 'arl':
            if not filename:
                logger.warning("ARL filename required for reload")
                return False
            url = f"http://127.0.0.1/7/7/7/arl.data/stager.trident.com/usr/local/trident/etc/ghost/arl.data/{filename}"
            method = "TRIDENT_META_UPDATE"
        else:
            logger.warning(f"Unknown config type: {config_type}")
            return False

        # Execute curl command on the machine
        cmd = f"curl -i -X {method} {url}"
        logger.debug(f"Reloading {config_type} config on {machine}")

        exit_code, stdout, stderr = self.ssh_manager.execute_command(
            machine,
            cmd,
            timeout=30
        )

        if exit_code != 0:
            logger.warning(f"Failed to reload {config_type} config: {stderr}")
            return False

        # Check for HTTP 200 in response
        if "200 OK" not in stdout and "HTTP/1.1 200" not in stdout:
            logger.warning(f"Config reload returned non-200 status: {stdout[:200]}")
            return False

        logger.debug(f"Successfully reloaded {config_type} config on {machine}")
        return True

    def _disable_ssl_forward_cert_verify(
        self,
        machine: MachineConfig,
        config: DeploymentConfig
    ) -> bool:
        """
        Disable SSL forward cert verify and G2G settings in baseline-hacks.xml.

        Adds security and G2G tags to baseline-hacks.xml.

        Args:
            machine: Target machine
            config: Deployment configuration

        Returns:
            True if successful
        """
        try:
            baseline_hacks_path = '/a/etc/ghost/baseline.data/baseline-hacks.xml'
            local_temp_path = f"/tmp/baseline-hacks_{machine.ip_address}.xml"

            logger.debug(f"Downloading {baseline_hacks_path} from {machine}")
            success = self.ssh_manager.download_file(
                machine,
                baseline_hacks_path,
                local_temp_path
            )

            if not success:
                logger.warning(f"Could not download baseline-hacks.xml from {machine}")
                return False

            # Add SSL and G2G rollback tags
            tags_to_add = [
                "<security:ssl-forward-cert-verify-2.peer.status>off</security:ssl-forward-cert-verify-2.peer.status>",
                "<security:network-partition.mutual-auth.enable-for-g2g>off</security:network-partition.mutual-auth.enable-for-g2g>",
                "<security:ssl-forward-cert-verify.status>off</security:ssl-forward-cert-verify.status>",
                "<security:ssl-forward-cert-verify-2.peer.check-mode>WARN</security:ssl-forward-cert-verify-2.peer.check-mode>",
                "<config:rollback-g2g-retry-http-on-error>off</config:rollback-g2g-retry-http-on-error>",
                "<security:network-partition.mutual-authentication.enable-for-g2g>off</security:network-partition.mutual-authentication.enable-for-g2g>",
                "<config:rollback.http-g2g-over-tls>on</config:rollback.http-g2g-over-tls>",
                "<match:request.header name=\"x-replay-step\" value=\"prewarm\" result=\"true\">",
                "<match:request.header name=\"x-replay-expected-path\" value=\"edge\" result=\"false\">",
                    "<cache:no-store>on</cache:no-store>",
                "</match:request.header>",
                "</match:request.header>",
            ]

            logger.debug(f"Adding SSL/G2G tags to baseline-hacks.xml")
            self.xml_manipulator.update_xml(
                base_xml_file=local_temp_path,
                tags_to_add=tags_to_add
            )

            # Upload modified file back
            logger.debug(f"Uploading modified baseline-hacks.xml to {machine}")
            success = self.ssh_manager.upload_file(
                machine,
                local_temp_path,
                baseline_hacks_path
            )

            if not success:
                logger.warning(f"Could not upload modified baseline-hacks.xml to {machine}")
                return False

            # Reload baseline-hacks.xml configuration
            logger.debug(f"Reloading baseline-hacks.xml configuration on {machine}")
            reload_success = self._reload_ghost_config(machine, 'baseline-hacks')

            if not reload_success:
                logger.warning(f"Failed to reload baseline-hacks.xml config (non-fatal)")

            logger.debug(f"SSL forward cert verify disabled")
            return True

        except Exception as e:
            logger.warning(f"Failed to disable SSL forward cert verify on {machine}: {e}")
            return False

    def _comment_region_service_ips(
        self,
        machine: MachineConfig,
        config: DeploymentConfig
    ) -> bool:
        """
        Comment out parent IP from region-service-ips in ghost.xml.

        Args:
            machine: Target machine
            config: Deployment configuration

        Returns:
            True if successful
        """
        try:
            ghost_xml_path = self.ghost_config.get('ghost_xml_path', '/a/etc/ghost/ghost.xml')
            local_temp_path = f"/tmp/ghost_{machine.ip_address}.xml"
            parent_ip = config.parent.ip_address

            logger.debug(f"Downloading {ghost_xml_path} from {machine}")
            success = self.ssh_manager.download_file(
                machine,
                ghost_xml_path,
                local_temp_path
            )

            if not success:
                logger.warning(f"Could not download ghost.xml from {machine}")
                return False

            # Comment out the parent IP tag
            tags_to_comment = [
                f"<config:legacy.region-service-ips>{parent_ip}</config:legacy.region-service-ips>"
            ]

            logger.debug(f"Commenting out region-service-ips tag with parent IP {parent_ip}")
            self.xml_manipulator.update_xml(
                base_xml_file=local_temp_path,
                tags_to_comment=tags_to_comment
            )

            # Upload modified file back
            logger.debug(f"Uploading modified ghost.xml to {machine}")
            success = self.ssh_manager.upload_file(
                machine,
                local_temp_path,
                ghost_xml_path
            )

            if not success:
                logger.warning(f"Could not upload modified ghost.xml to {machine}")
                return False

            logger.debug(f"Commented out region-service-ips for parent IP {parent_ip}")
            return True

        except Exception as e:
            logger.warning(f"Failed to comment region-service-ips on {machine}: {e}")
            return False

    def _enable_access_log_file_write(
        self,
        machine: MachineConfig,
        baseline_xml_path: str
    ) -> bool:
        """
        Enable access log file writing in baseline.xml.

        Adds these tags before the closing </trident:edge-config>:
        - <reporting:access-log.file-write>on</reporting:access-log.file-write>
        - <reporting:access-log.pipe-write>off</reporting:access-log.pipe-write>

        Args:
            machine: Target machine
            baseline_xml_path: Path to baseline.xml file

        Returns:
            True if successful
        """
        try:
            # Download baseline.xml
            local_temp_path = f"/tmp/baseline_{machine.ip_address}.xml"

            logger.debug(f"Downloading {baseline_xml_path} from {machine} to {local_temp_path}")
            success = self.ssh_manager.download_file(
                machine,
                baseline_xml_path,
                local_temp_path
            )

            if not success:
                logger.warning(f"Could not download baseline.xml from {machine}")
                return False

            logger.debug(f"Successfully downloaded baseline.xml")

            # Use XMLManipulator to add the tags at the end
            tags_to_add = [
                "<reporting:access-log.file-write>on</reporting:access-log.file-write>",
                "<reporting:access-log.pipe-write>off</reporting:access-log.pipe-write>"
            ]

            logger.debug(f"Adding access log tags to end of {local_temp_path}")

            # Call update_xml to add tags before closing </trident:edge-config>
            try:
                self.xml_manipulator.update_xml(
                    base_xml_file=local_temp_path,
                    tags_to_add=tags_to_add
                )
                logger.debug(f"XMLManipulator.update_xml completed")
            except Exception as xml_error:
                logger.warning(f"XMLManipulator.update_xml failed: {xml_error}")
                return False

            # Verify tags were added
            with open(local_temp_path, 'r') as f:
                modified_content = f.read()
                if 'reporting:access-log.file-write' not in modified_content:
                    logger.warning(f"Tags were not added to baseline.xml by XMLManipulator")
                    # Print last 500 chars to see what's in the file
                    logger.debug(f"File end: ...{modified_content[-500:]}")
                    return False

            logger.debug(f"Verified tags were added to local file")

            # Upload modified file back
            logger.debug(f"Uploading modified baseline.xml to {machine}:{baseline_xml_path}")
            success = self.ssh_manager.upload_file(
                machine,
                local_temp_path,
                baseline_xml_path
            )

            if not success:
                logger.warning(f"Upload failed")
                return False

            logger.debug(f"Successfully uploaded modified baseline.xml")

            if not success:
                logger.warning(f"Could not upload modified baseline.xml to {machine}")
                return False

            # Reload baseline.xml configuration without restarting Ghost
            logger.debug(f"Reloading baseline.xml configuration on {machine}")
            reload_success = self._reload_ghost_config(machine, 'baseline')

            if not reload_success:
                logger.warning(f"Failed to reload baseline.xml config (non-fatal)")

            logger.info(f"Access log file writing enabled")
            return True

        except Exception as e:
            logger.warning(f"Failed to enable access log file writing on {machine}: {e}")
            return False

    def _add_prewarm_metadata_to_baseline_hacks(
        self,
        machine: MachineConfig,
        machine_type: str,
        state: DeploymentState
    ) -> bool:
        """
        Add prewarm metadata tags to baseline-hacks.xml on a machine using temp file approach.
        
        Args:
            machine: Machine configuration
            machine_type: Type of machine ('edge' or 'parent')
            state: Deployment state
            
        Returns:
            True if successful
        """
        baseline_hacks_path = "/a/etc/ghost/baseline.data/baseline-hacks.xml"
        
        try:
            logger.debug(f"Adding prewarm metadata to {machine_type} baseline-hacks.xml")
            
            # Remove existing prewarm metadata if present (to avoid duplicates)
            remove_cmd = f"sed -i.prewarm_old '/x-replay-step.*prewarm/,/<\\/match:request.header>/d' {baseline_hacks_path}"
            self.ssh_manager.execute_command(machine, remove_cmd)
            
            # Define metadata XML based on machine type
            if machine_type == "edge":
                metadata_xml = """  <match:request.header name="x-replay-step" value="prewarm" result="true">
      <match:request.header name="x-replay-expected-path" value="edge" result="false">
          <cache:no-store>on</cache:no-store>
      </match:request.header>
  </match:request.header>"""
            else:  # parent
                metadata_xml = """  <match:request.header name="x-replay-step" value="prewarm" result="true">
      <match:request.header name="x-replay-expected-path" value="parent" result="true">
          <cache:no-store>off</cache:no-store>
      </match:request.header>
  </match:request.header>"""
            
            # Create temp file on remote machine with the metadata
            temp_file = f"/tmp/prewarm_metadata_{machine_type}.xml"
            create_temp_cmd = f"cat > {temp_file} << 'EOF'\n{metadata_xml}\nEOF"
            
            logger.debug(f"Creating temp file with prewarm metadata on {machine_type}")
            exit_code, stdout, stderr = self.ssh_manager.execute_command(machine, create_temp_cmd)
            
            # Insert temp file content before closing </trident:edge-config> tag
            insert_cmd = f"sed -i.prewarm_bak '/<\\/trident:edge-config>/{{r {temp_file}\n}}' {baseline_hacks_path}"
            
            logger.debug(f"Inserting prewarm metadata into baseline-hacks.xml")
            exit_code, stdout, stderr = self.ssh_manager.execute_command(machine, insert_cmd)
            
            # Clean up temp file
            cleanup_cmd = f"rm -f {temp_file}"
            self.ssh_manager.execute_command(machine, cleanup_cmd)
            
            # Verify tags were added
            verify_cmd = f'grep -c \'x-replay-step.*prewarm\' {baseline_hacks_path} || echo 0'
            exit_code, stdout, stderr = self.ssh_manager.execute_command(machine, verify_cmd)
            
            if stdout and stdout.strip() != '0':
                logger.info(f"✓ Prewarm metadata added to {machine_type} baseline-hacks.xml")
                return True
            else:
                logger.warning(f"Failed to verify prewarm metadata on {machine_type}")
                return False
            
        except Exception as e:
            logger.warning(f"Failed to add prewarm metadata to {machine}: {e}")
            return False

    def _update_arlindex(
        self,

        machine: MachineConfig,
        arl_file: ARLFileInfo,
        cli_hostname: Optional[str],
        sample_url: Optional[str],
        state: DeploymentState
    ) -> bool:
        """
        Update arlindex.xml to include the ARL file.
        
        Args:
            machine: Target machine
            arl_file: ARL file information
            cli_hostname: User-provided hostname from CLI (preferred)
            sample_url: Sample URL from traffic profile (fallback)
            state: Deployment state
            
        Returns:
            True if successful
        """
        try:
            arlindex_path = self.ghost_config['arlindex_path']
            local_arlindex = f"/tmp/arlindex_{machine.ip_address}.xml"
            
            # Try to download arlindex.xml
            file_exists = False
            try:
                if self.ssh_manager.download_file(machine, arlindex_path, local_arlindex):
                    file_exists = True
                    # File exists, create backup
                    self.state_manager.create_backup(self.ssh_manager, machine, arlindex_path, state)
                    logger.debug(f"Downloaded existing arlindex.xml from {machine}")
            except Exception as e:
                logger.debug(f"Failed to download arlindex.xml: {e}")
                file_exists = False
            
            if not file_exists:
                # File doesn't exist, create a new one
                logger.warning(f"arlindex.xml not found on {machine}, creating new one")
                arlindex_template = '''<?xml version="1.0" encoding="UTF-8"?>
<trident:arlindex-config xmlns:trident="uri:trident.com/metadata/trident/5.0">
</trident:arlindex-config>
'''
                Path(local_arlindex).write_text(arlindex_template)
                logger.info(f"✓ Created new arlindex.xml template")
            
            # Determine the hostnames to add:
            # 1. CLI-provided hostnames (--hostname, can be multiple)
            # 2. Hostname extracted from sample URL (if not already in list)
            # 3. Fallback to ARL file hostnames
            hostnames_to_add = []

            # Check if config has multiple hostnames (new field)
            if hasattr(state.config, 'hostnames') and state.config.hostnames:
                # Use new hostnames list field
                hostnames_to_add.extend(state.config.hostnames)
                logger.debug(f"Will add CLI-provided hostnames: {', '.join(state.config.hostnames)}")
            elif cli_hostname:
                # Fallback to old single hostname field
                hostnames_to_add.append(cli_hostname)
                logger.debug(f"Will add CLI-provided hostname: {cli_hostname}")

            if sample_url:
                # Extract hostname from sample URL
                from urllib.parse import urlparse
                if sample_url.startswith('http://') or sample_url.startswith('https://'):
                    parsed = urlparse(sample_url)
                    url_hostname = parsed.hostname
                    if url_hostname and url_hostname not in hostnames_to_add:
                        hostnames_to_add.append(url_hostname)
                        logger.debug(f"Will add hostname from sample URL: {url_hostname}")

            if not hostnames_to_add:
                # Final fallback to ARL file hostnames
                valid_hosts = [h for h in arl_file.hostnames if not h.startswith('%')]
                hostname = valid_hosts[0] if valid_hosts else "unknown"
                hostnames_to_add.append(hostname)
                logger.warning(f"No CLI hostname or sample URL, using fallback from ARL file: {hostname}")

            logger.debug(f"Adding arlindex entry with {len(hostnames_to_add)} hostname(s) for file: {arl_file.filename}")

            # xml_manipulator requires tags to be added sequentially:
            # 1. Opening tag  2. Nested content (all <host> tags)  3. Closing tag
            tags_to_add = [f'<trident:include arlid="{arl_file.arlid}" type="arldata" file="{arl_file.filename}">']

            # Add all hostnames
            for hostname in hostnames_to_add:
                tags_to_add.append(f'<host>{hostname}</host>')

            tags_to_add.append(f'</trident:include>')
            
            # Update arlindex.xml
            # Reset xml_manipulator state before use (in case it was used for previous file)
            self.xml_manipulator.current_element = None
            self.xml_manipulator.open_tags = []
            self.xml_manipulator.comment_added = False
            
            self.xml_manipulator.update_xml(
                local_arlindex,
                [],  # No tags to comment
                tags_to_add
            )
            
            # Upload modified file
            self.ssh_manager.upload_file(machine, local_arlindex, arlindex_path)
            
            # Clean up
            Path(local_arlindex).unlink(missing_ok=True)
            
            logger.debug(f"Updated arlindex.xml on {machine}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update arlindex.xml on {machine}: {e}")
            return False
    
    def _restart_ghost(self, machine: MachineConfig) -> bool:
        """
        Restart Ghost on a machine using the unified GhostRestartManager.

        Args:
            machine: Machine to restart Ghost on

        Returns:
            True if successful
        """
        try:
            manager = GhostRestartManager(
                run_command=lambda cmd, timeout: self.ssh_manager.execute_command(
                    machine, cmd, timeout=timeout
                ),
                machine_label=str(machine),
                trident_run=self.ghost_config['trident_run'],
                cache_log_path=self.ghost_config.get('cache_log_path', '/a/logs/cache.log'),
                logger=logger,
            )
            return manager.restart()

        except Exception as e:
            logger.error(f"Failed to restart Ghost on {machine}: {e}")
            return False
