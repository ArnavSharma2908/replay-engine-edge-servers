"""
ARL Transformer for Replay Mode

This module transforms ARL files to use header-based cpcode matching
instead of complex nested match conditions. This enables replay/prewarm
testing where the cpcode is specified via x-replay-expected-cpcode header.
"""

import logging
import re
from typing import List, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class ARLTransformError(Exception):
    """Raised when ARL transformation fails."""
    pass


class ARLTransformer:
    """
    Transforms ARL files to replace nested match conditions with
    header-based cpcode matching for replay testing.
    """

    def __init__(self):
        self.cpcode_pattern = re.compile(r'<reporting:cpcode>(\d+)</reporting:cpcode>')
        self.match_map = {}  # Will store pre-parsed match structure
        self.comment_ranges = []  # Will store (start, end) positions of comments

    def _is_inside_comment(self, pos: int) -> bool:
        """Check if a position is inside an XML comment."""
        for start, end in self.comment_ranges:
            if start <= pos < end:
                return True
        return False

    def transform_arl_for_replay(self, arl_file_path: str, backup: bool = True, timeout: int = 60) -> dict:
        """
        Transform ARL file to use header-based cpcode matching.

        Args:
            arl_file_path: Path to ARL file to transform
            backup: Whether to create backup before transformation
            timeout: Maximum seconds to allow for transformation

        Returns:
            Dict with transformation statistics

        Raises:
            ARLTransformError: If transformation fails
        """
        import time
        start_time = time.time()

        logger.info(f"Starting ARL transformation for replay mode: {arl_file_path}")
        logger.info(f"Transformation timeout: {timeout}s")

        try:
            # Read original file
            with open(arl_file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Validate XML before transformation
            logger.info("Validating XML structure before transformation...")
            try:
                import xml.etree.ElementTree as ET
                ET.fromstring(content)
                logger.info("✓ Original ARL file is valid XML")
            except ET.ParseError as e:
                logger.warning(f"Original ARL file has XML parsing issues: {e}")
                logger.warning("Proceeding with transformation anyway (may be namespace-related)")

            # Create backup if requested
            if backup:
                backup_path = f"{arl_file_path}.pre-replay-transform"
                with open(backup_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                logger.info(f"Created backup: {backup_path}")

            # Step 1: Build match map (one-pass O(n) scan)
            logger.info("Building match structure map...")
            map_start = time.time()
            self.match_map = self._build_match_map(content)
            map_elapsed = time.time() - map_start
            logger.info(f"Built match map in {map_elapsed:.2f}s: {len(self.match_map)} matches found")

            # Step 2: Collect granular, conflict-free edits.
            #
            # Two kinds of edits are produced so that EVERY cpcode (including
            # cpcodes that are directly owned by a match which ALSO contains
            # nested cpcode-bearing matches) becomes reachable during replay:
            #
            #   1. PASS-THROUGH: Every transformable match (variable / request.header
            #      / request.cookie) that contains any cpcode in its subtree has its
            #      opening + closing tags rewritten to a header match on the FULL set
            #      of subtree cpcodes. This lets a replay request descend into the
            #      block whenever its x-replay-expected-cpcode is anywhere inside.
            #
            #   2. PER-CPCODE GATE: Every individual <reporting:cpcode>X</reporting:cpcode>
            #      located inside a transformable match is wrapped in its own header
            #      match on exactly X, so only the request replaying X applies it.
            #
            # This avoids the nested-conflict problem where an outer "header == A"
            # would otherwise block descent to an inner "header == B".
            edits, stats = self._collect_edits(content)
            logger.info(
                f"Collected {len(edits)} edits: "
                f"{stats['matches']} match tags rewritten, "
                f"{stats['cpcodes']} cpcodes individually gated"
            )

            if not edits:
                logger.warning("No transformable cpcodes found in ARL file - no transformation needed")
                return {
                    'cpcodes_found': 0,
                    'cpcodes_transformed': 0,
                    'matches_transformed': 0,
                    'backup_created': backup
                }

            total_cpcodes = stats['cpcodes']
            transformed_count = stats['matches']

            # Step 3: Apply edits from end to start so positions stay valid.
            # Edits are non-overlapping by construction (tag spans vs cpcode spans),
            # but we assert this to catch any structural surprise.
            edits.sort(key=lambda e: e['start'], reverse=True)

            transformed_content = content
            last_start = len(content) + 1
            for edit in edits:
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    raise ARLTransformError(f"Transformation timed out after {elapsed:.1f}s (limit: {timeout}s)")

                start_pos = edit['start']
                end_pos = edit['end']
                new_text = edit['text']

                if end_pos > last_start:
                    raise ARLTransformError(
                        f"Overlapping edit detected at {start_pos}-{end_pos} "
                        f"(previous edit started at {last_start})"
                    )

                transformed_content = (
                    transformed_content[:start_pos] +
                    new_text +
                    transformed_content[end_pos:]
                )
                last_start = start_pos
                logger.debug(f"Applied {edit['kind']} edit at {start_pos}-{end_pos}")

            elapsed_total = time.time() - start_time
            logger.info(f"Transformation completed in {elapsed_total:.1f}s")

            # Validate transformed XML structure
            logger.info("Validating transformed XML structure...")
            try:
                import xml.etree.ElementTree as ET
                ET.fromstring(transformed_content)
                logger.info("✓ Transformed ARL file is valid XML")
            except ET.ParseError as e:
                logger.error(f"✗ Transformed ARL file has XML parsing errors: {e}")
                logger.error("The transformation created invalid XML - reverting changes")
                raise ARLTransformError(f"Transformation produced invalid XML: {e}")

            # Write transformed content
            with open(arl_file_path, 'w', encoding='utf-8') as f:
                f.write(transformed_content)

            logger.info(
                f"✓ ARL transformation complete: {transformed_count} match tags rewritten, "
                f"{total_cpcodes} cpcodes individually gated by x-replay-expected-cpcode"
            )

            return {
                'cpcodes_found': total_cpcodes,
                'cpcodes_transformed': total_cpcodes,
                'matches_transformed': transformed_count,
                'backup_created': backup,
                'backup_path': backup_path if backup else None
            }

        except Exception as e:
            logger.error(f"ARL transformation failed: {e}")
            raise ARLTransformError(f"Failed to transform ARL: {e}")

    def _build_match_map(self, content: str) -> dict:
        """
        Build a map of all match tags in the ARL file in a single O(n) pass.

        Returns:
            Dict mapping match_start_pos -> {
                'tag_end': position after '>' of opening tag,
                'closing_pos': position of '</match:...',
                'closing_end': position after '>' of closing tag,
                'full_tag': the complete opening tag string,
                'closing_tag': the complete closing tag string,
                'cpcodes': list of cpcode strings found within this match block
            }
        """
        # First pass: find all comment ranges to skip them
        self.comment_ranges = []
        pos = 0
        while pos < len(content):
            comment_start = content.find('<!--', pos)
            if comment_start == -1:
                break
            comment_end = content.find('-->', comment_start)
            if comment_end == -1:
                # Unclosed comment - treat rest of file as comment
                self.comment_ranges.append((comment_start, len(content)))
                break
            self.comment_ranges.append((comment_start, comment_end + 3))
            pos = comment_end + 3

        logger.debug(f"Found {len(self.comment_ranges)} comment blocks to skip")

        # Second pass: build match map, skipping comments
        match_map = {}
        match_stack = []  # Stack of (start_pos, tag_end_pos, full_tag)

        pos = 0
        while pos < len(content):
            # Look for next match-related tag
            next_open = content.find('<match:', pos)
            next_close = content.find('</match:', pos)

            # Determine which comes first
            if next_open == -1 and next_close == -1:
                break

            if next_close == -1 or (next_open != -1 and next_open < next_close):
                # Found opening tag - check if it's inside a comment
                if self._is_inside_comment(next_open):
                    logger.debug(f"Skipping <match: at {next_open} (inside comment)")
                    pos = next_open + 7  # Skip past '<match:'
                    continue

                tag_end = content.find('>', next_open)
                if tag_end == -1:
                    logger.warning(f"Malformed opening tag at {next_open}")
                    pos = next_open + 1
                    continue

                full_tag = content[next_open:tag_end + 1]
                match_stack.append((next_open, tag_end + 1, full_tag))
                pos = tag_end + 1

            else:
                # Found closing tag - check if it's inside a comment
                if self._is_inside_comment(next_close):
                    logger.debug(f"Skipping </match: at {next_close} (inside comment)")
                    pos = next_close + 8  # Skip past '</match:'
                    continue

                closing_end = content.find('>', next_close)
                if closing_end == -1:
                    logger.warning(f"Malformed closing tag at {next_close}")
                    pos = next_close + 1
                    continue

                if not match_stack:
                    logger.warning(f"Closing tag without opening at {next_close}")
                    pos = closing_end + 1
                    continue

                # Pop the matching opening tag
                start_pos, tag_end_pos, full_tag = match_stack.pop()

                # Extract cpcodes within this match block
                block_content = content[tag_end_pos:next_close]
                cpcodes = self.cpcode_pattern.findall(block_content)

                # Capture the full closing tag
                closing_tag = content[next_close:closing_end + 1]

                # Store in map
                match_map[start_pos] = {
                    'tag_end': tag_end_pos,
                    'closing_pos': next_close,
                    'closing_end': closing_end + 1,
                    'full_tag': full_tag,
                    'closing_tag': closing_tag,
                    'cpcodes': list(set(cpcodes))  # Unique cpcodes
                }
                
                # Log if cpcodes found
                if cpcodes:
                    logger.debug(
                        f"Match at pos {start_pos} contains {len(cpcodes)} cpcode(s): {cpcodes} "
                        f"(tag: {full_tag[:80]}...)"
                    )

                pos = closing_end + 1

        logger.debug(f"Built match map with {len(match_map)} entries")
        return match_map

    def _collect_edits(self, content: str) -> Tuple[List[dict], dict]:
        """
        Collect granular, non-overlapping edits that make every gated cpcode
        reachable during replay.

        Two kinds of edits are produced:

          - 'match' : rewrite a transformable match's opening and closing tags
            into a header match on the FULL set of cpcodes in that match's
            subtree. This acts as a pass-through so a replay request can descend
            into the block whenever its x-replay-expected-cpcode is anywhere
            inside (handles parents that own cpcodes directly AND contain nested
            cpcode-bearing matches).

          - 'cpcode' : wrap an individual <reporting:cpcode>X</reporting:cpcode>
            (that lives inside a transformable match) in its own header match on
            exactly X, so only the request replaying X applies that cpcode.

        Args:
            content: Original ARL file content

        Returns:
            Tuple of (edits, stats) where each edit is
            {'start': int, 'end': int, 'text': str, 'kind': str}
        """
        TRANSFORMABLE_TYPES = {'request.header', 'request.cookie', 'variable'}

        # Transformable matches that have at least one cpcode in their subtree
        transformable = {}
        for pos, info in self.match_map.items():
            if not info['cpcodes']:
                continue
            match_type = self._extract_match_type(info['full_tag'])
            if match_type in TRANSFORMABLE_TYPES:
                transformable[pos] = info

        edits: List[dict] = []

        # 1) Rewrite opening/closing tags of every transformable match (pass-through)
        for pos, info in transformable.items():
            match_type = self._extract_match_type(info['full_tag'])
            new_open = self._build_header_match_tag(info['cpcodes'])

            # Preserve variable assignment so downstream logic still works
            if match_type == 'variable':
                assignment = self._create_variable_assignment(info['full_tag'])
                if assignment:
                    new_open = new_open + assignment
                else:
                    logger.warning(
                        f"Could not build assign:variable for {info['full_tag'][:80]}"
                    )

            edits.append({
                'start': pos,
                'end': info['tag_end'],
                'text': new_open,
                'kind': 'match-open',
            })
            edits.append({
                'start': info['closing_pos'],
                'end': info['closing_end'],
                'text': '</match:request.header>',
                'kind': 'match-close',
            })

        # Content ranges (inside the opening/closing tags) of transformable matches
        transformable_ranges = [
            (info['tag_end'], info['closing_pos']) for info in transformable.values()
        ]

        def _inside_transformable(p: int) -> bool:
            return any(start <= p < end for start, end in transformable_ranges)

        # 2) Wrap each individual cpcode that lives inside a transformable match
        cpcode_count = 0
        for m in self.cpcode_pattern.finditer(content):
            c_start, c_end = m.start(), m.end()
            cpcode_value = m.group(1)

            if self._is_inside_comment(c_start):
                continue
            if not _inside_transformable(c_start):
                continue

            # Preserve the existing indentation of the cpcode line so the wrapped
            # block stays readable (one tag per line) instead of collapsing to a
            # single long line.
            line_start = content.rfind('\n', 0, c_start) + 1
            indent = content[line_start:c_start]
            # Only treat it as indentation if it is purely whitespace
            if indent.strip():
                indent = ''

            wrapped = (
                f'<match:request.header name="x-replay-expected-cpcode" '
                f'result="true" value="{cpcode_value}" value-wildcard="off" value-case="off">\n'
                f'{indent}    {m.group(0)}\n'
                f'{indent}</match:request.header>'
            )
            edits.append({
                'start': c_start,
                'end': c_end,
                'text': wrapped,
                'kind': 'cpcode-gate',
            })
            cpcode_count += 1

        stats = {
            'matches': len(transformable),
            'cpcodes': cpcode_count,
        }
        logger.info(
            f"Will rewrite {stats['matches']} match tags and gate {stats['cpcodes']} cpcodes"
        )
        return edits, stats

    def _build_header_match_tag(self, cpcodes: List[str]) -> str:
        """
        Build an x-replay-expected-cpcode header match opening tag for the given
        set of cpcodes. With multiple cpcodes the value is a space-separated list
        and wildcard matching is enabled so any one of them satisfies the match.

        Args:
            cpcodes: List of cpcode strings

        Returns:
            Opening tag string for <match:request.header ...>
        """
        unique = list(dict.fromkeys(cpcodes))
        value = ' '.join(unique)
        wildcard = 'on' if len(unique) > 1 else 'off'
        return (
            f'<match:request.header name="x-replay-expected-cpcode" '
            f'result="true" value="{value}" value-wildcard="{wildcard}" value-case="off">'
        )

    def _extract_match_type(self, full_tag: str) -> str:
        """
        Extract match type from full match tag.

        Examples:
            '<match:variable name="PM_7" ...>' -> 'variable'
            '<match:request.header name="X-Foo" ...>' -> 'request.header'
            '<match:request.cookie name="session" ...>' -> 'request.cookie'

        Returns:
            Match type string (e.g., 'variable', 'request.header', 'request.cookie')
        """
        match = re.match(r'<match:([^\s>]+)', full_tag)
        if match:
            return match.group(1)
        return 'unknown'

    def _create_variable_assignment(self, full_tag: str) -> str:
        """
        Create assign:variable block from match:variable tag.

        Example input:
            '<match:variable name="PM_7" value="t" result="true">'

        Example output:
            '<assign:variable>
                <name>PM_7</name>
                <value>t</value>
                <hidden>on</hidden>
            </assign:variable>
            '

        Returns:
            XML string for assign:variable block, or empty string if cannot parse
        """
        try:
            # Extract name and value attributes
            name_match = re.search(r'name="([^"]+)"', full_tag)
            value_match = re.search(r'value="([^"]+)"', full_tag)

            if not name_match or not value_match:
                logger.warning(f"Could not extract name/value from: {full_tag}")
                return ''

            var_name = name_match.group(1)
            var_value = value_match.group(1)

            # Create assign:variable block
            assignment = (
                f'\n                <assign:variable>\n'
                f'                    <name>{var_name}</name>\n'
                f'                    <value>{var_value}</value>\n'
                f'                    <hidden>on</hidden>\n'
                f'                </assign:variable>\n'
            )

            logger.debug(f"Created variable assignment: {var_name}={var_value}")
            return assignment

        except Exception as e:
            logger.warning(f"Failed to create variable assignment: {e}")
            return ''



def transform_arl_for_replay(arl_file_path: str) -> dict:
    """
    Convenience function to transform an ARL file for replay mode.

    Args:
        arl_file_path: Path to ARL file

    Returns:
        Transformation statistics
    """
    transformer = ARLTransformer()
    return transformer.transform_arl_for_replay(arl_file_path)
