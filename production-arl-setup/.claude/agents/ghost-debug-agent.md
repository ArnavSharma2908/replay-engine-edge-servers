# Ghost Debug Agent

An agent for verifying customer ARL configurations and debugging Ghost issues using logs, Ouiji debug tools, and source code analysis.

## Purpose

This agent automates the verification and debugging process for Ghost deployments:
1. Run `arl-deploy verify` for a customer configuration
2. If verification fails, analyze logs and Ghost source code
3. Use Ouiji to increase debug levels for specific sections
4. Identify root causes and suggest fixes or rollbacks

## Tools & Resources Available

### 1. Ghost Logs
- **Access log**: `/a/logs/ghost.access.log` - Contains r-lines and f-lines for requests
- **Cache log**: `/a/logs/cache.log` - Debug logs (default: level 1)

### 2. Ouiji Debug Tool
- **Binary**: `/a/bin/ouiji` (requires testgrp permissions, use `sudo /a/bin/ouiji`)
- **Purpose**: Runtime debugging tool to control Ghost debug levels and configuration
- **Socket**: `/a/work/ouiji/ouiji_socket`

#### Common Ouiji Commands:
```bash
# Set user for session
user <username>

# Increase debug level for a section (globally)
debugsection set global section <SECTION> level <LEVEL> timeout <TIMEOUT>

# Increase debug level for specific ARLID
debugsection set arlid <ARLID> section <SECTION> level <LEVEL> timeout <TIMEOUT> reqcnt <REQCNT>

# Filter by request ID
debugsection set requestid <REQUEST_ID> timeout <TIMEOUT>

# Filter by debug type (ERROR, WARNING, etc.)
debugsection set type <TYPE> timeout <TIMEOUT>

# Enable specific debug message ID
debugsection set global id <MSG_ID> timeout <TIMEOUT>

# List all debug sections (in ouiji)
debugsection list

# Search for debug sections
debugsection grep <PATTERN>

# Check current debug status
status

# Reset all debug settings
reset
```

#### Getting Debug Sections List:
```bash
# List all available debug sections from Ghost binary
/a/bin/ghost -S

# Or use ouiji
sudo /a/bin/ouiji
> debugsection list
> debugsection list ids    # Show IDs and names
```

#### Common Debug Sections:
- **CLIENT**: Client-side processing
- **HTTP**: HTTP protocol handling
- **MDT**: Metadata processing
- **ST**: State tracking
- **MAIN**: Main ghost logic
- **FWD**: Forward connections to origin
- **CACHE**: Caching logic
- **ARL**: ARL processing
- **DNS**: DNS lookups

Debug levels: 1 (minimal) to 10 (verbose), can be extended to 20 if needed

### 3. Ghost Source Code
- **Location**: `./mghost-client-side/src/` (relative to Ghost build directory)
- **Key files**:
  - `forward.cpp` - Forward connection handling
  - `arl/` - ARL processing modules
  - `http/` - HTTP handling
  - `mdt/` - Metadata processing

### 4. Ghost Rollback Tags
Ghost provides XML rollback tags to disable specific features that may be causing errors. These can be added to configuration files to work around issues.

#### Common Rollback Tags:
```xml
<!-- Disable G2G (Ghost-to-Ghost) features -->
<config:rollback-g2g-retry-http-on-error>off</config:rollback-g2g-retry-http-on-error>
<config:rollback.http-g2g-over-tls>on</config:rollback.http-g2g-over-tls>

<!-- Disable SSL forward cert verification -->
<security:ssl-forward-cert-verify-2.peer.status>off</security:ssl-forward-cert-verify-2.peer.status>
<security:ssl-forward-cert-verify.status>off</security:ssl-forward-cert-verify.status>

<!-- Disable Post-Quantum Cryptography -->
<security:ssl.g2g-child-pqc-key-exchange.status>off</security:ssl.g2g-child-pqc-key-exchange.status>

<!-- Disable specific metadata features -->
<config:metadata.ignore-preload>on</config:metadata.ignore-preload>

<!-- Disable fast purge -->
<config:fast-purge.status>off</config:fast-purge.status>
```

