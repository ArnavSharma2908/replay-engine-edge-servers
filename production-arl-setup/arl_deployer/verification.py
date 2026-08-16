"""
Verification module for testing deployments.

This module handles executing test requests and parsing Ghost access logs
to verify that the deployment is working correctly.
"""

import logging
import time
import re
from typing import Optional, Dict, List
from datetime import datetime

from arl_deployer.models import (
    VerificationResult,
    FLineData,
    RLineData,
    MachineConfig,
    DeploymentConfig,
)
from arl_deployer.ssh_manager import SSHManager

logger = logging.getLogger(__name__)


class VerificationError(Exception):
    """Raised when verification fails."""
    pass


class LogParser:
    """Parser for Ghost access log lines."""
    
    @staticmethod
    def parse_f_line(line: str) -> Optional[FLineData]:
        """
        Parse an f-line from Ghost access log.

        F-line format (space-delimited):
        f timestamp ssl_overhead request_end_time dns_lookup_time turnaround_time
        transfer_time content_bytes total_bytes forward_ip client_ip method arl
        status_code content_type ims ssl object_status ... [field_29_error] ...

        Args:
            line: Raw f-line from access log

        Returns:
            FLineData if successfully parsed, None otherwise
        """
        if not line.startswith('f '):
            return None

        try:
            parts = line.split()

            if len(parts) < 18:
                logger.warning(f"F-line has insufficient fields: {len(parts)}")
                return None

            return FLineData(
                timestamp=parts[1],
                ssl_overhead=parts[2],
                request_end_time=parts[3],
                dns_lookup_time=parts[4],
                turnaround_time=parts[5],
                transfer_time=parts[6],
                content_bytes=parts[7],
                total_bytes=parts[8],
                forward_ip=parts[9],  # Field 10 (index 9)
                client_ip=parts[10],
                method=parts[11],
                arl=parts[12],
                status_code=parts[13],
                content_type=parts[14],
                object_status=parts[17] if len(parts) > 17 else "-",
                error_field=parts[28] if len(parts) > 28 else "-",  # Field 29 (index 28)
                raw_line=line
            )
        except Exception as e:
            logger.error(f"Failed to parse f-line: {e}\nLine: {line}")
            return None
    
    @staticmethod
    def parse_r_line(line: str) -> Optional[RLineData]:
        """
        Parse an r-line from Ghost access log.

        R-line format (space-delimited):
        r timestamp ... client_ip method arl status_code error_code ...

        Args:
            line: Raw r-line from access log

        Returns:
            RLineData if successfully parsed, None otherwise
        """
        if not line.startswith('r '):
            return None

        try:
            parts = line.split()

            if len(parts) < 14:
                logger.warning(f"R-line has insufficient fields: {len(parts)} (need at least 14)")
                return None

            logger.debug(f"Parsing r-line with {len(parts)} fields")
            logger.debug(f"  Field 10 (index 9) client_ip: {parts[9]}")
            logger.debug(f"  Field 11 (index 10) method: {parts[10]}")
            logger.debug(f"  Field 12 (index 11) arl: {parts[11][:80]}")
            logger.debug(f"  Field 13 (index 12) status: {parts[12]}")
            logger.debug(f"  Field 14 (index 13) error: {parts[13]}")

            return RLineData(
                timestamp=parts[1],
                client_ip=parts[9],     # Field 10 (index 9)
                method=parts[10],       # Field 11 (index 10)
                arl=parts[11],          # Field 12 (index 11)
                status_code=parts[12],  # Field 13 (index 12) - HTTP status code
                error_code=parts[13],   # Field 14 (index 13) - Error code (e.g., ERR_ACCESS_DENIED, or "-")
                raw_line=line
            )
        except Exception as e:
            logger.error(f"Failed to parse r-line: {e}\nLine: {line}")
            return None


