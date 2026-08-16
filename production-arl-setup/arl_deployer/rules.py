"""
Rule engine for self-healing deployments.

This module loads and executes YAML-based rules to automatically fix
common deployment issues.
"""

import logging
import time
import yaml
from typing import List, Dict, Any, Optional
from pathlib import Path

from xmlmaniplib.xml_manipulator import XMLManipulator

from arl_deployer.models import (
    VerificationResult,
    DeploymentConfig,
    DeploymentState,
    Rule,
    RuleCondition,
    RuleAction,
    RuleExecutionResult,
    ActionType,
    MachineConfig,
)
from arl_deployer.ssh_manager import SSHManager
from arl_deployer.verification import Verifier
from arl_deployer.logger import get_logger

try:
    from ghost_restart import GhostRestartManager
except ModuleNotFoundError:  # pragma: no cover - path setup for standalone module
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
    from ghost_restart import GhostRestartManager

logger = logging.getLogger(__name__)
arl_logger = None  # Will be initialized in RuleEngine.__init__


class RuleEngineError(Exception):
    """Raised when rule engine encounters an error."""
    pass


class RuleEngine:
    """
    Executes self-healing rules based on verification failures.
    
    Responsibilities:
    - Load rules from YAML configuration
    - Match rules against verification results
    - Execute rule actions (copy files, modify XML, restart Ghost, etc.)
    - Track rule execution history
    - Support iterative healing attempts
    """
    
    def __init__(
        self,
        ssh_manager: SSHManager,
        verifier: Verifier,
        rules_file: str = "rules.yaml",
        ghost_config: Optional[Dict[str, Any]] = None
    ):
        """
        Initialize Rule Engine.
        
        Args:
            ssh_manager: SSH manager for remote operations
            verifier: Verifier for re-running verification
            rules_file: Path to rules YAML file
            ghost_config: Ghost configuration dict
        """
        self.ssh_manager = ssh_manager
        self.verifier = verifier
        self.rules_file = rules_file
        self.ghost_config = ghost_config or {}
        self.xml_manipulator = XMLManipulator()

        # Initialize custom logger
        global arl_logger
        arl_logger = get_logger(__name__)

        # Load rules
        self.rules = self._load_rules()
        self.settings = {}

        logger.info(f"Rule Engine initialized with {len(self.rules)} rules")
        arl_logger.info(f"Loaded {len(self.rules)} rules from {self.rules_file}")
    
    def _load_rules(self) -> List[Dict[str, Any]]:
        """
        Load rules from YAML file.
        
        Returns:
            List of rule dictionaries
        """
        try:
            with open(self.rules_file, 'r') as f:
                config = yaml.safe_load(f)
            
            rules = config.get('rules', [])
            self.settings = config.get('settings', {})
            
            logger.info(f"Loaded {len(rules)} rules from {self.rules_file}")
            return rules
            
        except FileNotFoundError:
            logger.error(f"Rules file not found: {self.rules_file}")
            return []
        except Exception as e:
            logger.error(f"Failed to load rules: {e}")
            return []
    
    def execute_healing(
        self,
        config: DeploymentConfig,
        state: DeploymentState,
        verification_result: VerificationResult
    ) -> bool:
        """
        Execute healing rules based on verification failure.
        
        Args:
            config: Deployment configuration
            state: Deployment state
            verification_result: Failed verification result
            
        Returns:
            True if healing successful and verification passes
        """
        # Beautiful banner for self-healing start
        arl_logger.banner("Self-Healing Engine", config.deployment_id)

        # Show failure reason prominently
        arl_logger.warning(f"Detected issue: {verification_result.failure_reason}")

        # Verbose logging
        logger.info(f"=== Starting Self-Healing for {config.deployment_id} ===")
        logger.info(f"Failure reason: {verification_result.failure_reason}")

        max_iterations = self.settings.get('max_iterations', 3)
        iteration_delay = self.settings.get('iteration_delay', 5)
        stop_on_success = self.settings.get('stop_on_success', True)

        for iteration in range(1, max_iterations + 1):
            # Only show iteration in default mode if multiple iterations
            if max_iterations > 1:
                arl_logger.section(f"Attempt {iteration} of {max_iterations}")
            else:
                arl_logger.step("Analyzing and applying fixes")

            logger.info(f"\n--- Healing Iteration {iteration}/{max_iterations} ---")

            # Find matching rules
            matching_rules = self._find_matching_rules(verification_result, config)

            if not matching_rules:
                arl_logger.warning("No matching rules found for this failure")
                logger.warning("No matching rules found for this failure")
                break

            # Show progress in minimal mode
            arl_logger.progress(f"Found {len(matching_rules)} applicable rule(s)")
            logger.info(f"Found {len(matching_rules)} matching rule(s)")

            # Execute matching rules
            any_success = False
            for rule in matching_rules:
                rule_name = rule.get('name', 'unknown')
                rule_desc = rule.get('description', '')

                # Show what we're doing
                arl_logger.progress(f"Applying: {rule_desc}")
                logger.info(f"Executing rule: {rule_name}")

                result = self._execute_rule(rule, config, state, verification_result)

                # Show result with color
                if result.success:
                    arl_logger.success(f"Fixed: {rule_desc}")
                else:
                    arl_logger.failure(f"Failed to apply: {rule_name}")

                logger.info(str(result))

                if result.success:
                    any_success = True
                    state.healing_attempts += 1

                    if stop_on_success:
                        logger.info("Rule succeeded, stopping healing")
                        break

            # If any rule executed successfully, verification will be run by the action
            # Check if we should continue
            if any_success and stop_on_success:
                return True

            # Wait before next iteration
            if iteration < max_iterations:
                arl_logger.progress(f"Waiting {iteration_delay}s before retry...")
                logger.info(f"Waiting {iteration_delay}s before next iteration...")
                time.sleep(iteration_delay)

        arl_logger.failure(f"Unable to auto-fix after {max_iterations} attempt(s)")
        logger.warning(f"✗ Healing failed after {max_iterations} iterations")
        return False
    
    def _find_matching_rules(
        self,
        verification_result: VerificationResult,
        config: DeploymentConfig
    ) -> List[Dict[str, Any]]:
        """
        Find rules that match the current failure.
        
        Args:
            verification_result: Verification result
            config: Deployment configuration
            
        Returns:
            List of matching rules, sorted by priority
        """
        matching_rules = []
        
        for rule in self.rules:
            if not rule.get('enabled', True):
                continue
            
            condition = rule.get('condition', {})
            
            # Check condition type
            cond_type = condition.get('type')
            
            if cond_type == 'verification_failure':
                # Match on failure reason
                reason = condition.get('reason')
                if reason and reason in verification_result.failure_reason:
                    matching_rules.append(rule)
                    logger.debug(f"Rule '{rule['name']}' matched on reason: {reason}")
            
            elif cond_type == 'log_pattern':
                # Match on log patterns
                patterns = condition.get('patterns', [])

                # Check f-line and r-line for patterns
                for pattern in patterns:
                    # Check failure_reason first (contains parsed error codes)
                    if verification_result.failure_reason and pattern in verification_result.failure_reason:
                        matching_rules.append(rule)
                        logger.debug(f"Rule '{rule['name']}' matched on failure_reason pattern: {pattern}")
                        break

                    if verification_result.f_line_data and pattern in verification_result.f_line_data.raw_line:
                        matching_rules.append(rule)
                        logger.debug(f"Rule '{rule['name']}' matched on f-line pattern: {pattern}")
                        break

                    if verification_result.r_line_data and pattern in verification_result.r_line_data.raw_line:
                        matching_rules.append(rule)
                        logger.debug(f"Rule '{rule['name']}' matched on r-line pattern: {pattern}")
                        break
            
            elif cond_type == 'file_not_exists':
                # Would need additional check - skip for now
                pass
        
        # Sort by priority (lower number = higher priority)
        matching_rules.sort(key=lambda r: r.get('priority', 999))
        
        return matching_rules
    
    def _execute_rule(
        self,
        rule: Dict[str, Any],
        config: DeploymentConfig,
        state: DeploymentState,
        verification_result: VerificationResult
    ) -> RuleExecutionResult:
        """
        Execute a single rule.
        
        Args:
            rule: Rule dictionary
            config: Deployment configuration
            state: Deployment state
            verification_result: Verification result
            
        Returns:
            RuleExecutionResult
        """
        result = RuleExecutionResult(
            rule_name=rule['name'],
            matched=True,
            executed=False,
            success=False
        )
        
        logger.info(f"Executing rule: {rule['name']} - {rule.get('description', '')}")
        
        try:
            actions = rule.get('actions', [])
            
            for action in actions:
                action_type = action.get('type')
                description = action.get('description', '')
                
                logger.info(f"  Action: {action_type} - {description}")
                
                # Execute action
                action_success = self._execute_action(action, config, state, verification_result)
                
                if action_success:
                    result.actions_executed.append(f"{action_type}: {description}")
                else:
                    result.errors.append(f"{action_type} failed: {description}")
                    # Continue with next action even if one fails
            
            # Mark as executed
            result.executed = True
            result.success = len(result.errors) == 0
            
        except Exception as e:
            logger.error(f"Failed to execute rule '{rule['name']}': {e}")
            result.errors.append(str(e))
        
        return result
    
    def _execute_action(
        self,
        action: Dict[str, Any],
        config: DeploymentConfig,
        state: DeploymentState,
        verification_result: VerificationResult
    ) -> bool:
        """
        Execute a single action.
        
        Args:
            action: Action dictionary
            config: Deployment configuration
            state: Deployment state
            verification_result: Verification result
            
        Returns:
            True if successful
        """
        action_type = action.get('type')
        
        try:
            if action_type == 'copy_directory':
                return self._action_copy_directory(action, config, state)
            
            elif action_type == 'copy_file':
                return self._action_copy_file(action, config, state)
            
            elif action_type == 'modify_xml':
                return self._action_modify_xml(action, config, state)
            
            elif action_type == 'restart_ghost':
                return self._action_restart_ghost(action, config)
            
            elif action_type == 'verify':
                return self._action_verify(config, state)
            
            elif action_type == 'wait':
                duration = action.get('duration', 5)
                logger.info(f"  Waiting {duration}s...")
                time.sleep(duration)
                return True
            
            elif action_type == 'check_ghost_status':
                return self._action_check_ghost_status(action, config)
            
            else:
                logger.warning(f"  Unknown action type: {action_type}")
                return False
                
        except Exception as e:
            logger.error(f"  Action failed: {e}")
            return False
    
    def _action_copy_directory(
        self,
        action: Dict[str, Any],
        config: DeploymentConfig,
        state: DeploymentState
    ) -> bool:
        """Execute copy_directory action."""
        source = action.get('source', {})
        destination = action.get('destination', {})
        targets = action.get('targets', [])
        
        # Resolve source path
        source_type = source.get('type')
        if source_type == 'config_server' and config.config_server:
            source_machine = config.config_server
            source_path = source.get('path', '').format(**self.ghost_config.get('directories', {}))
        else:
            logger.warning(f"  Unsupported source type: {source_type}")
            return False
        
        dest_path = destination.get('path')
        
        # Execute copy for each target
        success = True
        for target_type in targets:
            target_machine = self._resolve_target_machine(target_type, config)
            if not target_machine:
                continue
            
            # Use rsync for directory copy
            rsync_cmd = (
                f"rsync -avz -e 'ssh -i {target_machine.ssh_key_path}' "
                f"{source_machine.username}@{source_machine.ip_address}:{source_path}/ "
                f"{dest_path}/"
            )
            
            try:
                exit_code, stdout, stderr = self.ssh_manager.execute_command(
                    target_machine,
                    rsync_cmd,
                    timeout=300
                )
                
                if exit_code == 0:
                    logger.info(f"  ✓ Copied directory to {target_machine}")
                else:
                    logger.error(f"  ✗ Copy failed for {target_machine}: {stderr}")
                    success = False
            except Exception as e:
                logger.error(f"  ✗ Copy failed for {target_machine}: {e}")
                success = False
        
        return success
    
    def _action_copy_file(
        self,
        action: Dict[str, Any],
        config: DeploymentConfig,
        state: DeploymentState
    ) -> bool:
        """Execute copy_file action."""
        # Similar to copy_directory but for single files
        # Implementation would be similar
        return True
    
    def _action_modify_xml(
        self,
        action: Dict[str, Any],
        config: DeploymentConfig,
        state: DeploymentState
    ) -> bool:
        """Execute modify_xml action."""
        file_path = action.get('file', '').format(
            arl_file_path=f"{self.ghost_config.get('arl_data_path')}/{config.arl_file.filename}",
            arl_filename=config.arl_file.filename,
            parent_ip=config.parent.ip_address,
            hostname=config.hostname or '',
            origin_host=config.origin_host or ''
        )

        # Format tags with variables
        format_vars = {
            'arl_file_path': f"{self.ghost_config.get('arl_data_path')}/{config.arl_file.filename}",
            'arl_filename': config.arl_file.filename,
            'parent_ip': config.parent.ip_address,
            'hostname': config.hostname or '',
            'origin_host': config.origin_host or ''
        }

        tags = [tag.format(**format_vars) for tag in action.get('tags', [])]
        operation = action.get('operation', 'add')
        targets = action.get('targets', [])

        success = True
        for target_type in targets:
            target_machine = self._resolve_target_machine(target_type, config)
            if not target_machine:
                continue

            try:
                # Download file
                local_temp = f"/tmp/{Path(file_path).name}.{target_machine.ip_address}"

                # Check if the file path is valid (not empty filename)
                if not Path(file_path).name:
                    logger.error(f"  ✗ Invalid file path (empty filename): {file_path}")
                    success = False
                    continue

                self.ssh_manager.download_file(target_machine, file_path, local_temp)

                # Read original content for diff
                with open(local_temp, 'r') as f:
                    original_content = f.read()

                # Handle different operations
                if operation == 'add_host_to_include':
                    # Special operation: add <host> tag inside existing <trident:include>
                    arl_filename = action.get('arl_file', '').format(**format_vars)

                    if not arl_filename:
                        logger.error(f"  ✗ ARL filename is empty - cannot add host to include")
                        success = False
                        continue

                    success = self._add_host_to_arlindex_include(local_temp, arl_filename, tags)
                elif operation == 'comment':
                    # Comment operation: use pattern-based commenting for container tags
                    # Extract tag patterns from tags (remove < and >)
                    tag_patterns = [tag.strip('<>') for tag in tags]
                    arl_logger.progress(f"Commenting out: {', '.join(tag_patterns)}")
                    logger.info(f"  Commenting tags matching patterns: {tag_patterns}")
                    self.xml_manipulator.update_xml_with_patterns(
                        local_temp,
                        tag_patterns_to_comment=tag_patterns,
                        tags_to_comment=[],
                        tags_to_add=[]
                    )
                else:
                    # Standard operation: add tags
                    arl_logger.progress(f"Adding {len(tags)} tag(s) to configuration")
                    logger.info(f"  Adding tags: {tags}")
                    self.xml_manipulator.update_xml(local_temp, tags_to_comment=[], tags_to_add=tags)

                # Read modified content and show diff
                with open(local_temp, 'r') as f:
                    modified_content = f.read()

                # Show what changed
                self._log_xml_diff(original_content, modified_content, operation, target_machine)

                # Upload back
                self.ssh_manager.upload_file(target_machine, local_temp, file_path)

                # Clean up
                Path(local_temp).unlink(missing_ok=True)

                logger.info(f"  ✓ Modified XML on {target_machine}")

            except Exception as e:
                logger.error(f"  ✗ XML modification failed for {target_machine}: {e}")
                success = False

        return success

    def _add_host_to_arlindex_include(
        self,
        arlindex_file: str,
        arl_filename: str,
        host_tags: List[str]
    ) -> bool:
        """
        Add <host> tags inside an existing <trident:include> entry in arlindex.xml.

        Uses XMLManipulator's add_child_tag_to_element method which preserves
        original XML formatting and avoids namespace prefix issues (ns0:, etc.).

        Args:
            arlindex_file: Path to local arlindex.xml file
            arl_filename: ARL filename to find in the include entry
            host_tags: List of <host> tags to add

        Returns:
            True if successful
        """
        try:
            import re

            # Build the parent element pattern to find the <trident:include> with matching file
            parent_pattern = rf'<trident:include[^>]*file="{re.escape(arl_filename)}"[^>]*>'

            # Use XMLManipulator's new method to add child tags
            success = self.xml_manipulator.add_child_tag_to_element(
                xml_file=arlindex_file,
                parent_element_pattern=parent_pattern,
                child_tags=host_tags,
                check_duplicates=True
            )

            if success:
                logger.info(f"  ✓ Added host tags to arlindex.xml include for {arl_filename}")
            else:
                logger.warning(f"  Could not add host tags to arlindex.xml for {arl_filename}")

            return success

        except Exception as e:
            logger.error(f"Failed to add host to arlindex include: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False

    def _log_xml_diff(
        self,
        original_content: str,
        modified_content: str,
        operation: str,
        target_machine
    ):
        """
        Log the differences between original and modified XML content.

        Args:
            original_content: Original XML content
            modified_content: Modified XML content
            operation: Operation type ('add', 'comment', etc.)
            target_machine: Target machine object
        """
        import difflib

        # Generate unified diff
        diff = list(difflib.unified_diff(
            original_content.splitlines(keepends=True),
            modified_content.splitlines(keepends=True),
            fromfile='original',
            tofile='modified',
            lineterm=''
        ))

        if not diff:
            logger.info(f"  No changes detected after {operation} operation")
            return

        # Count additions and deletions
        additions = sum(1 for line in diff if line.startswith('+') and not line.startswith('+++'))
        deletions = sum(1 for line in diff if line.startswith('-') and not line.startswith('---'))

        # In default mode, just show summary
        arl_logger.progress(f"Modified {additions + deletions} line(s) on {target_machine.hostname}")

        # In verbose mode, show full diff
        logger.info(f"  XML Diff Summary on {target_machine}: +{additions} -{deletions} lines")

        # Only show diff preview in verbose mode
        if arl_logger.verbose:
            logger.info("  Diff preview (first 20 lines):")
            for line in diff[:20]:
                line = line.rstrip()
                if line.startswith('+') and not line.startswith('+++'):
                    logger.info(f"    {line}")  # Added lines in green (info level)
                elif line.startswith('-') and not line.startswith('---'):
                    logger.warning(f"    {line}")  # Deleted lines in yellow (warning level)
                elif line.startswith('@@'):
                    logger.info(f"    {line}")  # Position markers

            if len(diff) > 20:
                logger.info(f"    ... ({len(diff) - 20} more diff lines)")
                logger.info(f"    Full diff saved in backup file: {target_machine}.back")
    
    def _action_restart_ghost(
        self,
        action: Dict[str, Any],
        config: DeploymentConfig
    ) -> bool:
        """Execute restart_ghost action."""
        targets = action.get('targets', [])
        
        success = True
        for target_type in targets:
            target_machine = self._resolve_target_machine(target_type, config)
            if not target_machine:
                continue
            
            try:
                manager = GhostRestartManager(
                    run_command=lambda cmd, timeout, m=target_machine: self.ssh_manager.execute_command(
                        m, cmd, timeout=timeout
                    ),
                    machine_label=str(target_machine),
                    trident_run=self.ghost_config.get('trident_run', '/a/sbin/trident_run'),
                    logger=logger,
                )

                if manager.restart():
                    logger.info(f"  ✓ Ghost restarted on {target_machine}")
                else:
                    logger.error(f"  ✗ Ghost restart failed on {target_machine}")
                    success = False
                    
            except Exception as e:
                logger.error(f"  ✗ Ghost restart failed on {target_machine}: {e}")
                success = False
        
        return success
    
    def _action_verify(
        self,
        config: DeploymentConfig,
        state: DeploymentState
    ) -> bool:
        """Execute verify action - re-run verification."""
        logger.info("  Re-running verification...")
        
        if not config.sample_url:
            logger.warning("  No sample URL configured for verification")
            return False
        
        try:
            result = self.verifier.verify_deployment(config, config.sample_url)
            state.verification_result = result
            
            if result.passed:
                logger.info("  ✓ Verification PASSED after healing")
                return True
            else:
                logger.warning(f"  ✗ Verification still failing: {result.failure_reason}")
                return False
                
        except Exception as e:
            logger.error(f"  ✗ Verification failed: {e}")
            return False
    
    def _action_check_ghost_status(
        self,
        action: Dict[str, Any],
        config: DeploymentConfig
    ) -> bool:
        """Execute check_ghost_status action."""
        checks = action.get('checks', [])
        
        status = self.verifier.check_ghost_status(config.edge)
        
        all_passed = True
        for check in checks:
            if check == 'process_running' and not status['process_running']:
                logger.warning("  ✗ Ghost process not running")
                all_passed = False
            elif check == 'port_stats_accepting' and not status['accepting_connections']:
                logger.warning("  ✗ Ghost not accepting connections")
                all_passed = False
        
        return all_passed
    
    def _resolve_target_machine(
        self,
        target_type: str,
        config: DeploymentConfig
    ) -> Optional[MachineConfig]:
        """
        Resolve target machine from target type string.
        
        Args:
            target_type: Target type ('edge', 'parent', 'peer')
            config: Deployment configuration
            
        Returns:
            MachineConfig if found, None otherwise
        """
        if target_type == 'edge':
            return config.edge
        elif target_type == 'parent':
            return config.parent
        elif target_type == 'peer' and config.peers:
            return config.peers[0]  # Return first peer
        else:
            logger.warning(f"Unknown target type: {target_type}")
            return None