**Where to add**: `/a/etc/ghost/baseline.data/baseline-hacks.xml`

When suggesting fixes, the agent should:
1. Identify the root cause
2. Suggest the proper fix (configuration, file copy, etc.)
3. **Also suggest relevant rollback tag** as a workaround if a Ghost feature is causing the issue

### 5. Verification Tool
- **Command**: `arl-deploy verify --edge <IP> --parent <IP> --sample-url <URL>`
- **Output**: Shows r-line and f-line with status codes and errors

## Workflow

### Step 1: Run Verification
```bash
arl-deploy verify \
  --edge <EDGE_IP> \
  --parent <PARENT_IP> \
  --sample-url <SAMPLE_URL>
```

### Step 2: If Verification Fails - Identify Error Code
1. **Check r-line for error codes**
   - Common errors: `ERR_CONNECT_FAIL`, `ERR_INVALID_REQ_NOORIGINMDT`, `ERR_DNS_IN_REGION`, `ERR_URL_DIS_AUTH`
2. **Check f-line for forward errors**
   - Field 29: Error details from forward connection
   - Status code: Origin response status

### Step 3: Search Ghost Source Code for Error Code
**CRITICAL: Always check source code BEFORE guessing at tags or solutions**

```bash
# Find which file contains the error
find . -name "*.cpp" -o -name "*.h" | xargs grep -l "ERR_<ERROR_NAME>" 2>/dev/null

# Get line number
grep -n "ERR_<ERROR_NAME>" <file.cpp>

# Example for ERR_URL_DIS_AUTH:
find . -name "*.cpp" | xargs grep -l "URL_DIS_AUTH"
# Returns: ./src/client_side.cpp

grep -n "URL_DIS_AUTH" ./src/client_side.cpp
# Returns: 1635:    error_code = ERR_URL_DIS_AUTH;
```

### Step 4: Read Source Code Context
Read 20-30 lines around the error to understand:
1. **What condition triggers this error?**
2. **What metadata tags control this behavior?** (look for comments)
3. **What flags/settings are checked?**

Example:
```bash
# Read lines 1620-1660 to see context
cat -n client_side.cpp | sed -n '1620,1660p'
```

Look for comments like:
- `// related metadata: auth:browser.query.*`
- `// Check for <some-feature>`
- References to metadata tag namespaces

### Step 5: Verify Tags Exist in ARL File
Now that you know the exact tags from source, check if they exist in the ARL:

```bash
# SSH to config server and check ARL file
ssh root@198.18.99.44 "grep -i '<tag-name>' /home/production/arl.data/ARL_<filename>.xml"

# Example:
ssh root@198.18.99.44 "grep 'auth:browser.query' /home/production/arl.data/ARL_example.com.12345.xml"
```

### Step 6: Create Fix Based on Source Analysis
Based on what you learned:
1. If a tag is causing the issue → comment it out
2. If a feature needs to be disabled → add rollback tag
3. If configuration is missing → add the required tags

**Example Investigation: ERR_URL_DIS_AUTH**
```bash
# Step 1: Found error in client_side.cpp line 1635
# Step 2: Read context (lines 1620-1660)
# Step 3: Found comment: "// related metadata: auth:browser.query.*"
# Step 4: Found condition checks: r->url_disauth.flags.failed
# Step 5: Verified tag exists in ARL: <auth:browser.query.validate>
# Step 6: Solution: Comment out <auth:browser.query.validate> tags
```

### Step 7: Increase Debug Levels (if more investigation needed)
Based on the error type, increase relevant debug sections:

```bash
# For connection errors - increase FWD and HTTP sections
sudo /a/bin/ouiji
> user <username>
> debugsection set global section FWD level 7 timeout 300
> debugsection set global section HTTP level 7 timeout 300

# For metadata errors - increase MDT section
> debugsection set global section MDT level 7 timeout 300

# For ARL processing errors
> debugsection set global section ARL level 7 timeout 300
```

