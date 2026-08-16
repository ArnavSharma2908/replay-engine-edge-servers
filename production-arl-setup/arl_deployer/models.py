"""
Data models for ARL Deployer.

This module defines all data classes and enums used throughout the application.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any
from datetime import datetime


class MachineType(Enum):
    """Types of Ghost machines in the deployment topology."""
    EDGE = "edge"
    PARENT = "parent"
    PEER = "peer"
    CONFIG_SERVER = "config_server"


class DeploymentStatus(Enum):
    """Status of a deployment."""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    DEPLOYED = "deployed"
    VERIFIED = "verified"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class VerificationStatus(Enum):
    """Status of verification."""
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"


class ActionType(Enum):
    """Types of actions in the rule engine."""
    COPY_FILE = "copy_file"
    COPY_DIRECTORY = "copy_directory"
    MODIFY_XML = "modify_xml"
    RESTART_GHOST = "restart_ghost"
    VERIFY = "verify"
    WAIT = "wait"
    CHECK_GHOST_STATUS = "check_ghost_status"


@dataclass
class MachineConfig:
    """Configuration for a Ghost machine."""
    hostname: str
    ip_address: str
    machine_type: MachineType
    username: str = "root"
    ssh_key_path: str = "/root/.ssh/identity"
    
    def __str__(self) -> str:
        return f"{self.machine_type.value}@{self.ip_address}"


@dataclass
class ARLFileInfo:
    """Information about an ARL file."""
    filename: str
    arlid: str
    cpcode: str
    hostnames: List[str]
    remote_path: Optional[str] = None
    local_path: Optional[str] = None
    requires_hd_data: bool = False
    requires_waf_data: bool = False
    requires_pearl_data: bool = False

    def __str__(self) -> str:
        return f"{self.filename} (CPCode: {self.cpcode}, Hosts: {', '.join(self.hostnames)})"


@dataclass
class DeploymentConfig:
    """Complete configuration for a deployment."""
    # Required fields first (no defaults)
    deployment_id: str
    arl_file: ARLFileInfo
    edge: MachineConfig
    parent: MachineConfig
    
    # Optional fields (with defaults)
    timestamp: datetime = field(default_factory=datetime.now)
    hostname: Optional[str] = None  # User-provided hostname from CLI (deprecated, use hostnames)
    hostnames: List[str] = field(default_factory=list)  # User-provided hostnames from CLI (supports multiple)
    sample_url: Optional[str] = None
    origin_host: Optional[str] = None  # Origin host for forward configuration (used by rules)
    peers: List[MachineConfig] = field(default_factory=list)
    config_server: Optional[MachineConfig] = None
    create_backups: bool = True
    auto_rollback_on_failure: bool = False
    max_healing_attempts: int = 3
    
    def get_all_machines(self) -> List[MachineConfig]:
        """Get all machines in the deployment."""
        machines = [self.edge, self.parent]
        machines.extend(self.peers)
        if self.config_server:
            machines.append(self.config_server)
        return machines
    
    def get_ghost_machines(self) -> List[MachineConfig]:
        """Get only Ghost machines (edge, parent, peers)."""
        machines = [self.edge, self.parent]
        machines.extend(self.peers)
        return machines


@dataclass
class BackupInfo:
    """Information about a backed up file."""
    original_path: str
    backup_path: str
    machine: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class DeploymentState:
    """State tracking for a deployment."""
    config: DeploymentConfig
    status: DeploymentStatus = DeploymentStatus.NOT_STARTED
    
    # Tracking
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    backups: List[BackupInfo] = field(default_factory=list)
    deployed_files: List[Dict[str, str]] = field(default_factory=list)
    
    # Results
    verification_result: Optional['VerificationResult'] = None
    errors: List[str] = field(default_factory=list)
    healing_attempts: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'deployment_id': self.config.deployment_id,
            'status': self.status.value,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'arl_file': str(self.config.arl_file),
            'edge': str(self.config.edge),
            'parent': str(self.config.parent),
            'peers': [str(p) for p in self.config.peers],
            'backups': [
                {
                    'original_path': b.original_path,
                    'backup_path': b.backup_path,
                    'machine': b.machine,
                    'timestamp': b.timestamp.isoformat()
                }
                for b in self.backups
            ],
            'deployed_files': self.deployed_files,
            'verification_passed': self.verification_result.passed if self.verification_result else None,
            'errors': self.errors,
            'healing_attempts': self.healing_attempts,
        }


@dataclass
class FLineData:
    """Parsed data from a Ghost access log f-line."""
    timestamp: str
    forward_ip: str
    client_ip: str
    method: str
    arl: str
    status_code: str
    content_type: str
    object_status: str
    raw_line: str
    
    # Additional parsed fields
    ssl_overhead: str = "-"
    request_end_time: str = "-"
    dns_lookup_time: str = "-"
    turnaround_time: str = "-"
    transfer_time: str = "-"
    content_bytes: str = "-"
    total_bytes: str = "-"
    error_field: str = "-"  # Field 29 (index 28)


@dataclass
class RLineData:
    """Parsed data from a Ghost access log r-line."""
    timestamp: str
    client_ip: str
    method: str
    arl: str
    status_code: str
    error_code: str
    raw_line: str


@dataclass
class VerificationResult:
    """Result of deployment verification."""
    passed: bool
    timestamp: datetime = field(default_factory=datetime.now)
    
    # Test details
    sample_url: str = ""
    curl_command: str = ""
    curl_output: str = ""
    curl_exit_code: int = 0
    
    # Log analysis
    f_line_found: bool = False
    f_line_data: Optional[FLineData] = None
    r_line_data: Optional[RLineData] = None
    expected_forward_ip: str = ""
    actual_forward_ip: str = ""
    
    # Failure details
    failure_reason: str = ""
    suggested_fixes: List[str] = field(default_factory=list)
    
    def __str__(self) -> str:
        if self.passed:
            return f"✓ Verification PASSED - Forward IP: {self.actual_forward_ip}"
        else:
            return f"✗ Verification FAILED - {self.failure_reason}"


@dataclass
class RuleCondition:
    """Condition for triggering a rule."""
    type: str  # log_pattern, verification_failure, file_not_exists, etc.
    patterns: List[str] = field(default_factory=list)
    log_types: List[str] = field(default_factory=list)
    reason: Optional[str] = None
    additional_checks: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class RuleAction:
    """Action to execute when rule is triggered."""
    type: ActionType
    description: str
    targets: List[str] = field(default_factory=list)
    
    # Action-specific parameters
    source: Optional[Dict[str, str]] = None
    destination: Optional[Dict[str, str]] = None
    file: Optional[str] = None
    operation: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    command: Optional[str] = None
    duration: Optional[int] = None
    checks: List[str] = field(default_factory=list)


@dataclass
class Rule:
    """Self-healing rule."""
    name: str
    description: str
    enabled: bool
    priority: int
    condition: RuleCondition
    actions: List[RuleAction]
    
    def matches(self, verification_result: VerificationResult, logs: Dict[str, str]) -> bool:
        """Check if this rule's condition matches the current situation."""
        # Implementation in rules.py
        return False


@dataclass
class RuleExecutionResult:
    """Result of executing a rule."""
    rule_name: str
    matched: bool
    executed: bool
    success: bool
    actions_executed: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    
    def __str__(self) -> str:
        if not self.matched:
            return f"Rule '{self.rule_name}': Not matched"
        if not self.executed:
            return f"Rule '{self.rule_name}': Matched but not executed"
        if self.success:
            return f"Rule '{self.rule_name}': ✓ Success - {', '.join(self.actions_executed)}"
        else:
            return f"Rule '{self.rule_name}': ✗ Failed - {', '.join(self.errors)}"
