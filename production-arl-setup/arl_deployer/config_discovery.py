"""
Config discovery module for finding and analyzing ARL files.

This module handles searching for customer configuration files on the config server,
extracting metadata, and identifying dependencies.
"""

import logging
import re
import tempfile
from pathlib import Path
from typing import Optional, List, Tuple
from xml.dom import minidom

from arl_deployer.models import ARLFileInfo, MachineConfig
from arl_deployer.ssh_manager import SSHManager

logger = logging.getLogger(__name__)


class ConfigDiscoveryError(Exception):
    """Raised when config discovery fails."""
    pass


class ConfigDiscovery:
    """
    Discovers and analyzes customer ARL configuration files.
    
    Responsibilities:
    - Search for ARL files by CPCode
    - Filter by hostname if multiple matches
    - Extract metadata (CPCode, hostnames, dependencies)
    - Download ARL file for analysis
    """
    
    def __init__(self, ssh_manager: SSHManager):
        """
        Initialize Config Discovery.
        
        Args:
            ssh_manager: SSH manager for remote operations
        """
        self.ssh_manager = ssh_manager
    
    def find_arl_by_cpcode(
        self,
        config_server: MachineConfig,
        arl_data_dir: str,
        cpcode: str,
        hostname: Optional[str] = None
    ) -> Optional[ARLFileInfo]:
        """
        Find ARL file by CPCode and optionally hostname.
        
        Strategy:
        1. If hostname provided, search arlindex.xml first (faster)
        2. Otherwise, search all ARL files for CPCode
        
        Args:
            config_server: Config server machine configuration
            arl_data_dir: Directory containing ARL files
            cpcode: CPCode to search for
            hostname: Optional hostname to filter by
            
        Returns:
            ARLFileInfo if found, None otherwise
            
        Raises:
            ConfigDiscoveryError: If search fails or multiple ambiguous matches found
        """
        logger.info(f"Searching for CPCode {cpcode} in {config_server}:{arl_data_dir}")
        
        # Strategy 1: If hostname provided, search arlindex.xml first
        if hostname:
            logger.info(f"Searching arlindex.xml for hostname: {hostname}")
            arl_info = self._find_by_hostname_in_arlindex(
                config_server,
                arl_data_dir,
                hostname,
                cpcode
            )
            if arl_info:
                return arl_info
            # Fall through to CPCode search if hostname not found
            logger.warning(f"Hostname {hostname} not found in arlindex.xml, trying CPCode search")
        
        # Strategy 2: Search all ARL files for CPCode
        search_pattern = f"<reporting:cpcode>{cpcode}</reporting:cpcode>"
        
        try:
            # Use grep to search for CPCode
            exit_code, stdout, stderr = self.ssh_manager.execute_command(
                config_server,
                f"cd {arl_data_dir} && grep -l '{search_pattern}' *.xml 2>/dev/null | grep -v arlindex.xml",
                timeout=60
            )
            
            if exit_code != 0 or not stdout.strip():
                logger.error(f"No ARL files found for CPCode {cpcode}")
                return None
            
            # Parse matching filenames
            matching_files = [line.strip() for line in stdout.split('\n') if line.strip()]
            
            if not matching_files:
                logger.error(f"No ARL files found for CPCode {cpcode}")
                return None
            
            logger.info(f"Found {len(matching_files)} ARL file(s) for CPCode {cpcode}: {matching_files}")
            
            # If only one match, return it
            if len(matching_files) == 1:
                return self._analyze_arl_file(
                    config_server,
                    arl_data_dir,
                    matching_files[0],
                    cpcode
                )
            
            # Multiple matches - filter by hostname if provided
            if hostname:
                logger.info(f"Multiple matches found, filtering by hostname: {hostname}")
                
                for filename in matching_files:
                    arl_info = self._analyze_arl_file(
                        config_server,
                        arl_data_dir,
                        filename,
                        cpcode
                    )
                    
                    if arl_info and hostname in arl_info.hostnames:
                        logger.info(f"✓ Found match with hostname: {filename}")
                        return arl_info
                
                raise ConfigDiscoveryError(
                    f"Multiple ARL files found for CPCode {cpcode}, "
                    f"but none match hostname {hostname}: {matching_files}"
                )
            else:
                raise ConfigDiscoveryError(
                    f"Multiple ARL files found for CPCode {cpcode}: {matching_files}. "
                    f"Please provide a hostname to disambiguate."
                )
                
        except ConfigDiscoveryError:
            raise
        except Exception as e:
            raise ConfigDiscoveryError(f"Failed to search for CPCode {cpcode}: {e}")
    
    def get_arl_by_filename(
        self,
        config_server: MachineConfig,
        arl_data_dir: str,
        filename: str
    ) -> Optional[ARLFileInfo]:
        """
        Get ARL file info directly by filename.
        
        Args:
            config_server: Config server machine configuration
            arl_data_dir: Directory containing ARL files
            filename: ARL filename
            
        Returns:
            ARLFileInfo if found, None otherwise
        """
        logger.info(f"Getting ARL file by filename: {filename}")
        
        # Check if file exists
        remote_path = f"{arl_data_dir}/{filename}"
        if not self.ssh_manager.file_exists(config_server, remote_path):
            logger.error(f"ARL file not found: {remote_path}")
            return None
        
        # Extract CPCode from filename if possible
        # Typical format: ARL_hostname.cpcode.xml
        cpcode_match = re.search(r'\.(\d+)\.xml$', filename)
        cpcode = cpcode_match.group(1) if cpcode_match else None
        
        return self._analyze_arl_file(config_server, arl_data_dir, filename, cpcode)
    
    def find_arl_by_arlid(
        self,
        config_server: MachineConfig,
        arl_data_dir: str,
        arl_id: str
    ) -> Optional[ARLFileInfo]:
        """
        Find ARL file by searching for arlid in arlindex.xml.

        The arlindex.xml structure:
        <trident:include arlid="1257397" type="arldata" file="ARL_-llm.moli.lenovo.com-prod.1257397.xml" ...>
            <host exact="true">mea-llm.moli.lenovo.com</host>
        </trident:include>

        Args:
            config_server: Config server machine configuration
            arl_data_dir: Directory containing ARL files
            arl_id: ARL ID to search for

        Returns:
            ARLFileInfo if found, None otherwise

        Raises:
            ConfigDiscoveryError: If ARL ID not found or search fails
        """
        arlindex_path = f"{arl_data_dir}/arlindex.xml"

        try:
            logger.info(f"Searching arlindex.xml for ARL ID: {arl_id}")

            # Search for arlid attribute in arlindex.xml
            exit_code, stdout, stderr = self.ssh_manager.execute_command(
                config_server,
                f"grep 'arlid=\"{arl_id}\"' {arlindex_path}",
                timeout=30
            )

            if exit_code != 0 or not stdout.strip():
                raise ConfigDiscoveryError(f"ARL ID {arl_id} not found in arlindex.xml")

            # Parse grep output to extract filename
            # Example output:
            # <trident:include arlid="1257397" type="arldata" file="ARL_-llm.moli.lenovo.com-prod.1257397.xml" ...>

            file_match = re.search(r'file="([^"]+\.xml)"', stdout)
            if not file_match:
                raise ConfigDiscoveryError(
                    f"Could not extract filename from arlindex.xml for ARL ID {arl_id}"
                )

            filename = file_match.group(1)
            logger.info(f"✓ Found ARL file for ARL ID {arl_id}: {filename}")

            # Extract cpcode from arlid attribute (they're typically the same)
            # Or extract from filename if different
            cpcode_match = re.search(r'\.(\d+)\.xml$', filename)
            cpcode = cpcode_match.group(1) if cpcode_match else arl_id

            return self._analyze_arl_file(config_server, arl_data_dir, filename, cpcode, arlid=arl_id)

        except ConfigDiscoveryError:
            raise
        except Exception as e:
            logger.error(f"Failed to search arlindex.xml for ARL ID {arl_id}: {e}")
            raise ConfigDiscoveryError(f"Failed to find ARL by ID {arl_id}: {e}")

    def _find_by_hostname_in_arlindex(
        self,
        config_server: MachineConfig,
        arl_data_dir: str,
        hostname: str,
        cpcode: Optional[str] = None
    ) -> Optional[ARLFileInfo]:
        """
        Find ARL file by searching for hostname in arlindex.xml.

        This is faster than searching all files. The arlindex.xml structure:
        <trident:include arlid="1042719" type="arldata" file="ARL_hubdoc-app.xero.com.1042719.xml" ...>
            <host exact="true">hubdoc-app.xero-uat.com</host>
        </trident:include>

        Args:
            config_server: Config server machine configuration
            arl_data_dir: Directory containing ARL files
            hostname: Hostname to search for
            cpcode: Optional CPCode to verify if multiple matches found

        Returns:
            ARLFileInfo if found, None otherwise
        """
        arlindex_path = f"{arl_data_dir}/arlindex.xml"
        
        try:
            # Search for hostname in arlindex.xml with context
            exit_code, stdout, stderr = self.ssh_manager.execute_command(
                config_server,
                f"grep -B 1 '<host.*>{hostname}</host>' {arlindex_path}",
                timeout=30
            )
            
            if exit_code != 0 or not stdout.strip():
                logger.warning(f"Hostname {hostname} not found in arlindex.xml")
                return None
            
            # Parse grep output to extract filename
            # Example output:
            # <trident:include arlid="1042719" type="arldata" file="ARL_hubdoc-app.xero.com.1042719.xml" ...>
            #   <host exact="true">hubdoc-app.xero-uat.com</host>
            
            matching_files = []
            for line in stdout.split('\n'):
                # Look for file= attribute in trident:include lines
                file_match = re.search(r'file="([^"]+\.xml)"', line)
                if file_match:
                    filename = file_match.group(1)
                    matching_files.append(filename)
            
            if not matching_files:
                logger.warning(f"Could not extract filename from arlindex.xml for hostname {hostname}")
                return None
            
            logger.info(f"Found {len(matching_files)} ARL file(s) in arlindex.xml for hostname {hostname}: {matching_files}")
            
            # If only one match, use it
            if len(matching_files) == 1:
                filename = matching_files[0]
                logger.info(f"✓ Using ARL file from arlindex.xml: {filename}")
                
                # Extract cpcode from filename if not provided
                if not cpcode:
                    cpcode_match = re.search(r'\.(\d+)\.xml$', filename)
                    cpcode = cpcode_match.group(1) if cpcode_match else None
                
                return self._analyze_arl_file(config_server, arl_data_dir, filename, cpcode)
            
            # Multiple matches - verify CPCode if provided
            if cpcode:
                logger.info(f"Multiple matches found, filtering by CPCode: {cpcode}")
                
                # Check if filename contains the cpcode
                for filename in matching_files:
                    if f".{cpcode}." in filename:
                        logger.info(f"✓ Found match with CPCode in filename: {filename}")
                        return self._analyze_arl_file(config_server, arl_data_dir, filename, cpcode)
                
                # If not in filename, need to check file content
                for filename in matching_files:
                    arl_info = self._analyze_arl_file(config_server, arl_data_dir, filename, None)
                    if arl_info and arl_info.cpcode == cpcode:
                        logger.info(f"✓ Found match with CPCode in file content: {filename}")
                        return arl_info
                
                raise ConfigDiscoveryError(
                    f"Multiple ARL files found for hostname {hostname}, "
                    f"but none match CPCode {cpcode}: {matching_files}"
                )
            else:
                raise ConfigDiscoveryError(
                    f"Multiple ARL files found for hostname {hostname}: {matching_files}. "
                    f"Please provide a CPCode to disambiguate."
                )
            
        except ConfigDiscoveryError:
            raise
        except Exception as e:
            logger.error(f"Failed to search arlindex.xml for hostname {hostname}: {e}")
            return None
    
    def _analyze_arl_file(
        self,
        config_server: MachineConfig,
        arl_data_dir: str,
        filename: str,
        cpcode: Optional[str] = None,
        arlid: Optional[str] = None
    ) -> Optional[ARLFileInfo]:
        """
        Analyze an ARL file to extract metadata.
        
        Args:
            config_server: Config server machine configuration
            arl_data_dir: Directory containing ARL files
            filename: ARL filename
            cpcode: Optional CPCode (if known)
            
        Returns:
            ARLFileInfo with extracted metadata
        """
        try:
            remote_path = f"{arl_data_dir}/{filename}"
            
            # Download file to temporary location for analysis
            with tempfile.NamedTemporaryFile(mode='w+', suffix='.xml', delete=False) as tmp_file:
                local_path = tmp_file.name
            
            self.ssh_manager.download_file(config_server, remote_path, local_path)
            
            # Parse XML to extract metadata
            try:
                doc = minidom.parse(local_path)
                
                # Extract CPCode if not provided
                if not cpcode:
                    cpcode_elements = doc.getElementsByTagName('reporting:cpcode')
                    if cpcode_elements:
                        cpcode = cpcode_elements[0].firstChild.nodeValue.strip()
                
                # Extract hostnames from various possible locations
                hostnames = self._extract_hostnames_from_xml(doc)
                
                # Check for dependencies
                requires_hd_data = self._check_hd_data_dependency(doc, local_path)
                requires_waf_data = self._check_waf_data_dependency(doc, local_path)
                requires_pearl_data = self._check_pearl_data_dependency(doc, local_path)

                # Create ARLFileInfo
                arl_info = ARLFileInfo(
                    filename=filename,
                    arlid=arlid,
                    cpcode=cpcode or "unknown",
                    hostnames=hostnames,
                    remote_path=remote_path,
                    local_path=local_path,
                    requires_hd_data=requires_hd_data,
                    requires_waf_data=requires_waf_data,
                    requires_pearl_data=requires_pearl_data
                )

                logger.info(f"✓ Analyzed ARL file: {arl_info}")
                logger.debug(
                    f"  Requires HD data: {requires_hd_data}, "
                    f"Requires WAF data: {requires_waf_data}, "
                    f"Requires Pearl data: {requires_pearl_data}"
                )
                
                return arl_info
                
            except Exception as e:
                logger.error(f"Failed to parse ARL file {filename}: {e}")
                # Clean up temp file
                Path(local_path).unlink(missing_ok=True)
                return None
            
        except Exception as e:
            logger.error(f"Failed to analyze ARL file {filename}: {e}")
            return None
    
    def _extract_hostnames_from_xml(self, doc: minidom.Document) -> List[str]:
        """
        Extract hostnames from various locations in ARL XML.
        
        Args:
            doc: Parsed XML document
            
        Returns:
            List of hostnames
        """
        hostnames = []
        
        # Common locations for hostnames in ARL files
        hostname_tags = [
            'origin-server',
            'hostname',
            'host',
            'match:hostname',
            'network:hostname',
        ]
        
        for tag in hostname_tags:
            elements = doc.getElementsByTagName(tag)
            for elem in elements:
                if elem.firstChild and elem.firstChild.nodeValue:
                    hostname = elem.firstChild.nodeValue.strip()
                    if hostname and hostname not in hostnames:
                        hostnames.append(hostname)
        
        # Also check for hostname attributes
        for elem in doc.getElementsByTagName('*'):
            if elem.hasAttribute('hostname'):
                hostname = elem.getAttribute('hostname').strip()
                if hostname and hostname not in hostnames:
                    hostnames.append(hostname)
        
        return hostnames
    
    def _check_hd_data_dependency(self, doc: minidom.Document, file_path: str) -> bool:
        """
        Check if ARL file requires HD data.
        
        Args:
            doc: Parsed XML document
            file_path: Path to ARL file
            
        Returns:
            True if HD data is required
        """
        # Search for HD data references in XML
        hd_data_indicators = [
            'hd.data',
            'host-definition',
            'hostdef',
        ]
        
        # Read file content for text search
        try:
            with open(file_path, 'r') as f:
                content = f.read()
            
            for indicator in hd_data_indicators:
                if indicator in content.lower():
                    return True
        except Exception:
            pass
        
        # Always copy HD data to ensure availability
        return True
    
    def _check_waf_data_dependency(self, doc: minidom.Document, file_path: str) -> bool:
        """
        Check if ARL file requires WAF data.
        
        Args:
            doc: Parsed XML document
            file_path: Path to ARL file
            
        Returns:
            True if WAF data is required
        """
        # Search for WAF data references in XML
        waf_data_indicators = [
            'waf.data',
            'waf:',
            'web-application-firewall',
            'security:waf',
        ]
        
        # Read file content for text search
        try:
            with open(file_path, 'r') as f:
                content = f.read()
            
            for indicator in waf_data_indicators:
                if indicator in content.lower():
                    return True
        except Exception:
            pass
        
        return False

    def _check_pearl_data_dependency(self, doc: minidom.Document, file_path: str) -> bool:
        """
        Check if ARL file requires Pearl data.

        Args:
            doc: Parsed XML document
            file_path: Path to ARL file

        Returns:
            True if Pearl data is required
        """
        # Search for Pearl data references in XML
        pearl_data_indicators = [
            'pearl.data',
            'pearl:',
            'type="pearl"',
            'pearldata',
        ]

        # Read file content for text search
        try:
            with open(file_path, 'r') as f:
                content = f.read()

            for indicator in pearl_data_indicators:
                if indicator in content.lower():
                    return True
        except Exception:
            pass

        return True # TODO: just trying to see if this works with staples.com

    def extract_sample_url_from_traffic_profile(
        self,
        traffic_profile_path: str,
        hostname: str,
        cpcode: str
    ) -> Optional[str]:
        """
        Extract sample URL from traffic profile JSON.
        
        Args:
            traffic_profile_path: Path to traffic_profile.json
            hostname: Hostname to search for
            cpcode: CPCode to search for
            
        Returns:
            Sample URL if found, None otherwise
        """
        try:
            import json
            
            with open(traffic_profile_path, 'r') as f:
                traffic_profile = json.load(f)
            
            # Look for unique_hostnames section
            unique_hostnames = traffic_profile.get('unique_hostnames', {})
            
            # Try to find entry by hostname-cpcode key
            key = f"{hostname}-{cpcode}"
            if key in unique_hostnames:
                url = unique_hostnames[key]
                logger.info(f"✓ Found sample URL from traffic profile: {url}")
                return url
            
            # Try variations
            for k, v in unique_hostnames.items():
                if hostname in k or cpcode in k:
                    logger.info(f"✓ Found sample URL from traffic profile (partial match): {v}")
                    return v
            
            logger.warning(f"No sample URL found in traffic profile for {hostname}-{cpcode}")
            return None
            
        except FileNotFoundError:
            logger.warning(f"Traffic profile not found: {traffic_profile_path}")
            return None
        except Exception as e:
            logger.error(f"Failed to extract sample URL from traffic profile: {e}")
            return None
