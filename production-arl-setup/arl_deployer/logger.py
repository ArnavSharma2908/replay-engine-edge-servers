"""
Custom logging utility with color support and verbose mode.

Provides two logging modes:
1. Default: Beautiful, minimal output showing only key steps and pass/fail status
2. Verbose: Detailed logging with all information
"""

import logging
import sys
from typing import Optional

# ANSI color codes
class Colors:
    """ANSI color codes for terminal output."""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'

    # Regular colors
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    GRAY = '\033[90m'

    # Bright colors
    BRIGHT_BLACK = '\033[90m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'

    # Background colors
    BG_GREEN = '\033[42m'
    BG_RED = '\033[41m'
    BG_YELLOW = '\033[43m'


# Unicode box drawing characters for beautiful output
class Symbols:
    """Unicode symbols for beautiful CLI output."""
    CHECK = '✓'
    CROSS = '✗'
    WARNING = '⚠'
    ARROW = '→'
    BULLET = '•'
    CHEVRON = '›'
    SPINNER = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

    # Box drawing
    BOX_TOP_LEFT = '╭'
    BOX_TOP_RIGHT = '╮'
    BOX_BOTTOM_LEFT = '╰'
    BOX_BOTTOM_RIGHT = '╯'
    BOX_HORIZONTAL = '─'
    BOX_VERTICAL = '│'
    BOX_VERTICAL_RIGHT = '├'
    BOX_VERTICAL_LEFT = '┤'


