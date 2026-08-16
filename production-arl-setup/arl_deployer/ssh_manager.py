"""
SSH Manager for handling remote operations.

This module provides a wrapper around Paramiko for SSH/SCP operations with
retry logic, connection pooling, and error handling.
"""

import logging
import time
from typing import Tuple, Optional, Dict, List
from pathlib import Path

import paramiko
from paramiko import SSHClient, RSAKey, AutoAddPolicy

# Try to import legacy key types (may not be available in newer Paramiko versions)
try:
    from paramiko.dsskey import DSSKey
    DSS_AVAILABLE = True
except ImportError:
    DSS_AVAILABLE = False
    DSSKey = None

try:
    from paramiko.ecdsakey import ECDSAKey
except ImportError:
    from paramiko import ECDSAKey

try:
    from paramiko.ed25519key import Ed25519Key
except ImportError:
    from paramiko import Ed25519Key

from arl_deployer.models import MachineConfig

logger = logging.getLogger(__name__)


class SSHConnectionError(Exception):
    """Raised when SSH connection fails."""
    pass


class SSHCommandError(Exception):
    """Raised when SSH command execution fails."""
    pass


class SCPTransferError(Exception):
    """Raised when SCP file transfer fails."""
    pass


class SSHManager:
    """
    Manages SSH connections and operations to remote machines.
    
    Provides methods for:
    - Pre-flight connectivity checks
    - Remote command execution
    - SCP file upload/download
    - Directory operations
    """
    
    def __init__(
        self,
        connect_timeout: int = 10,
        command_timeout: int = 30,
        transfer_timeout: int = 300,
        max_retries: int = 3,
        retry_delay: int = 5
    ):
        """
        Initialize SSH Manager.
        
        Args:
            connect_timeout: SSH connection timeout in seconds
            command_timeout: Command execution timeout in seconds
            transfer_timeout: File transfer timeout in seconds
            max_retries: Maximum number of retry attempts
            retry_delay: Delay between retries in seconds
        """
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self.transfer_timeout = transfer_timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        # Connection pool to reuse connections
        self._connections: Dict[str, SSHClient] = {}
    
    def _get_connection_key(self, machine: MachineConfig) -> str:
        """Generate a unique key for connection caching."""
        return f"{machine.username}@{machine.ip_address}"
    
    def _create_ssh_client(self, machine: MachineConfig) -> SSHClient:
        """
        Create and configure an SSH client.
        
        Args:
            machine: Machine configuration
            
        Returns:
            Configured SSHClient instance
            
        Raises:
            SSHConnectionError: If connection fails
        """
        client = SSHClient()
        client.set_missing_host_key_policy(AutoAddPolicy())
        
        # Configure Paramiko to accept legacy host key algorithms
        # This is needed for older SSH servers that only support ssh-rsa, ssh-dss, etc.
        import paramiko.transport
        
        # Build list of preferred keys (include ssh-dss only if available)
        preferred_keys = ['ssh-rsa', 'rsa-sha2-256', 'rsa-sha2-512']
        if DSS_AVAILABLE:
            preferred_keys.append('ssh-dss')
        preferred_keys.extend([
            'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 
            'ecdsa-sha2-nistp521', 'ssh-ed25519'
        ])
        paramiko.transport.Transport._preferred_keys = tuple(preferred_keys)
        
        # Build key handler registry
        key_info = {
            'ssh-rsa': RSAKey,
            'rsa-sha2-256': RSAKey,
            'rsa-sha2-512': RSAKey,
            'ecdsa-sha2-nistp256': ECDSAKey,
            'ecdsa-sha2-nistp384': ECDSAKey,
            'ecdsa-sha2-nistp521': ECDSAKey,
            'ssh-ed25519': Ed25519Key,
        }
        if DSS_AVAILABLE and DSSKey is not None:
            key_info['ssh-dss'] = DSSKey
        
        paramiko.transport.Transport._key_info = key_info
        
        try:
            # Load private key
            private_key = RSAKey.from_private_key_file(machine.ssh_key_path)
            
            # Connect
            logger.debug(
                f"Connecting to {machine.ip_address} as {machine.username} "
                f"with key {machine.ssh_key_path}"
            )
            
            client.connect(
                hostname=machine.ip_address,
                username=machine.username,
                pkey=private_key,
                timeout=self.connect_timeout,
                look_for_keys=False,
                allow_agent=False
            )

            logger.debug(f"Connected to {machine}")
            return client
            
        except FileNotFoundError:
            raise SSHConnectionError(f"SSH key not found: {machine.ssh_key_path}")
        except paramiko.AuthenticationException:
            raise SSHConnectionError(f"Authentication failed for {machine}")
        except paramiko.SSHException as e:
            raise SSHConnectionError(f"SSH error connecting to {machine}: {e}")
        except Exception as e:
            raise SSHConnectionError(f"Failed to connect to {machine}: {e}")
    
    def get_connection(self, machine: MachineConfig, reuse: bool = True) -> SSHClient:
        """
        Get an SSH connection to a machine.
        
        Args:
            machine: Machine configuration
            reuse: Whether to reuse existing connection
            
        Returns:
            SSHClient instance
            
        Raises:
            SSHConnectionError: If connection fails
        """
        conn_key = self._get_connection_key(machine)
        
        # Check if we have a cached connection
        if reuse and conn_key in self._connections:
            client = self._connections[conn_key]
            # Test if connection is still alive
            try:
                transport = client.get_transport()
                if transport and transport.is_active():
                    return client
            except Exception:
                # Connection is dead, remove it
                del self._connections[conn_key]
        
        # Create new connection
        client = self._create_ssh_client(machine)
        if reuse:
            self._connections[conn_key] = client
        
        return client
    
    def check_connectivity(self, machine: MachineConfig) -> Tuple[bool, str]:
        """
        Pre-flight connectivity check to a machine.
        
        Args:
            machine: Machine configuration
            
        Returns:
            Tuple of (success, message)
        """
        try:
            client = self.get_connection(machine, reuse=False)
            client.close()
            return True, f"✓ Connectivity check passed for {machine}"
        except SSHConnectionError as e:
            return False, f"✗ Connectivity check failed for {machine}: {e}"
    
    def execute_command(
        self,
        machine: MachineConfig,
        command: str,
        timeout: Optional[int] = None
    ) -> Tuple[int, str, str]:
        """
        Execute a command on a remote machine.
        
        Args:
            machine: Machine configuration
            command: Command to execute
            timeout: Command timeout (uses default if None)
            
        Returns:
            Tuple of (exit_code, stdout, stderr)
            
        Raises:
            SSHCommandError: If command execution fails
        """
        timeout = timeout or self.command_timeout
        
        for attempt in range(1, self.max_retries + 1):
            try:
                client = self.get_connection(machine)
                
                logger.debug(f"Executing on {machine}: {command}")
                stdin, stdout, stderr = client.exec_command(
                    command,
                    timeout=timeout,
                    get_pty=False
                )
                
                # Read output
                exit_code = stdout.channel.recv_exit_status()
                stdout_str = stdout.read().decode('utf-8', errors='replace')
                stderr_str = stderr.read().decode('utf-8', errors='replace')
                
                logger.debug(
                    f"Command completed with exit code {exit_code}\n"
                    f"Stdout: {stdout_str[:200]}{'...' if len(stdout_str) > 200 else ''}\n"
                    f"Stderr: {stderr_str[:200]}{'...' if len(stderr_str) > 200 else ''}"
                )
                
                return exit_code, stdout_str, stderr_str
                
            except paramiko.SSHException as e:
                if attempt < self.max_retries:
                    logger.warning(
                        f"Command failed on attempt {attempt}/{self.max_retries}: {e}. "
                        f"Retrying in {self.retry_delay}s..."
                    )
                    time.sleep(self.retry_delay)
                else:
                    raise SSHCommandError(f"Command failed after {self.max_retries} attempts: {e}")
            except Exception as e:
                raise SSHCommandError(f"Failed to execute command on {machine}: {e}")
        
        raise SSHCommandError("Unexpected error in execute_command")
    
    def upload_file(
        self,
        machine: MachineConfig,
        local_path: str,
        remote_path: str
    ) -> bool:
        """
        Upload a file to a remote machine via SCP.
        
        Args:
            machine: Machine configuration
            local_path: Local file path
            remote_path: Remote file path
            
        Returns:
            True if successful
            
        Raises:
            SCPTransferError: If transfer fails
        """
        if not Path(local_path).exists():
            raise SCPTransferError(f"Local file not found: {local_path}")
        
        for attempt in range(1, self.max_retries + 1):
            try:
                client = self.get_connection(machine)
                sftp = client.open_sftp()
                
                logger.info(f"Uploading {local_path} to {machine}:{remote_path}")
                sftp.put(local_path, remote_path, confirm=True)
                sftp.close()
                
                logger.info(f"✓ Successfully uploaded {local_path} to {machine}:{remote_path}")
                return True
                
            except paramiko.SSHException as e:
                if attempt < self.max_retries:
                    logger.warning(
                        f"Upload failed on attempt {attempt}/{self.max_retries}: {e}. "
                        f"Retrying in {self.retry_delay}s..."
                    )
                    time.sleep(self.retry_delay)
                else:
                    raise SCPTransferError(
                        f"Failed to upload {local_path} to {machine}:{remote_path} "
                        f"after {self.max_retries} attempts: {e}"
                    )
            except Exception as e:
                raise SCPTransferError(
                    f"Failed to upload {local_path} to {machine}:{remote_path}: {e}"
                )
        
        return False
    
    def download_file(
        self,
        machine: MachineConfig,
        remote_path: str,
        local_path: str
    ) -> bool:
        """
        Download a file from a remote machine via SCP.
        
        Args:
            machine: Machine configuration
            remote_path: Remote file path
            local_path: Local file path
            
        Returns:
            True if successful
            
        Raises:
            SCPTransferError: If transfer fails
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                client = self.get_connection(machine)
                sftp = client.open_sftp()
                
                logger.info(f"Downloading {machine}:{remote_path} to {local_path}")
                
                # Ensure local directory exists
                Path(local_path).parent.mkdir(parents=True, exist_ok=True)
                
                sftp.get(remote_path, local_path)
                sftp.close()
                
                logger.info(f"✓ Successfully downloaded {machine}:{remote_path} to {local_path}")
                return True
                
            except FileNotFoundError:
                raise SCPTransferError(f"Remote file not found: {machine}:{remote_path}")
            except paramiko.SSHException as e:
                if attempt < self.max_retries:
                    logger.warning(
                        f"Download failed on attempt {attempt}/{self.max_retries}: {e}. "
                        f"Retrying in {self.retry_delay}s..."
                    )
                    time.sleep(self.retry_delay)
                else:
                    raise SCPTransferError(
                        f"Failed to download {machine}:{remote_path} to {local_path} "
                        f"after {self.max_retries} attempts: {e}"
                    )
            except Exception as e:
                raise SCPTransferError(
                    f"Failed to download {machine}:{remote_path} to {local_path}: {e}"
                )
        
        return False
    
    def sync_directory_via_rsync(
        self,
        source_machine: MachineConfig,
        source_path: str,
        target_machine: MachineConfig,
        target_path: str
    ) -> bool:
        """
        Sync directory using rsync on the source machine (direct machine-to-machine).

        This executes rsync on the source machine to push directly to target machine.
        Much faster than download+upload, but requires SSH key access between machines.

        Args:
            source_machine: Source machine configuration
            source_path: Source directory path (should end with / to copy contents)
            target_machine: Target machine configuration
            target_path: Target directory path

        Returns:
            True if successful
        """
        logger.info(f"Using rsync to sync {source_path} from {source_machine} to {target_machine}:{target_path}")

        # Ensure target directory exists
        mkdir_cmd = f"mkdir -p {target_path}"
        exit_code, stdout, stderr = self.execute_command(target_machine, mkdir_cmd)
        if exit_code != 0:
            logger.warning(f"Failed to create target directory (may already exist): {stderr}")

        # Build rsync command
        # -a: archive mode (preserves permissions, timestamps, etc.)
        # -v: verbose
        # -z: compress during transfer
        # Note: We use ~/.ssh/identity as the SSH key path on the remote machine
        # This is the standard location on Linux servers
        rsync_cmd = (
            f"rsync -avz "
            f"-e 'ssh -o StrictHostKeyChecking=no -i ~/.ssh/identity' "
            f"{source_path} "
            f"{target_machine.username}@{target_machine.ip_address}:{target_path}"
        )

        logger.info(f"Executing rsync command on {source_machine}...")
        logger.debug(f"Command: {rsync_cmd}")

        exit_code, stdout, stderr = self.execute_command(
            source_machine,
            rsync_cmd,
            timeout=600  # 10 minute timeout for large directories
        )

        if exit_code == 0:
            logger.info(f"✓ Successfully synced directory via rsync")
            return True
        else:
            logger.error(f"✗ Rsync failed with exit code {exit_code}")
            logger.error(f"Stderr: {stderr}")
            return False

    def sync_directory(
        self,
        source_machine: MachineConfig,
        source_path: str,
        target_machine: MachineConfig,
        target_path: str,
        use_rsync: bool = True
    ) -> bool:
        """
        Recursively sync a directory from source machine to target machine.

        By default, uses rsync for direct machine-to-machine transfer (fast).
        Falls back to SFTP download+upload if rsync fails or use_rsync=False.

        Args:
            source_machine: Source machine configuration
            source_path: Source directory path (should end with / for rsync)
            target_machine: Target machine configuration
            target_path: Target directory path
            use_rsync: If True, try rsync first; if False, use SFTP only

        Returns:
            True if successful

        Raises:
            SCPTransferError: If transfer fails
        """
        # Try rsync first (much faster for large directories)
        if use_rsync:
            try:
                if self.sync_directory_via_rsync(source_machine, source_path, target_machine, target_path):
                    return True
                else:
                    logger.warning("Rsync failed, falling back to SFTP method...")
            except Exception as e:
                logger.warning(f"Rsync method failed: {e}, falling back to SFTP...")

        # Fallback to SFTP download+upload method
        logger.info("Using SFTP download+upload method (slower)...")
        return self._sync_directory_via_sftp(source_machine, source_path, target_machine, target_path)

    def _sync_directory_via_sftp(
        self,
        source_machine: MachineConfig,
        source_path: str,
        target_machine: MachineConfig,
        target_path: str
    ) -> bool:
        """
        SFTP-based directory sync (downloads locally first, then uploads).

        This is slower but doesn't require SSH keys between machines.

        Args:
            source_machine: Source machine configuration
            source_path: Source directory path
            target_machine: Target machine configuration
            target_path: Target directory path

        Returns:
            True if successful

        Raises:
            SCPTransferError: If transfer fails
        """
        import tempfile
        import shutil
        from pathlib import Path
        
        # Create temp directory for intermediate storage
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_local = Path(temp_dir) / "sync_data"
            temp_local.mkdir(parents=True, exist_ok=True)
            
            logger.info(f"Syncing directory from {source_machine}:{source_path} to {target_machine}:{target_path}")
            
            try:
                # Download from source machine
                source_client = self.get_connection(source_machine)
                source_sftp = source_client.open_sftp()
                
                # Recursively download directory
                self._sftp_get_recursive(source_sftp, source_path, str(temp_local))
                source_sftp.close()
                
                # Upload to target machine
                target_client = self.get_connection(target_machine)
                target_sftp = target_client.open_sftp()
                
                # Create target directory if it doesn't exist
                try:
                    target_sftp.stat(target_path)
                except FileNotFoundError:
                    self._sftp_mkdir_recursive(target_sftp, target_path)
                
                # Recursively upload directory
                self._sftp_put_recursive(target_sftp, str(temp_local), target_path)
                target_sftp.close()
                
                logger.info(f"✓ Successfully synced directory to {target_machine}:{target_path}")
                return True
                
            except Exception as e:
                logger.error(f"Failed to sync directory: {e}")
                raise SCPTransferError(f"Directory sync failed: {e}")
    
    def _sftp_get_recursive(self, sftp, remote_dir: str, local_dir: str):
        """Recursively download directory via SFTP."""
        from pathlib import Path

        local_path = Path(local_dir)
        local_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"Scanning directory: {remote_dir}")
        items = sftp.listdir_attr(remote_dir)
        logger.info(f"  Found {len(items)} items")

        for item in items:
            remote_path = f"{remote_dir.rstrip('/')}/{item.filename}"
            local_item = local_path / item.filename

            if self._sftp_is_dir(item):
                logger.info(f"  Descending into subdirectory: {item.filename}")
                self._sftp_get_recursive(sftp, remote_path, str(local_item))
            else:
                size_mb = item.st_size / (1024 * 1024)
                logger.info(f"  Downloading: {item.filename} ({size_mb:.2f} MB)")
                sftp.get(remote_path, str(local_item))
    
    def _sftp_put_recursive(self, sftp, local_dir: str, remote_dir: str):
        """Recursively upload directory via SFTP."""
        from pathlib import Path

        local_path = Path(local_dir)

        items = list(local_path.iterdir())
        logger.info(f"Uploading directory: {local_path.name} ({len(items)} items)")

        for item in items:
            remote_path = f"{remote_dir.rstrip('/')}/{item.name}"

            if item.is_dir():
                # Create remote directory
                try:
                    sftp.stat(remote_path)
                except FileNotFoundError:
                    logger.info(f"  Creating directory: {item.name}")
                    sftp.mkdir(remote_path)

                # Recurse into directory
                self._sftp_put_recursive(sftp, str(item), remote_path)
            else:
                size_mb = item.stat().st_size / (1024 * 1024)
                logger.info(f"  Uploading: {item.name} ({size_mb:.2f} MB)")
                sftp.put(str(item), remote_path, confirm=True)
    
    def _sftp_mkdir_recursive(self, sftp, remote_path: str):
        """Create directory recursively via SFTP."""
        parts = remote_path.strip('/').split('/')
        current = ''
        
        for part in parts:
            current += f'/{part}'
            try:
                sftp.stat(current)
            except FileNotFoundError:
                sftp.mkdir(current)
    
    def _sftp_is_dir(self, attr) -> bool:
        """Check if SFTP file attribute represents a directory."""
        import stat
        return stat.S_ISDIR(attr.st_mode)
    
    def file_exists(self, machine: MachineConfig, remote_path: str) -> bool:
        """
        Check if a file exists on a remote machine.
        
        Args:
            machine: Machine configuration
            remote_path: Remote file path
            
        Returns:
            True if file exists
        """
        try:
            exit_code, _, _ = self.execute_command(
                machine,
                f"test -f {remote_path} && echo 'exists' || echo 'not found'"
            )
            return exit_code == 0
        except SSHCommandError:
            return False
    
    def directory_exists(self, machine: MachineConfig, remote_path: str) -> bool:
        """
        Check if a directory exists on a remote machine.
        
        Args:
            machine: Machine configuration
            remote_path: Remote directory path
            
        Returns:
            True if directory exists
        """
        try:
            exit_code, _, _ = self.execute_command(
                machine,
                f"test -d {remote_path} && echo 'exists' || echo 'not found'"
            )
            return exit_code == 0
        except SSHCommandError:
            return False
    
    def create_directory(
        self,
        machine: MachineConfig,
        remote_path: str,
        parents: bool = True
    ) -> bool:
        """
        Create a directory on a remote machine.
        
        Args:
            machine: Machine configuration
            remote_path: Remote directory path
            parents: Create parent directories if needed
            
        Returns:
            True if successful
        """
        try:
            flag = "-p" if parents else ""
            exit_code, stdout, stderr = self.execute_command(
                machine,
                f"mkdir {flag} {remote_path}"
            )
            if exit_code == 0:
                logger.info(f"✓ Created directory {machine}:{remote_path}")
                return True
            else:
                logger.error(f"✗ Failed to create directory {machine}:{remote_path}: {stderr}")
                return False
        except SSHCommandError as e:
            logger.error(f"✗ Failed to create directory {machine}:{remote_path}: {e}")
            return False
    
    def list_directory(self, machine: MachineConfig, remote_path: str) -> List[str]:
        """
        List files in a remote directory.
        
        Args:
            machine: Machine configuration
            remote_path: Remote directory path
            
        Returns:
            List of filenames
        """
        try:
            exit_code, stdout, stderr = self.execute_command(
                machine,
                f"ls -1 {remote_path}"
            )
            if exit_code == 0:
                return [line.strip() for line in stdout.split('\n') if line.strip()]
            else:
                logger.error(f"✗ Failed to list directory {machine}:{remote_path}: {stderr}")
                return []
        except SSHCommandError as e:
            logger.error(f"✗ Failed to list directory {machine}:{remote_path}: {e}")
            return []
    
    def close_all(self):
        """Close all cached SSH connections."""
        for conn_key, client in self._connections.items():
            try:
                client.close()
                logger.debug(f"Closed connection: {conn_key}")
            except Exception as e:
                logger.warning(f"Error closing connection {conn_key}: {e}")
        
        self._connections.clear()
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close all connections."""
        self.close_all()
