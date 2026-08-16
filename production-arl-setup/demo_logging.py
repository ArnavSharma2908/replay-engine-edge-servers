#!/usr/bin/env python3
"""
Demo script to showcase the beautiful logging output.
Run this to see what the default and verbose modes look like.
"""

from arl_deployer.logger import get_logger, set_verbose
import time

def demo_default_mode():
    """Demonstrate default (beautiful, minimal) logging mode."""
    print("\n" + "="*70)
    print("DEMO: Default Mode (Beautiful, Minimal Output)")
    print("="*70 + "\n")

    set_verbose(False)
    # Force colors on for demo
    from arl_deployer.logger import ARLLogger
    logger = ARLLogger("demo", verbose=False, use_colors=True)

    # Banner
    logger.banner("ARL Deployment System", "Automated Configuration Management")

    # Step
    logger.step("Initializing deployment")
    logger.progress("Loading configuration files")
    logger.progress("Connecting to remote machines")
    logger.success("Connected to 3 machines")

    # Section with substeps
    logger.section("Running Verification")
    logger.progress("Clearing access logs")
    logger.progress("Executing curl request")
    logger.substep("Request completed in 0.5s", status='success')
    logger.progress("Analyzing logs")
    logger.failure("Found error: ERR_FWD_CERT_SPEC_MISSING")

    logger.divider()

    # Healing section
    logger.banner("Self-Healing Engine", "fix_abc123")
    logger.warning("Detected issue: ERR_FWD_CERT_SPEC_MISSING|ssl_fwd_cert")

    logger.step("Analyzing and applying fixes")
    logger.progress("Found 1 applicable rule(s)")
    logger.progress("Applying: Comment out SSL forward certificate requirement")
    logger.progress("Commenting out: auth:origin.ssl-fwd-cert")
    logger.progress("Modified 2 line(s) on edge")
    logger.success("Fixed: Comment out SSL forward certificate requirement")

    logger.divider()

    # Final results
    logger.step("Re-running verification")
    logger.progress("Executing verification request")
    logger.success("Verification passed!")

    # Summary
    logger.result_summary(passed=1, failed=0, total=1)


def demo_verbose_mode():
    """Demonstrate verbose logging mode."""
    print("\n" + "="*70)
    print("DEMO: Verbose Mode (Detailed Output)")
    print("="*70 + "\n")

    set_verbose(True)
    logger = get_logger("demo")

    logger.banner("ARL Deployment System", "Automated Configuration Management")

    logger.step("Initializing deployment")
    logger.info("Loading config from: /path/to/config.yaml")
    logger.info("SSH key path: /root/.ssh/identity")
    logger.debug("Establishing SSH connection to 198.18.134.240")
    logger.debug("SSH connection timeout: 10s")
    logger.success("Connected to 3 machines")

    logger.section("Running Verification")
    logger.info("Clearing access log: /a/logs/ghost.access.log")
    logger.debug("Executing: curl -k -v https://example.com/test")
    logger.info("Curl request completed with exit code 0")
    logger.info("Waiting 5s for log flush...")
    logger.info("Parsing access logs...")
    logger.info("Found r-line with error code: ERR_FWD_CERT_SPEC_MISSING")
    logger.failure("Found error: ERR_FWD_CERT_SPEC_MISSING")

    logger.result_summary(passed=0, failed=1, total=1)


if __name__ == "__main__":
    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "both"

    if mode in ["default", "both"]:
        demo_default_mode()

    if mode in ["verbose", "both"]:
        time.sleep(1)
        demo_verbose_mode()

    print("\n" + "="*70)
    print("To see default mode: python demo_logging.py default")
    print("To see verbose mode: python demo_logging.py verbose")
    print("="*70 + "\n")