class Verifier:
    """
    Verifies deployment by executing test requests and analyzing access logs.
    
    Responsibilities:
    - Execute curl test requests
    - Parse Ghost access logs (f-lines and r-lines)
    - Validate forward IP matches parent
    - Provide detailed failure analysis
    """
    
    def __init__(
        self,
        ssh_manager: SSHManager,
        log_flush_wait: int = 5,
        log_tail_lines: int = 100,
        curl_timeout: int = 30
    ):
        """
        Initialize Verifier.
        
        Args:
            ssh_manager: SSH manager for remote operations
            log_flush_wait: Seconds to wait for log flush after request
            log_tail_lines: Number of log lines to tail
            curl_timeout: Timeout for curl requests
        """
        self.ssh_manager = ssh_manager
        self.log_flush_wait = log_flush_wait
        self.log_tail_lines = log_tail_lines
        self.curl_timeout = curl_timeout
        self.log_parser = LogParser()
    
    def verify_deployment(
        self,
        config: DeploymentConfig,
        sample_url: str,
        access_log_path: str = "/a/logs/ghost.access.log"
    ) -> VerificationResult:
        """
        Verify deployment by testing a request and checking logs.

        Args:
            config: Deployment configuration
            sample_url: Sample URL to test
            access_log_path: Path to Ghost access log

        Returns:
            VerificationResult with detailed analysis
        """
        # Add timestamp query parameter to ensure origin fetch (no cache)
        import time as time_module
        from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

        parsed_url = urlparse(sample_url)
        query_params = parse_qs(parsed_url.query)
        query_params['t'] = [str(int(time_module.time() * 1000))]  # Millisecond timestamp
        new_query = urlencode(query_params, doseq=True)
        timestamped_url = urlunparse((
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            parsed_url.params,
            new_query,
            parsed_url.fragment
        ))

        logger.info(f"Starting verification for deployment {config.deployment_id}")
        logger.info(f"Sample URL: {timestamped_url}")
        logger.info(f"Expected forward IP: {config.parent.ip_address}")

        result = VerificationResult(
            passed=False,
            sample_url=timestamped_url,
            expected_forward_ip=config.parent.ip_address
        )

        # Pre-flight: Check Ghost health
        logger.info("Pre-flight: Checking Ghost health...")
        ghost_status = self.check_ghost_status(config.edge)

        if not ghost_status['process_running']:
            result.failure_reason = "Ghost process is not running on edge"
            result.suggested_fixes = [
                "Start Ghost: /a/sbin/trident_run unstickystop webghost",
                "Check Ghost logs: /a/logs/cache.log",
            ]
            return result

        if not ghost_status['accepting_connections']:
            result.failure_reason = "Ghost is not accepting connections on edge"
            result.suggested_fixes = [
                "Restart Ghost: /a/sbin/trident_run stickystop webghost && /a/sbin/trident_run unstickystop webghost",
                "Check Port Stats in cache.log",
                "Verify Ghost configuration",
            ]
            return result

        logger.info("✓ Ghost is healthy and accepting connections")

        # Step 1: Clear access log before test
        logger.info(f"Step 1: Clearing access log: {access_log_path}")
        clear_cmd = f"> {access_log_path}"
        exit_code, stdout, stderr = self.ssh_manager.execute_command(
            config.edge,
            clear_cmd
        )

        if exit_code != 0:
            logger.warning(f"Failed to clear access log (non-fatal): {stderr}")

        # Step 2: Execute curl request from edge to itself
        logger.info(f"Step 2: Executing curl request...")
        curl_success, curl_output, curl_exit_code = self._execute_curl_request(
            config.edge,
            timestamped_url
        )

        # Extract hostname for display in result
        parsed = urlparse(timestamped_url)
        hostname = parsed.hostname
        scheme = parsed.scheme
        port = parsed.port or (443 if scheme == 'https' else 80)

        result.curl_command = f"curl --resolve '{hostname}:{port}:127.0.0.1' -k -m {self.curl_timeout} '{timestamped_url}'"
        result.curl_output = curl_output[:500]  # Truncate for readability
        result.curl_exit_code = curl_exit_code

        if not curl_success:
            logger.warning(f"✗ Curl request failed with exit code {curl_exit_code}")
            logger.info(f"Continuing to check access logs for error details...")
        else:
            logger.info(f"✓ Curl request completed")

        # Step 3: Wait for log flush
        logger.info(f"Step 3: Waiting {self.log_flush_wait}s for log flush...")
        time.sleep(self.log_flush_wait)

        # Step 4: Parse access logs
        logger.info(f"Step 4: Parsing access logs...")
        f_line, r_line = self._parse_access_logs(
            config.edge,
            access_log_path,
            timestamped_url
        )

        result.f_line_data = f_line
        result.r_line_data = r_line

        # Step 5: Analyze results following the required reporting sequence
        logger.info("=" * 60)
        logger.info("VERIFICATION RESULTS:")
        logger.info("=" * 60)

        # R-line analysis
        if r_line:
            logger.info(f"✓ R-line found")
            logger.info(f"  → Field 14 (index 13) error_code: {r_line.error_code}")
        else:
            logger.warning(f"✗ R-line not found")

        # F-line analysis
        if not f_line:
            result.f_line_found = False
            logger.warning(f"✗ F-line not found")
            result.failure_reason = "No f-line found in access log"
            result.suggested_fixes = [
                "Check if request reached Ghost (check r-line for errors)",
                "Verify ARL file is loaded in arlindex.xml",
                "Check if Ghost is forwarding to origin/parent",
                "Review cache.log for errors",
            ]

            if r_line and r_line.error_code != "-":
                result.failure_reason += f" (r-line error: {r_line.error_code})"
                result.suggested_fixes.insert(0, f"Fix r-line error: {r_line.error_code}")

            logger.info("=" * 60)
            return result

        result.f_line_found = True
        result.actual_forward_ip = f_line.forward_ip
        logger.info(f"✓ F-line found")
        logger.info(f"  → Field 10 (index 9) forward_ip: {f_line.forward_ip} (expected: {config.parent.ip_address})")
        logger.info(f"  → Field 29 (index 28) error: {f_line.error_field}")

        # Validate results
        has_r_line_error = r_line and r_line.error_code != "-"
        has_f_line_error = f_line and f_line.error_field != "-"
        wrong_forward_ip = f_line and f_line.forward_ip != config.parent.ip_address
        curl_failed = not curl_success

        # Priority: Check log errors first (most informative), then forward IP, then curl failure
        if has_r_line_error:
            result.passed = False
            result.failure_reason = f"R-line error: {r_line.error_code}"
            result.suggested_fixes = [
                f"Fix r-line error code: {r_line.error_code}",
                "Check cache.log for detailed error messages",
                "Verify ARL file configuration",
            ]
            logger.warning(f"✗ Verification FAILED - {result.failure_reason}")
        elif has_f_line_error:
            result.passed = False
            result.failure_reason = f"F-line error: {f_line.error_field}"
            result.suggested_fixes = [
                f"Fix f-line error: {f_line.error_field}",
                "Check forward connection to parent",
                "Verify parent is reachable and Ghost is running",
            ]
            logger.warning(f"✗ Verification FAILED - {result.failure_reason}")
        elif wrong_forward_ip:
            result.passed = False
            result.failure_reason = (
                f"Wrong forward IP: expected {config.parent.ip_address}, "
                f"got {f_line.forward_ip}"
            )
            result.suggested_fixes = [
                "Check DNS simulation lookup configuration in ARL file",
                "Verify parent IP is correctly configured",
                "Check if Ghost is using the correct ARL file",
            ]
            logger.warning(f"✗ Verification FAILED - {result.failure_reason}")
        elif curl_failed:
            # Curl failed but no errors in logs - might be timeout or connection issue
            result.passed = False
            if not result.failure_reason:  # Only set if not already set
                result.failure_reason = f"Curl request failed with exit code {curl_exit_code}"
            result.suggested_fixes = [
                "Check if Ghost is running on edge",
                "Verify hostname resolves correctly",
                "Check if sample URL is valid",
                "Review r-line and f-line for details",
            ]
            logger.warning(f"✗ Verification FAILED - {result.failure_reason}")
        else:
            result.passed = True
            logger.info(f"✓ Verification PASSED")

        logger.info("=" * 60)
        return result
    
    def _execute_curl_request(
        self,
        edge: MachineConfig,
        sample_url: str
    ) -> tuple[bool, str, int]:
        """
        Execute a curl request from edge machine to itself.
        
        Args:
            edge: Edge machine configuration
            sample_url: URL to request
            
        Returns:
            Tuple of (success, output, exit_code)
        """
        try:
            # Extract hostname from URL for --resolve
            from urllib.parse import urlparse
            parsed = urlparse(sample_url)
            hostname = parsed.hostname
            scheme = parsed.scheme
            port = parsed.port or (443 if scheme == 'https' else 80)
            
            # Build curl command
            # --resolve: Force hostname to resolve to 127.0.0.1 so Ghost handles it
            # -k: Allow insecure SSL
            # -m: Timeout
            # -s: Silent mode
            # -w: Write out response code
            curl_cmd = (
                f"curl --resolve '{hostname}:{port}:127.0.0.1' "
                f"-k -s -m {self.curl_timeout} "
                f"-w '\\nHTTP_CODE:%{{http_code}}' "
                f"'{sample_url}'"
            )
            
            logger.info(f"Executing curl on {edge}: {sample_url}")
            logger.debug(f"Curl command: {curl_cmd}")
            exit_code, stdout, stderr = self.ssh_manager.execute_command(
                edge,
                curl_cmd,
                timeout=self.curl_timeout + 10
            )
            
            if exit_code == 0:
                logger.info(f"✓ Curl request successful")
                return True, stdout, exit_code
            else:
                logger.warning(f"✗ Curl request failed with exit code {exit_code}")
                logger.warning(f"Stderr: {stderr}")
                return False, stdout + stderr, exit_code
                
        except Exception as e:
            logger.error(f"Failed to execute curl request: {e}")
            return False, str(e), -1
    
    def _parse_access_logs(
        self,
        edge: MachineConfig,
        access_log_path: str,
        sample_url: str
    ) -> tuple[Optional[FLineData], Optional[RLineData]]:
        """
        Parse access logs to find f-line and r-line for the request.

        Args:
            edge: Edge machine configuration
            access_log_path: Path to access log
            sample_url: Sample URL to match

        Returns:
            Tuple of (FLineData, RLineData) - either may be None
        """
        try:
            # Tail the access log
            tail_cmd = f"tail -n {self.log_tail_lines} {access_log_path}"
            exit_code, stdout, stderr = self.ssh_manager.execute_command(
                edge,
                tail_cmd
            )

            if exit_code != 0:
                logger.error(f"Failed to read access log: {stderr}")
                return None, None

            # Extract path and hostname from URL for matching
            from urllib.parse import urlparse
            parsed = urlparse(sample_url)
            url_path = self._extract_path_from_url(sample_url)
            url_hostname = parsed.hostname
            logger.info(f"Searching for hostname: {url_hostname}, path: {url_path}")
            
            # Parse lines in reverse (most recent first)
            lines = stdout.strip().split('\n')
            logger.info(f"Scanning {len(lines)} log lines...")

            # Debug: show sample of log lines
            r_count = sum(1 for line in lines if line.startswith('r '))
            f_count = sum(1 for line in lines if line.startswith('f '))
            logger.info(f"Found {r_count} r-lines and {f_count} f-lines in log")
            
            f_line = None
            r_line = None
            
            for line in reversed(lines):
                # Try to parse as f-line
                if line.startswith('f ') and not f_line:
                    parsed_f = self.log_parser.parse_f_line(line)
                    if parsed_f:
                        logger.debug(f"Parsed f-line with arl: {parsed_f.arl[:100]}")
                        # Match both hostname and path to avoid false matches
                        if url_hostname in line and url_path in parsed_f.arl:
                            f_line = parsed_f
                            logger.info(f"✓ Found matching f-line")
                            logger.info(f"Full f-line: {line}")
                    else:
                        logger.debug(f"Failed to parse f-line: {line[:150]}")

                # Try to parse as r-line
                if line.startswith('r ') and not r_line:
                    parsed_r = self.log_parser.parse_r_line(line)
                    if parsed_r:
                        logger.debug(f"Parsed r-line with arl: {parsed_r.arl[:100]}")
                        logger.debug(f"Checking if hostname '{url_hostname}' and path '{url_path}' in line")
                        # Match both hostname and path to avoid ghostmon false positives
                        # Ghostmon uses path "/" with hostname "ghstmnchk", so hostname check prevents false match
                        if url_hostname in line and url_path in parsed_r.arl:
                            r_line = parsed_r
                            logger.info(f"✓ Found matching r-line")
                            logger.info(f"Full r-line: {line}")
                        else:
                            logger.debug(f"Hostname or path not found in line")
                    else:
                        logger.debug(f"Failed to parse r-line: {line[:150]}")

                # Stop if we found both
                if f_line and r_line:
                    break
            
            if not f_line:
                logger.warning(f"✗ No matching f-line found for URL path: {url_path}")
            if not r_line:
                logger.warning(f"✗ No matching r-line found for URL path: {url_path}")
            
            return f_line, r_line
            
        except Exception as e:
            logger.error(f"Failed to parse access logs: {e}")
            return None, None
    
    @staticmethod
    def _extract_path_from_url(url: str) -> str:
        """
        Extract path from URL for matching in logs.
        
        Args:
            url: Full URL
            
        Returns:
            Path component of URL (normalized to remove duplicate slashes)
        """
        # Remove protocol
        if '://' in url:
            url = url.split('://', 1)[1]
        
        # Remove hostname
        if '/' in url:
            path = '/' + url.split('/', 1)[1]
        else:
            path = '/'
        
        # Remove query string for initial matching
        if '?' in path:
            path = path.split('?', 1)[0]
        
        # Normalize duplicate slashes (e.g., "//path" -> "/path")
        # This handles cases where URLs have malformed paths like "https://example.com//path"
        while '//' in path:
            path = path.replace('//', '/')
        
        return path
    
    def check_ghost_status(
        self,
        machine: MachineConfig,
        ghost_binary: str = "/a/bin/ghost",
        cache_log_path: str = "/a/logs/cache.log"
    ) -> Dict[str, bool]:
        """
        Check Ghost process health and status.
        
        Args:
            machine: Machine to check
            ghost_binary: Path to Ghost binary
            cache_log_path: Path to cache.log
            
        Returns:
            Dict with status indicators
        """
        status = {
            'process_running': False,
            'accepting_connections': False,
            'binary_exists': False,
        }
        
        try:
            # Check if Ghost binary exists
            if self.ssh_manager.file_exists(machine, ghost_binary):
                status['binary_exists'] = True
            
            # Check if Ghost process is running
            exit_code, stdout, stderr = self.ssh_manager.execute_command(
                machine,
                "ps aux | grep -E '[g]host|[w]ebghost'"
            )
            
            if exit_code == 0 and stdout.strip():
                status['process_running'] = True
                logger.info(f"✓ Ghost process running on {machine}")
            else:
                logger.warning(f"✗ Ghost process not found on {machine}")
            
            # Check port stats in cache.log
            exit_code, stdout, stderr = self.ssh_manager.execute_command(
                machine,
                f"tail -n 50 {cache_log_path} | grep 'Ports Stats'"
            )

            if exit_code == 0 and stdout.strip():
                # Look for non-zero accepts
                # Format: Ports Stats - accepts per port:0:0, 80:123, 443:456
                # Match port 80, 443, or 9001 with non-zero accept count
                if re.search(r'(80|443|9001):([1-9]\d*)', stdout):
                    status['accepting_connections'] = True
                    logger.info(f"✓ Ghost accepting connections on {machine}")
                else:
                    logger.warning(f"✗ Ghost not accepting connections on {machine}")
            
        except Exception as e:
            logger.error(f"Failed to check Ghost status on {machine}: {e}")
        
        return status
