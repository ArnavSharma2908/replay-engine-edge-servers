# ARL Deployer Logging Modes

The ARL Deployer supports two logging modes to provide flexibility between clean CLI output and detailed debugging information.

## Default Mode (Clean, Colorized Output)

By default, `arl-deploy` uses a clean, colorized output format that shows only the essential information:

- **Step headings** in bold blue
- **Success messages** with green ✓ symbol
- **Failure messages** with red ✗ symbol  
- **Warnings** with yellow ⚠ symbol
- **Substeps** indented with status indicators

### Example Default Output:

```bash
$ arl-deploy fix --edge 198.18.134.240 --parent 198.18.134.241 --sample-url https://example.com/test

======================================================================
Starting Self-Healing for fix_426bf57f
======================================================================

⚠ Failure reason: ERR_FWD_CERT_SPEC_MISSING|ssl_fwd_cert

Healing Iteration 1/3
----------------------------------------------------------------------
  → Found 1 matching rule(s)
  ✓ Rule 'missing_ssl_fwd_cert' executed successfully
✓ Rule succeeded, stopping healing

======================================================================
SUMMARY: ALL PASSED
  1 passed, 0 failed out of 1 total
======================================================================
```

## Verbose Mode (Detailed Logging)

Enable verbose mode with the `--verbose` or `-v` flag to see all detailed logging:

```bash
arl-deploy --verbose fix --edge 198.18.134.240 --parent 198.18.134.241 --sample-url https://example.com/test
```

or

```bash
arl-deploy -v fix --edge 198.18.134.240 --parent 198.18.134.241 --sample-url https://example.com/test
```

### Verbose Mode Includes:

- **Timestamps** for all log entries
- **Log levels** (INFO, DEBUG, WARNING, ERROR)
- **Module names** showing where logs originated
- **Detailed action execution** logs
- **SSH command output**
- **XML diff previews**
- **Full stack traces** on errors

### Example Verbose Output:

```bash
$ arl-deploy --verbose fix --edge 198.18.134.240 --parent 198.18.134.241 --sample-url https://example.com/test

======================================================================
Starting Self-Healing for fix_426bf57f
======================================================================

2026-06-19 17:14:34,810 - arl_deployer.rules - INFO - === Starting Self-Healing for fix_426bf57f ===
2026-06-19 17:14:34,810 - arl_deployer.rules - INFO - Failure reason: ERR_FWD_CERT_SPEC_MISSING|ssl_fwd_cert

⚠ Failure reason: ERR_FWD_CERT_SPEC_MISSING|ssl_fwd_cert

Healing Iteration 1/3
----------------------------------------------------------------------

2026-06-19 17:14:34,810 - arl_deployer.rules - INFO - 
--- Healing Iteration 1/3 ---
2026-06-19 17:14:34,635 - arl_deployer.rules - INFO - Found 1 matching rule(s)
  → Found 1 matching rule(s)

2026-06-19 17:14:34,635 - arl_deployer.rules - INFO - Executing rule: missing_ssl_fwd_cert
2026-06-19 17:14:34,635 - arl_deployer.rules - INFO -   Action: modify_xml - Comment out auth:origin.ssl-fwd-cert tags in ARL file
2026-06-19 17:14:34,636 - arl_deployer.rules - INFO -   Commenting tags matching patterns: ['auth:origin.ssl-fwd-cert']
2026-06-19 17:14:35,150 - xmlmaniplib.xml_manipulator - INFO - Commenting tag: auth:origin.ssl-fwd-cert
2026-06-19 17:14:35,150 - xmlmaniplib.xml_manipulator - INFO -   Content preview: <auth:origin.ssl-fwd-cert><name>%(PMUSER_G2O_CERT_NAME)</name><status>on</status><enforce-origin-request>off</enforce-origin-request></auth:origin.ssl-fwd-cert>
2026-06-19 17:14:35,151 - xmlmaniplib.xml_manipulator - INFO - Commented 1 tag(s) matching pattern: auth:origin.ssl-fwd-cert
2026-06-19 17:14:35,157 - arl_deployer.rules - INFO -   XML Diff Summary on edge@198.18.134.240: +1 -1 lines
2026-06-19 17:14:35,157 - arl_deployer.rules - INFO -   Diff preview (first 20 lines):
2026-06-19 17:14:35,157 - arl_deployer.rules - INFO -     @@ -655,7 +655,7 @@
2026-06-19 17:14:35,157 - arl_deployer.rules - WARNING -     -            <auth:origin.ssl-fwd-cert>
2026-06-19 17:14:35,157 - arl_deployer.rules - INFO -     +            <!--auth:origin.ssl-fwd-cert>
...

  ✓ Rule 'missing_ssl_fwd_cert' executed successfully
✓ Rule succeeded, stopping healing
```

## Color Support

Colors are automatically enabled when:
- Output is to a TTY (terminal)
- `TERM` environment variable is set

Colors are automatically disabled when:
- Output is redirected to a file: `arl-deploy fix ... > output.log`
- Running in a non-TTY environment (CI/CD pipelines)

You can manually disable colors by redirecting to a file or using `NO_COLOR=1` environment variable.

## Use Cases

### When to Use Default Mode
- **Interactive CLI usage** - Clean, easy-to-read output
- **Quick verification** - Focus on pass/fail results
- **Demos and presentations** - Professional, colorized output

### When to Use Verbose Mode
- **Debugging issues** - See all internal operations
- **Understanding failures** - Full context of what went wrong
- **Development** - Detailed logs for troubleshooting
- **Log file generation** - Capture complete execution history

## Global Verbose Flag

The `--verbose` flag is a **global option** that must come **before** the subcommand:

```bash
# ✓ Correct
arl-deploy --verbose fix --edge ...
arl-deploy -v verify --edge ...

# ✗ Incorrect
arl-deploy fix --verbose --edge ...  # Won't work
```

## Examples

### Verify with default output:
```bash
arl-deploy verify --edge 198.18.134.240 --parent 198.18.134.241 --sample-url https://example.com/
```

### Verify with verbose output:
```bash
arl-deploy --verbose verify --edge 198.18.134.240 --parent 198.18.134.241 --sample-url https://example.com/
```

### Fix with verbose output and save to log:
```bash
arl-deploy --verbose fix --edge 198.18.134.240 --parent 198.18.134.241 --sample-url https://example.com/ 2>&1 | tee healing.log
```

### Deploy without verbose (clean output):
```bash
arl-deploy deploy --cpcode 1608524 --hostname example.com --edge 198.18.134.240 --parent 198.18.134.241
```
