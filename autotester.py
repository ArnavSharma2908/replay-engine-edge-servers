#!/usr/bin/env python3
"""
Auto-tester for production replay orchestrator
Tests all files in 'media' folder through orchestrator steps 1, 2, 3, and 4
Logs results and tracks pass/fail counts
"""

import os
import shutil
import subprocess
from pathlib import Path

IDX = 6
STEPS = {1: True, 2: True, 3: True, 4: False}


def validate_steps_config():
    allowed_steps = {1, 2, 3, 4}

    invalid_keys = [k for k in STEPS.keys() if k not in allowed_steps]
    if invalid_keys:
        raise ValueError(
            f"Invalid step keys in STEPS: {invalid_keys}. Only 1, 2, 3, 4 are allowed."
        )

    invalid_values = [k for k, v in STEPS.items() if not isinstance(v, bool)]
    if invalid_values:
        raise TypeError(
            f"Invalid boolean values in STEPS for keys: {invalid_values}. Values must be True/False."
        )


def run_step_1(file_name, log_file):
    cmd = "python orchestrate.py --edge 198.18.134.240 --parent 198.18.134.241 --steps 1"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    log_content = (
        "=" * 80 + "\n"
        + f"FILE: {file_name}\n"
        + "STEP: 1 (Generate Traffic)\n"
        + f"COMMAND: {cmd}\n"
        + f"EXIT CODE: {result.returncode}\n"
        + f"STDOUT:\n{result.stdout}\n"
    )
    if result.stderr:
        log_content += f"STDERR:\n{result.stderr}\n"
    log_content += "=" * 80 + "\n\n"

    with open(log_file, "a") as f:
        f.write(log_content)

    print(log_content)
    if result.returncode == 0:
        print("  ✓ Step 1: PASS")
        return True

    print(f"  ✗ Step 1: FAIL (exit code {result.returncode})")
    return False


def run_step_2(file_name, log_file):
    cmd = "python orchestrate.py --edge 198.18.134.240 --parent 198.18.134.241 --steps 2"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    log_content = (
        "=" * 80 + "\n"
        + f"FILE: {file_name}\n"
        + "STEP: 2 (Deploy ARLs)\n"
        + f"COMMAND: {cmd}\n"
        + f"EXIT CODE: {result.returncode}\n"
        + f"STDOUT:\n{result.stdout}\n"
    )
    if result.stderr:
        log_content += f"STDERR:\n{result.stderr}\n"
    log_content += "=" * 80 + "\n\n"

    with open(log_file, "a") as f:
        f.write(log_content)

    print(log_content)
    if result.returncode == 0:
        print("  ✓ Step 2: PASS")
        return True

    print(f"  ✗ Step 2: FAIL (exit code {result.returncode})")
    return False


def run_step_3(file_name, log_file):
    cmd = "python orchestrate.py --edge 198.18.134.240 --parent 198.18.134.241 --steps 3"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    log_content = (
        "=" * 80 + "\n"
        + f"FILE: {file_name}\n"
        + "STEP: 3 (Step 3)\n"
        + f"COMMAND: {cmd}\n"
        + f"EXIT CODE: {result.returncode}\n"
        + f"STDOUT:\n{result.stdout}\n"
    )
    if result.stderr:
        log_content += f"STDERR:\n{result.stderr}\n"
    log_content += "=" * 80 + "\n\n"

    with open(log_file, "a") as f:
        f.write(log_content)

    print(log_content)
    if result.returncode == 0:
        print("  ✓ Step 3: PASS")
        return True

    print(f"  ✗ Step 3: FAIL (exit code {result.returncode})")
    return False


def run_step_4(file_name, log_file):
    cmd = "python orchestrate.py --edge 198.18.134.240 --parent 198.18.134.241 --steps 4"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    log_content = (
        "=" * 80 + "\n"
        + f"FILE: {file_name}\n"
        + "STEP: 4 (Step 4)\n"
        + f"COMMAND: {cmd}\n"
        + f"EXIT CODE: {result.returncode}\n"
        + f"STDOUT:\n{result.stdout}\n"
    )
    if result.stderr:
        log_content += f"STDERR:\n{result.stderr}\n"
    log_content += "=" * 80 + "\n\n"

    with open(log_file, "a") as f:
        f.write(log_content)

    print(log_content)
    if result.returncode == 0:
        print("  ✓ Step 4: PASS")
        return True

    print(f"  ✗ Step 4: FAIL (exit code {result.returncode})")
    return False


def main():
    validate_steps_config()

    media_dir = Path("media")
    input_dir = Path("input")
    output_dir = Path("output")
    done_dir = Path("done")
    log_file = Path(f"logs/log{IDX}.txt") 
    
    error_count = 0
    success_count = 0
    
    # Check if log file already exists
    if log_file.exists():
        raise FileExistsError(f"Log file already exists: {log_file.absolute()}")
    
    # Ensure output and logs directories exist
    done_dir.mkdir(exist_ok=True)
    input_dir.mkdir(exist_ok=True)
    log_file.parent.mkdir(exist_ok=True)
    
    # Create empty log file
    log_file.write_text("")
    
    print(f"Starting autotester...")
    print(f"Media files found: {len(list(media_dir.glob('*')))}")
    print(f"Log file: {log_file.absolute()}\n")
    
    # Process each file in media folder
    while True:
        media_files = list(media_dir.glob("*"))
        if not media_files:
            break
        
        file_to_process = media_files[0]
        
        # Delete output folder
        if output_dir.exists():
            shutil.rmtree(output_dir)
        
        # Move file from media to input (cut & paste)
        dest_input = input_dir / file_to_process.name
        shutil.move(str(file_to_process), str(dest_input))
        print(f"Processing: {file_to_process.name}")

        if STEPS.get(1, False):
            if run_step_1(file_to_process.name, log_file):
                success_count += 1
            else:
                error_count += 1

        if STEPS.get(2, False):
            if run_step_2(file_to_process.name, log_file):
                success_count += 1
            else:
                error_count += 1

        if STEPS.get(3, False):
            if run_step_3(file_to_process.name, log_file):
                success_count += 1
            else:
                error_count += 1

        if STEPS.get(4, False):
            if run_step_4(file_to_process.name, log_file):
                success_count += 1
            else:
                error_count += 1
        
        # Move file from input to done (cut & paste)
        input_files = list(input_dir.glob("*"))
        if input_files:
            file_to_move = input_files[0]
            dest_done = done_dir / file_to_move.name
            shutil.move(str(file_to_move), str(dest_done))
        
        print()
    
    # Print summary
    print("=" * 80)
    print("TESTING COMPLETE")
    print("=" * 80)
    print(f"Total Runs: {error_count + success_count}")
    print(f"Successes (exit code 0): {success_count}")
    print(f"Errors (exit code 1+): {error_count}")
    print(f"Log file: {log_file.absolute()}")
    print("=" * 80)

if __name__ == "__main__":
    main()