"""
Trident ARL Deployer - Automated customer configuration deployment tool.

This package provides tools for deploying Trident customer configurations (ARL files)
to edge, parent, and peer machines with automatic verification and self-healing capabilities.
"""

__version__ = "0.1.0"
__author__ = "Trident QA Team"

from arl_deployer.models import (
    DeploymentConfig,
    MachineConfig,
    DeploymentState,
    VerificationResult,
)

__all__ = [
    "DeploymentConfig",
    "MachineConfig",
    "DeploymentState",
    "VerificationResult",
]
