#!/usr/bin/env python3
"""
Parse k6 console output to extract failure details into JSONL files.
Usage: k6 run ... 2>&1 | tee k6_output.log && python3 parse_k6_failures.py k6_output.log
"""

import json
import re
import sys
from collections import defaultdict


def parse_k6_log(log_path):
    """Extract failure logs from k6 console output."""
    failures = defaultdict(list)
    
    with open(log_path, 'r', encoding='utf-8') as f:
        for line in f:
            # Look for lines with "FAILURE: " in them
            if 'FAILURE: ' in line:
                try:
                    # k6 uses structured logging: msg="FAILURE: {json}" source=console
                    # Extract JSON from msg field
                    json_start = line.index('FAILURE: ') + len('FAILURE: ')
                    # Find the closing of the msg field (before " source=)
                    json_end = line.rindex('" source=')
                    failure_json = line[json_start:json_end]
                    
                    # Unescape the JSON string (k6 escapes quotes and backslashes)
                    failure_json = failure_json.replace('\\"', '"').replace('\\\\', '\\')
                    failure = json.loads(failure_json)
                    
                    # Group by failure type
                    for fail in failure.get('failures', []):
                        validation_type = fail.get('validation')
                        
                        detail = {
                            'timestamp': failure.get('timestamp', ''),
                            'vu': failure.get('vu'),
                            'iteration': failure.get('iteration'),
                            'global_index': failure.get('global_index'),
                            'request_id': failure.get('request_id'),
                            'url': failure.get('url'),
                            'method': failure.get('method'),
                            'host': failure.get('host'),
                            'expected': fail.get('expected'),
                            'actual': fail.get('actual'),
                            'diff_absolute': fail.get('diff_absolute'),
                            'diff_percent': fail.get('diff_percent'),
                        }
                        
                        if validation_type:
                            failures[validation_type].append(detail)
                
                except (ValueError, json.JSONDecodeError, KeyError) as e:
                    print(f"Warning: Could not parse failure line: {e}", file=sys.stderr)
                    continue
    
    return failures


def write_failures(failures, output_dir='./output'):
    """Write failures to JSONL files."""
    # Determine wave type from first failure's context (if available)
    wave_type = 'unknown'
    for fail_type, details in failures.items():
        if details:
            # Try to extract from URL or use default
            wave_type = 'results'  # fallback
            break
    
    # Write each failure type to its own file
    type_map = {
        'cpcode': f'{output_dir}/cpcode_mismatches.jsonl',
        'status': f'{output_dir}/status_mismatches.jsonl',
        'bytes': f'{output_dir}/bytes_mismatches.jsonl',
    }
    
    written = {}
    for fail_type, file_path in type_map.items():
        details = failures.get(fail_type, [])
        if details:
            with open(file_path, 'w', encoding='utf-8') as f:
                for detail in details:
                    f.write(json.dumps(detail) + '\n')
            written[fail_type] = len(details)
            print(f"✓ Wrote {len(details)} {fail_type} failures to {file_path}")
        else:
            print(f"  No {fail_type} failures found")
    
    return written


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 parse_k6_failures.py <k6_log_file>")
        print("Example: k6 run verify-prewarm.js 2>&1 | tee k6_output.log")
        print("         python3 parse_k6_failures.py k6_output.log")
        sys.exit(1)
    
    log_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else './output'
    
    print(f"Parsing k6 log: {log_path}")
    failures = parse_k6_log(log_path)
    
    total = sum(len(v) for v in failures.values())
    print(f"\nFound {total} total failures:")
    for fail_type, details in failures.items():
        print(f"  {fail_type}: {len(details)}")
    
    print(f"\nWriting failures to {output_dir}/")
    written = write_failures(failures, output_dir)
    
    if written:
        print(f"\n✓ Successfully extracted {sum(written.values())} failures")
    else:
        print("\n✓ No failures found in log")


if __name__ == '__main__':
    main()