class ARLLogger:
    """
    Custom logger with color support and verbose mode.

    In default mode (verbose=False):
    - Shows only step headings and pass/fail status
    - Uses colors: Green for success, Red for failure, Yellow for warnings
    - Minimal output for clean CLI experience

    In verbose mode (verbose=True):
    - Shows all detailed logging information
    - Includes timestamps, log levels, and full context
    """

    def __init__(self, name: str, verbose: bool = False, use_colors: bool = True):
        """
        Initialize the logger.

        Args:
            name: Logger name
            verbose: Enable verbose logging mode
            use_colors: Enable color output (auto-disabled for non-TTY)
        """
        self.name = name
        self.verbose = verbose
        self.use_colors = use_colors and sys.stdout.isatty()

        # Set up underlying Python logger
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    def _colorize(self, text: str, color: str, bold: bool = False) -> str:
        """Add color to text if colors are enabled."""
        if not self.use_colors:
            return text

        prefix = Colors.BOLD if bold else ''
        return f"{prefix}{color}{text}{Colors.RESET}"

    def banner(self, message: str, subtitle: str = ""):
        """Log a beautiful banner for major sections."""
        width = 70
        if self.use_colors:
            print()
            print(self._colorize(f"{Symbols.BOX_TOP_LEFT}{Symbols.BOX_HORIZONTAL * (width-2)}{Symbols.BOX_TOP_RIGHT}", Colors.BRIGHT_BLUE, bold=True))
            msg_line = f"{Symbols.BOX_VERTICAL} {message.center(width-4)} {Symbols.BOX_VERTICAL}"
            print(self._colorize(msg_line, Colors.BRIGHT_BLUE, bold=True))
            if subtitle:
                sub_line = f"{Symbols.BOX_VERTICAL} {subtitle.center(width-4)} {Symbols.BOX_VERTICAL}"
                print(self._colorize(sub_line, Colors.CYAN))
            print(self._colorize(f"{Symbols.BOX_BOTTOM_LEFT}{Symbols.BOX_HORIZONTAL * (width-2)}{Symbols.BOX_BOTTOM_RIGHT}", Colors.BRIGHT_BLUE, bold=True))
            print()
        else:
            print(f"\n{'='*width}")
            print(message)
            if subtitle:
                print(subtitle)
            print(f"{'='*width}\n")

    def step(self, message: str):
        """Log a major step heading (always shown, colored blue and bold)."""
        if self.use_colors:
            print()
            step_msg = self._colorize(f"▶ {message}", Colors.BRIGHT_CYAN, bold=True)
            print(step_msg)
        else:
            print(f"\n▶ {message}")

    def success(self, message: str):
        """Log a success message (always shown, colored green)."""
        if self.use_colors:
            symbol = self._colorize(Symbols.CHECK, Colors.BRIGHT_GREEN, bold=True)
            colored_msg = self._colorize(message, Colors.GREEN)
            print(f"{symbol} {colored_msg}")
        else:
            print(f"✓ {message}")

    def failure(self, message: str):
        """Log a failure message (always shown, colored red)."""
        if self.use_colors:
            symbol = self._colorize(Symbols.CROSS, Colors.BRIGHT_RED, bold=True)
            colored_msg = self._colorize(message, Colors.RED)
            print(f"{symbol} {colored_msg}")
        else:
            print(f"✗ {message}")

    def warning(self, message: str):
        """Log a warning message (always shown, colored yellow)."""
        if self.use_colors:
            symbol = self._colorize(Symbols.WARNING, Colors.BRIGHT_YELLOW, bold=True)
            colored_msg = self._colorize(message, Colors.YELLOW)
            print(f"{symbol} {colored_msg}")
        else:
            print(f"⚠ {message}")

        # Also log to underlying logger in verbose mode
        if self.verbose:
            self.logger.warning(message)

    def info(self, message: str):
        """Log an info message (shown with bullet in non-verbose, full log in verbose)."""
        if self.verbose:
            self.logger.info(message)
        else:
            # Show with bullet point in non-verbose mode with proper indentation
            indent = "    "  # 4 spaces for subtask indentation
            if self.use_colors:
                symbol = self._colorize(Symbols.BULLET, Colors.CYAN)
                colored_msg = self._colorize(message, Colors.GRAY)
                print(f"{indent}{symbol} {colored_msg}")
            else:
                print(f"{indent}• {message}")

    def debug(self, message: str):
        """Log a debug message (shown only in verbose mode)."""
        if self.verbose:
            self.logger.debug(message)

    def error(self, message: str):
        """Log an error message (always shown, colored red)."""
        self.failure(message)

        # Also log to underlying logger
        self.logger.error(message)

    def substep(self, message: str, status: Optional[str] = None):
        """
        Log a substep with optional status indicator.

        Args:
            message: Substep description
            status: Optional status ('success', 'failure', 'running', None)
        """
        indent = "  "

        if status == 'success':
            if self.use_colors:
                symbol = self._colorize(Symbols.CHECK, Colors.BRIGHT_GREEN)
                colored_msg = self._colorize(message, Colors.GREEN)
                print(f"{indent}{symbol} {colored_msg}")
            else:
                print(f"{indent}✓ {message}")
        elif status == 'failure':
            if self.use_colors:
                symbol = self._colorize(Symbols.CROSS, Colors.BRIGHT_RED)
                colored_msg = self._colorize(message, Colors.RED)
                print(f"{indent}{symbol} {colored_msg}")
            else:
                print(f"{indent}✗ {message}")
        elif status == 'running':
            if self.use_colors:
                symbol = self._colorize(Symbols.CHEVRON, Colors.GRAY, bold=True)
                colored_msg = self._colorize(message, Colors.GRAY)
                print(f"{indent}{symbol} {colored_msg}")
            else:
                print(f"{indent}› {message}")
        else:
            if self.use_colors:
                symbol = self._colorize(Symbols.BULLET, Colors.GRAY)
                print(f"{indent}{symbol} {message}")
            else:
                print(f"{indent}• {message}")

    def progress(self, message: str):
        """Log a progress message (minimal, dim text - only in default mode)."""
        if not self.verbose and self.use_colors:
            dim_msg = self._colorize(f"  {Symbols.BULLET} {message}", Colors.GRAY)
            print(dim_msg)

    def result_summary(self, passed: int, failed: int, total: int):
        """
        Print a beautiful summary of results.

        Args:
            passed: Number of passed items
            failed: Number of failed items
            total: Total number of items
        """
        print()
        if self.use_colors:
            if failed == 0:
                # Success banner
                print(self._colorize(f"{Symbols.BOX_TOP_LEFT}{Symbols.BOX_HORIZONTAL * 68}{Symbols.BOX_TOP_RIGHT}", Colors.BRIGHT_GREEN, bold=True))
                status_line = f"{Symbols.BOX_VERTICAL} {Symbols.CHECK} ALL TESTS PASSED ".ljust(69) + f"{Symbols.BOX_VERTICAL}"
                print(self._colorize(status_line, Colors.BRIGHT_GREEN, bold=True))
                summary_line = f"{Symbols.BOX_VERTICAL}   {passed} passed out of {total} total".ljust(69) + f"{Symbols.BOX_VERTICAL}"
                print(self._colorize(summary_line, Colors.GREEN))
                print(self._colorize(f"{Symbols.BOX_BOTTOM_LEFT}{Symbols.BOX_HORIZONTAL * 68}{Symbols.BOX_BOTTOM_RIGHT}", Colors.BRIGHT_GREEN, bold=True))
            else:
                # Failure banner
                print(self._colorize(f"{Symbols.BOX_TOP_LEFT}{Symbols.BOX_HORIZONTAL * 68}{Symbols.BOX_TOP_RIGHT}", Colors.BRIGHT_RED, bold=True))
                status_line = f"{Symbols.BOX_VERTICAL} {Symbols.CROSS} SOME TESTS FAILED ".ljust(69) + f"{Symbols.BOX_VERTICAL}"
                print(self._colorize(status_line, Colors.BRIGHT_RED, bold=True))
                passed_text = self._colorize(f"{passed} passed", Colors.GREEN)
                failed_text = self._colorize(f"{failed} failed", Colors.RED)
                summary_line = f"{Symbols.BOX_VERTICAL}   {passed_text}, {failed_text} out of {total} total"
                # Account for ANSI codes in padding
                padding = 69 - len(f"   {passed} passed, {failed} failed out of {total} total")
                print(summary_line + " " * padding + self._colorize(Symbols.BOX_VERTICAL, Colors.BRIGHT_RED))
                print(self._colorize(f"{Symbols.BOX_BOTTOM_LEFT}{Symbols.BOX_HORIZONTAL * 68}{Symbols.BOX_BOTTOM_RIGHT}", Colors.BRIGHT_RED, bold=True))
        else:
            print("="*70)
            if failed == 0:
                print(f"✓ ALL TESTS PASSED")
            else:
                print(f"✗ SOME TESTS FAILED")
            print(f"  {passed} passed, {failed} failed out of {total} total")
            print("="*70)
        print()

    def section(self, title: str):
        """Log a section heading (always shown, colored cyan)."""
        if self.use_colors:
            print()
            colored_title = self._colorize(f"┌─ {title}", Colors.BRIGHT_CYAN, bold=True)
            print(colored_title)
        else:
            print(f"\n┌─ {title}")

    def divider(self):
        """Print a subtle divider line."""
        if self.use_colors:
            print(self._colorize(f"  {Symbols.BOX_HORIZONTAL * 66}", Colors.GRAY))
        else:
            print(f"  {'-' * 66}")


# Global verbose flag (can be set by CLI)
_VERBOSE_MODE = False

def set_verbose(verbose: bool):
    """Set global verbose mode."""
    global _VERBOSE_MODE
    _VERBOSE_MODE = verbose


def get_logger(name: str) -> ARLLogger:
    """
    Get a logger instance with current verbose settings.

    Args:
        name: Logger name

    Returns:
        ARLLogger instance
    """
    return ARLLogger(name, verbose=_VERBOSE_MODE)