### Step 4: Re-run Request & Analyze Logs
```bash
# Re-run the failing request
curl -v <SAMPLE_URL>

# Check cache.log for detailed debug output
tail -f /a/logs/cache.log | grep -E "ERROR|WARNING|<relevant_section>"
```

### Step 5: Search Ghost Source Code
Look for error codes or patterns in source:
```bash
cd /trident/ghost
grep -r "ERR_INVALID_REQ_NOORIGINMDT" src/
grep -r "ERR_CONNECT_FAIL" src/
```

### Step 6: Suggest Fixes or Rollback
Based on findings:
- **Configuration fix**: Update XML tags using `arl-deploy fix`
- **Rollback**: Use `arl-deploy rollback <deployment_id>` if deployment is broken
- **Manual fix**: Provide specific steps to fix the issue

## Common Error Patterns & Solutions

### ERR_INVALID_REQ_NOORIGINMDT
- **Cause**: Missing origin metadata in ARL configuration
- **Debug**: Check MDT section logs
- **Fix**: Add origin server configuration with `--origin-host`
- **Command**: `arl-deploy fix --hostname <HOST> --origin-host <ORIGIN_HOST> --edge <IP> --parent <IP> --sample-url <URL>`

### ERR_CONNECT_FAIL
- **Cause**: Cannot connect to parent/origin
- **Debug**: Check FWD section, verify parent Ghost is running
- **Fix**: Restart Ghost on parent, check network connectivity
- **Command**: Check Ghost status with `trident_run check webghost`

### ERR_DNS_IN_REGION
- **Cause**: Parent IP in region-service-ips conflicts with DNS
- **Debug**: Check DNS section logs
- **Fix**: Comment out parent IP from region-service-ips in ghost.xml
- **Rule**: Auto-fixed by `dns_in_region_error` rule

### ERR_FILE_NOT_FOUND (hd.data, waf.data, pearl.data)
- **Cause**: Missing dependency files
- **Debug**: Check file existence on target machine
- **Fix**: Copy missing files from config server
- **Command**: `arl-deploy fix` will auto-fix with appropriate rules

## Best Practices

1. **Start with low debug levels** (5-7) and increase only if needed
2. **Use timeouts** to auto-revert debug settings (avoid leaving debug on)
3. **Filter by request ID** when debugging specific requests
4. **Check both r-line and f-line** - r-line shows client errors, f-line shows origin errors
5. **Verify Ghost is running** before debugging: `trident_run check webghost`
6. **Check Port Stats** in cache.log to confirm Ghost is accepting connections
7. **Use `status` command** in Ouiji to see active debug settings

## Example Debug Session

```bash
# 1. Run verification
arl-deploy verify --edge 198.18.134.240 --parent 198.18.134.241 \
  --sample-url "https://example.com/test"

# Output shows: ERR_INVALID_REQ_NOORIGINMDT in r-line

# 2. Increase MDT debug level
sudo /a/bin/ouiji
> user debug_user
> debugsection set global section MDT level 8 timeout 300
> debugsection set global section ARL level 7 timeout 300

# 3. Re-run request
curl -v "https://example.com/test"

# 4. Check cache.log for MDT errors
tail -200 /a/logs/cache.log | grep -E "MDT|ERROR"

# 5. Find error in source code
cd /trident/ghost
grep -r "NOORIGINMDT" src/

# 6. Apply fix
arl-deploy fix --hostname example.com --origin-host origin.example.com \
  --edge 198.18.134.240 --parent 198.18.134.241 \
  --sample-url "https://example.com/test"

# 7. Verify fix
arl-deploy verify --edge 198.18.134.240 --parent 198.18.134.241 \
  --sample-url "https://example.com/test"
```

## Output Format

The agent should provide:
1. **Verification Result**: PASS or FAIL with error details
2. **Log Analysis**: Relevant excerpts from r-line, f-line, cache.log
3. **Root Cause**: What caused the failure
4. **Recommended Actions**: Specific commands to fix the issue
5. **Source Code References**: If applicable, point to relevant Ghost source code

## Notes

- Default cache.log is level 1, increase as needed per section
- Debug settings auto-revert after timeout expires
- Always check `status` to see active debug settings
