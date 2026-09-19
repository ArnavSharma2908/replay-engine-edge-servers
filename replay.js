import http from 'k6/http';
import { sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { SharedArray } from 'k6/data';
import { textSummary } from 'https://jslib.k6.io/k6-summary/0.0.1/index.js';

// ============================================================================
// CUSTOM METRICS
// ============================================================================
const validationSuccessRate = new Rate('validation_success_rate');
const responseTime = new Trend('response_time');
const bytesThroughput = new Counter('bytes_throughput');
const cpcodeFailures = new Counter('cpcode_failures');
const statusFailures = new Counter('status_failures');
const bytesFailures = new Counter('bytes_failures');
const logStartMs = Date.now();

// Validation configuration
const BYTES_TOLERANCE = parseFloat(__ENV.BYTES_TOLERANCE || '0.10'); // 10% tolerance
const BYTES_ABSOLUTE_TOLERANCE = parseInt(__ENV.BYTES_ABSOLUTE_TOLERANCE || '10', 10); // 10 bytes absolute tolerance
const ACCEPTABLE_FAIL_RATE = parseFloat(__ENV.ACCEPTABLE_FAIL_RATE || '0.05'); // 5%

// ============================================================================
// FAILURE COLLECTION ARRAYS
// ============================================================================
let cpcodeFailureDetails = [];
let statusFailureDetails = [];
let bytesFailureDetails = [];

function ts() {
  return `${Date.now() - logStartMs}ms`;
}

// DEBUG_VU options:
// - all (default): logs from all VUs
// - <number>: logs only from that VU, e.g. DEBUG_VU=2
const DEBUG_VU = (__ENV.DEBUG_VU || 'all').toLowerCase();
const LOG_LEVEL = (__ENV.LOG_LEVEL || 'info').toLowerCase();

function shouldLogForCurrentVu() {
  if (DEBUG_VU === 'all') {
    return true;
  }

  const targetVu = parseInt(DEBUG_VU, 10);
  if (Number.isNaN(targetVu) || targetVu < 1) {
    return true;
  }

  const currentVu = typeof __VU === 'number' ? __VU : 0;
  return currentVu === targetVu;
}

function shouldLogInfo() {
  return LOG_LEVEL === 'info';
}

function shouldLogRequest() {
  return LOG_LEVEL === 'request';
}

function logInfo(message) {
  if (shouldLogForCurrentVu() && shouldLogInfo()) {
    console.info(`[${ts()}] ${message}`);
  }
}

function logWarn(message) {
  if (shouldLogForCurrentVu() && shouldLogInfo()) {
    console.warn(`[${ts()}] ${message}`);
  }
}

function logRequest(message) {
  if (shouldLogForCurrentVu() && shouldLogRequest()) {
    console.info(`[${ts()}] ${message}`);
  }
}

// Shard loading configuration
const SHARD_DIR = __ENV.SHARD_DIR || './output/shards';
const SHARD_PREFIX = __ENV.SHARD_PREFIX || 'shard_';
const SHARD_SUFFIX = __ENV.SHARD_SUFFIX || '.jsonl';
const TEST_VUS = parseInt(__ENV.TEST_VUS || '2', 10);
const TEST_HOST = __ENV.TEST_HOST || '';
const REPLAY_MODE = (__ENV.REPLAY_MODE || 'origin').toLowerCase();

// Parse HOST_MAPPINGS environment variable
// Format: "hostname1:ip1,hostname2:ip2" or JSON string
function parseHostMappings() {
  const hostMappingsEnv = __ENV.HOST_MAPPINGS || '';

  if (!hostMappingsEnv) {
    return {};
  }

  // Try parsing as JSON first (e.g., '{"www.staples.com":"198.18.134.240"}')
  try {
    const parsed = JSON.parse(hostMappingsEnv);
    if (typeof parsed === 'object' && parsed !== null) {
      return parsed;
    }
  } catch (e) {
    // Not JSON, try comma-separated format
  }

  // Parse comma-separated format: "hostname1:ip1,hostname2:ip2"
  const mappings = {};
  const pairs = hostMappingsEnv.split(',');

  for (const pair of pairs) {
    const parts = pair.trim().split(':');
    if (parts.length === 2) {
      const hostname = parts[0].trim();
      const ip = parts[1].trim();
      if (hostname && ip) {
        mappings[hostname] = ip;
      }
    }
  }

  return mappings;
}

const HOST_MAPPINGS = parseHostMappings();

function parseJsonlConnections(content, filePath) {
  const lines = content.split('\n');
  const connections = [];

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i].trim();
    if (!line) {
      continue;
    }

    try {
      connections.push(JSON.parse(line));
    } catch (error) {
      throw new Error(`Invalid JSONL at ${filePath}:${i + 1} - ${error.message}`);
    }
  }

  return connections;
}

// Compatibility mode for k6 versions that do not support k6/experimental/fs.
// This still uses per-VU shard files, but parses each shard file as text in init context.
const shardConnectionsByVu = {};
for (let vu = 1; vu <= TEST_VUS; vu += 1) {
  const shardFile = `${SHARD_DIR}/${SHARD_PREFIX}${vu}${SHARD_SUFFIX}`;
  shardConnectionsByVu[vu] = new SharedArray(`shard_${vu}`, function () {
    const content = open(shardFile);
    return parseJsonlConnections(content, shardFile);
  });
}

// ============================================================================
// K6 CONFIGURATION
// ============================================================================
// Build k6 options with dynamic host mappings
function buildK6Options() {
  const opts = {
    scenarios: {
      replay_connections: {
        executor: 'per-vu-iterations',
        vus: TEST_VUS,
        iterations: 1,  // Each VU processes its shard once
        maxDuration: '24h',
      },
    },
    thresholds: {
      'http_req_duration': ['p(95)<1000', 'p(99)<2000'],
      'validation_success_rate': [`rate>=${1 - ACCEPTABLE_FAIL_RATE}`],
    },
    insecureSkipTLSVerify: true,
  };

  // Use HOST_MAPPINGS if provided, otherwise fall back to hardcoded default
  if (Object.keys(HOST_MAPPINGS).length > 0) {
    opts.hosts = HOST_MAPPINGS;
    console.log(`Using custom host mappings: ${JSON.stringify(HOST_MAPPINGS)}`);
  } else {
    opts.hosts = {
      '*': '198.18.134.240',
    };
  }

  return opts;
}

export const options = buildK6Options();

// ============================================================================
// HELPER FUNCTIONS
// ============================================================================

/**
 * Build HTTP headers for a request
 */
/**
 * Replace hostname in URL with TEST_HOST if TEST_HOST is defined
 */
function replaceHostInUrl(url, testHost) {
  if (!testHost) {
    return url;
  }

  try {
    // Parse URL manually since k6 doesn't have URL constructor
    // Format: protocol://hostname:port/path?query#hash
    const protocolMatch = url.match(/^([a-z][a-z0-9+.-]*):\/\//i);
    if (!protocolMatch) {
      logWarn(`Failed to parse protocol in URL: ${url}`);
      return url;
    }

    const protocol = protocolMatch[1];
    const rest = url.substring(protocolMatch[0].length);

    // Find the end of hostname (either /, :, ?, or #)
    const hostEndMatch = rest.match(/[/:?#]/);
    let hostname, remainder;

    if (hostEndMatch) {
      const endIdx = hostEndMatch.index;
      hostname = rest.substring(0, endIdx);
      remainder = rest.substring(endIdx);
    } else {
      hostname = rest;
      remainder = '';
    }

    // Reconstruct URL with new hostname
    return `${protocol}://${testHost}${remainder}`;
  } catch (error) {
    logWarn(`Failed to replace hostname in URL: ${url}, error: ${error.message}`);
    return url;
  }
}

function buildHeaders(request) {
  // Create a new headers object to avoid modifying shared data
  const headers = {};
  
  // Copy existing headers if present
  if (request.headers) {
    for (const key in request.headers) {
      headers[key] = request.headers[key];
    }
  }

  // Create replay headers x-replay-expected-status, x-replay-expected-size, x-replay-turnaround-ms, x-replay-transfer-ms 
  headers['x-replay-expected-status'] = request.expected_status || request.r_record.status;
  headers['x-replay-expected-size']   = request.r_record.preferred_size    || 0;
  if (request.f_record) {
    headers['x-replay-turnaround-ms']   = request.f_record.turnaround_ms    || 0;
    headers['x-replay-transfer-ms']     = request.f_record.transfer_ms      || 0;
  }

  
  // Add default headers if not present
  if (!headers['Accept-Encoding']) {
    headers['Accept-Encoding'] = 'gzip, deflate';
  }
  if (!headers['Connection']) {
    headers['Connection'] = 'keep-alive';
  }

  logInfo(`[VU ${__VU}] Request headers (request_id: ${request.request_id}): ${JSON.stringify(headers)}`);
  return headers;
}

/**
 * Check if byte difference is within tolerance
 * Uses whichever is more lenient: percentage OR absolute bytes
 */
function isBytesWithinTolerance(expected, actual) {
  // Calculate absolute difference
  const absoluteDiff = Math.abs(actual - expected);

  // If absolute difference is within tolerance, pass
  if (absoluteDiff <= BYTES_ABSOLUTE_TOLERANCE) {
    return true;
  }

  // If expected is 0, we already checked absolute diff above
  if (expected === 0) {
    return false;
  }

  // Calculate percentage difference
  const percentDiff = absoluteDiff / expected;

  // If percentage difference is within tolerance, pass
  return percentDiff <= BYTES_TOLERANCE;
}

/**
 * Validate response against expectations
 */
function validateResponse(request, response) {
  const validations = {
    cpcode: false,
    status: false,
    bytes: false,
  };

  const failures = [];

  // 1. Validate X-Trident-Cpcode header
  const expectedCpcode = request.r_record.arl_cpcode;
  const actualCpcode = response.headers['X-Trident-Cpcode'] || response.headers['x-trident-cpcode'];
  
  if (actualCpcode === expectedCpcode) {
    validations.cpcode = true;
  } else {
    cpcodeFailures.add(1);
    failures.push({
      validation: 'cpcode',
      expected: expectedCpcode,
      actual: actualCpcode || 'missing',
    });
  }

  // 2. Validate status code
  const expectedStatus = request.expected_status || request.r_record.status;
  if (response.status === expectedStatus) {
    validations.status = true;
  } else {
    statusFailures.add(1);
    failures.push({
      validation: 'status',
      expected: expectedStatus,
      actual: response.status,
    });
  }

  // 3. Validate bytes (with tolerance: 10% OR 10 bytes, whichever is more lenient)
  const expectedBytes = request.r_record.bytes_served || 0;
  const actualBytes = response.body ? response.body.length : 0;
  const withinTolerance = isBytesWithinTolerance(expectedBytes, actualBytes);

  if (withinTolerance) {
    validations.bytes = true;
  } else {
    bytesFailures.add(1);
    const absoluteDiff = Math.abs(actualBytes - expectedBytes);
    const percentDiff = expectedBytes > 0
      ? (absoluteDiff / expectedBytes * 100).toFixed(2)
      : 'N/A';
    failures.push({
      validation: 'bytes',
      expected: expectedBytes,
      actual: actualBytes,
      diff_absolute: absoluteDiff,
      diff_percent: percentDiff,
    });
  }

  const allPassed = validations.cpcode && validations.status && validations.bytes;

  return {
    passed: allPassed,
    validations,
    failures,
    actualBytes,
    actualCpcode,
  };
}

/**
 * Build request body for methods that can send payloads.
 */
function buildRequestBody(request, method) {
  const bodyMethods = ['POST', 'PUT', 'PATCH', 'DELETE'];
  if (!bodyMethods.includes(method)) {
    return null;
  }

  if (request.body !== undefined && request.body !== null) {
    if (typeof request.body === 'string') {
      return request.body;
    }

    try {
      return JSON.stringify(request.body);
    } catch (error) {
      logWarn(
        `[VU ${__VU}] Failed to stringify request body for request_id: ${request.request_id}. Sending empty body.`
      );
      return '';
    }
  }

  if (request.body_len !== undefined && request.body_len !== null) {
    const parsedLen = parseInt(request.body_len, 10);
    if (!Number.isNaN(parsedLen) && parsedLen > 0) {
      return 'x'.repeat(parsedLen);
    }

    // If body_len is present but invalid/non-positive, force zero-length body.
    return '';
  }

  // If neither body nor body_len is provided, send zero-length body.
  return '';
}

/**
 * Execute a single request
 */
function executeRequest(request) {
  let url = request.r_record.canonical_url;
  url = replaceHostInUrl(url, TEST_HOST);
  const method = request.r_record.method || 'GET';
  const headers = buildHeaders(request);
  const body = buildRequestBody(request, method);

  if (body !== null && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }

  logInfo(`[VU ${__VU}] Request: ${method} ${url} (request_id: ${request.request_id})`);
  logRequest(`[VU ${__VU}] Request: ${method} ${url} (request_id: ${request.request_id})`);
  if (body !== null) {
    logInfo(`[VU ${__VU}] Request body bytes (request_id: ${request.request_id}): ${body.length}`);
  }

  const params = {
    headers: headers,
    tags: {
      request_id: request.request_id,
    },
    timeout: '10000ms',
  };

  const response = http.request(method, url, body, params);
  const duration = response.timings.duration;

  responseTime.add(duration);
  bytesThroughput.add(response.body ? response.body.length : 0);

  // Validate response
  const result = validateResponse(request, response);

  // Track success rate
  validationSuccessRate.add(result.passed ? 1 : 0);

  // Log failures and collect in memory
  if (!result.passed) {
    const failureLog = {
      timestamp: new Date().toISOString(),
      vu: __VU,
      request_id: request.request_id,
      url: request.r_record.canonical_url,
      method: method,
      failures: result.failures,
      expected: {
        cpcode: request.r_record.arl_cpcode,
        status: request.expected_status || request.r_record.status,
        bytes: request.r_record.bytes_served || 0,
      },
      actual: {
        cpcode: result.actualCpcode,
        status: response.status,
        bytes: result.actualBytes,
      },
    };
    logWarn(`FAILURE: ${JSON.stringify(failureLog)}`);

    // Collect failures by type in memory
    result.failures.forEach((failure) => {
      const failureDetail = {
        timestamp: new Date().toISOString(),
        vu: __VU,
        request_id: request.request_id,
        url: request.r_record.canonical_url,
        method: method,
        expected: failure.expected,
        actual: failure.actual,
        diff_absolute: failure.diff_absolute,
        diff_percent: failure.diff_percent,
      };
      
      if (failure.validation === 'cpcode') {
        cpcodeFailureDetails.push(failureDetail);
      } else if (failure.validation === 'status') {
        statusFailureDetails.push(failureDetail);
      } else if (failure.validation === 'bytes') {
        bytesFailureDetails.push(failureDetail);
      }
    });
  }

  logInfo(`[VU ${__VU}]   ✓ ${response.status} (${duration.toFixed(0)}ms, ${response.body ? response.body.length : 0} bytes)`);

  return {
    success: result.passed,
    status: response.status,
    duration: duration,
    size: response.body ? response.body.length : 0,
  };
}

/**
 * Execute a batch of concurrent requests (for HTTP/2)
 */
function executeBatch(requests) {
  logInfo(`[VU ${__VU}] Batch: ${requests.length} concurrent requests`);
  requests.forEach((req, idx) => {
    const url = replaceHostInUrl(req.r_record.canonical_url, TEST_HOST);
    logInfo(`[VU ${__VU}]   [${idx + 1}] ${req.r_record.method || 'GET'} ${url} (request_id: ${req.request_id})`);
  });

  const batch = requests.map((request) => {
    let url = request.r_record.canonical_url;
    url = replaceHostInUrl(url, TEST_HOST);
    const method = request.r_record.method || 'GET';
    const headers = buildHeaders(request);
    const body = buildRequestBody(request, method);

    if (body !== null && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }

    logRequest(`[VU ${__VU}] Request: ${method} ${url} (request_id: ${request.request_id})`);

    if (body !== null) {
      logInfo(`[VU ${__VU}]   [batch body] request_id: ${request.request_id}, bytes: ${body.length}`);
    }

    return [
      method,
      url,
      body,
      {
        headers: headers,
        tags: {
          request_id: request.request_id,
        },
        timeout: '10000ms',
      },
    ];
  });

  const responses = http.batch(batch);

  const results = [];
  responses.forEach((response, index) => {
    const request = requests[index];
    
    // Validate response
    const result = validateResponse(request, response);
    
    // Track success rate
    validationSuccessRate.add(result.passed ? 1 : 0);
    responseTime.add(response.timings.duration);
    bytesThroughput.add(response.body ? response.body.length : 0);

    // Log failures and collect in memory
    if (!result.passed) {
      const failureLog = {
        timestamp: new Date().toISOString(),
        vu: __VU,
        batch_index: index,
        request_id: request.request_id,
        url: request.r_record.canonical_url,
        method: request.r_record.method || 'GET',
        failures: result.failures,
        expected: {
          cpcode: request.r_record.arl_cpcode,
          status: request.expected_status || request.r_record.status,
          bytes: request.r_record.bytes_served || 0,
        },
        actual: {
          cpcode: result.actualCpcode,
          status: response.status,
          bytes: result.actualBytes,
        },
      };
      logWarn(`FAILURE: ${JSON.stringify(failureLog)}`);

      // Collect failures by type in memory
      result.failures.forEach((failure) => {
        const failureDetail = {
          timestamp: new Date().toISOString(),
          vu: __VU,
          batch_index: index,
          request_id: request.request_id,
          url: request.r_record.canonical_url,
          method: request.r_record.method || 'GET',
          expected: failure.expected,
          actual: failure.actual,
          diff_absolute: failure.diff_absolute,
          diff_percent: failure.diff_percent,
        };
        
        if (failure.validation === 'cpcode') {
          cpcodeFailureDetails.push(failureDetail);
        } else if (failure.validation === 'status') {
          statusFailureDetails.push(failureDetail);
        } else if (failure.validation === 'bytes') {
          bytesFailureDetails.push(failureDetail);
        }
      });
    }

    logInfo(`[VU ${__VU}]   ✓ [${index + 1}] ${response.status} (${response.timings.duration.toFixed(0)}ms)`);

    results.push({
      request_id: request.request_id,
      success: result.passed,
      status: response.status,
      duration: response.timings.duration,
      size: response.body ? response.body.length : 0,
    });
  });

  return results;
}

/**
 * Execute a connection group
 */
function executeConnection(connection, replayStartTimeMs) {
  const connectionStartOffset = connection.start_offset_ms;

  // Calculate how long to sleep before starting this connection
  // replayStartTimeMs is when the test began
  // We want to preserve the relative timing from production
  const elapsedMs = Date.now() - replayStartTimeMs;
  const sleepMs = Math.max(0, connectionStartOffset - elapsedMs);

  logInfo(`[VU ${__VU}] Connection ${connection.connection_group_index}: start_offset=${connectionStartOffset}ms, elapsed=${elapsedMs.toFixed(0)}ms`);

  if (sleepMs > 0) {
    logInfo(`[VU ${__VU}]   Sleeping ${(sleepMs / 1000).toFixed(3)}s before starting connection`);
    sleep(sleepMs / 1000);
  } else if (sleepMs < -1000) {
    logWarn(
      `[VU ${__VU}]   ⚠ Late connection start for ` +
      `connection_group_index=${connection.connection_group_index} ` +
      `(${-sleepMs}ms late)`
    );
  }

  // Execute all request batches in this connection sequentially
  connection.request_batches.forEach((batch, batchIndex) => {
    logInfo(`[VU ${__VU}]   Batch ${batchIndex}: ${batch.requests.length} request(s)`);

    if (batch.requests.length === 1) {
      // Single request in batch (sequential)
      executeRequest(batch.requests[0]);
    } else {
      // Multiple requests in batch (concurrent via http.batch)
      executeBatch(batch.requests);
    }

    // Sleep between batches (gap_ms)
    if (batch.gap_ms && batch.gap_ms > 0) {
      logInfo(`[VU ${__VU}]   Waiting ${(batch.gap_ms / 1000).toFixed(3)}s between batches`);
      sleep(batch.gap_ms / 1000);
    }
  });

  logInfo(`[VU ${__VU}] Connection ${connection.connection_group_index} complete`);
}

// ============================================================================
// MAIN TEST EXECUTION
// ============================================================================
export default function () {
  // Each VU owns shard_<VU>.jsonl (e.g., VU1 -> shard_1.jsonl)
  const shardId = __VU;
  const shardFile = `${SHARD_DIR}/${SHARD_PREFIX}${shardId}${SHARD_SUFFIX}`;
  const shard = shardConnectionsByVu[shardId] || [];

  // Track when this VU started its first iteration
  if (__ITER === 0) {
    console.log(`[VU ${__VU}] Starting replay - ${shard.length} connection(s) assigned`);
    logInfo(
      `VU ${__VU} assigned to shard file ${shardFile} ` +
      `(${shard.length} connections)`
    );
  }

  // Execute all connections assigned to this shard
  const replayStartTimeMs = Date.now();
  shard.forEach((connection, connectionIndex) => {
    executeConnection(connection, replayStartTimeMs);
    
    // Log progress every 10 connections
    if ((connectionIndex + 1) % 10 === 0) {
      console.log(`[VU ${__VU}] Progress: ${connectionIndex + 1}/${shard.length} connections completed`);
    }
  });
  
  // Log completion for this VU
  console.log(`[VU ${__VU}] ✓ Completed all ${shard.length} connection(s)`);
}

// ============================================================================
// SETUP AND TEARDOWN
// ============================================================================
export function setup() {
  // Count total connections across all shards
  let totalConnections = 0;
  let totalRequestBatches = 0;
  let totalRequests = 0;
  
  for (let vu = 1; vu <= TEST_VUS; vu += 1) {
    const shard = shardConnectionsByVu[vu] || [];
    totalConnections += shard.length;
    
    shard.forEach((connection) => {
      totalRequestBatches += connection.request_batches.length;
      connection.request_batches.forEach((batch) => {
        totalRequests += batch.requests.length;
      });
    });
  }

  console.log('='.repeat(70));
  console.log('Production Replay - Exact Timeline Mode');
  console.log('='.repeat(70));
  console.log(`Shard Directory: ${SHARD_DIR}`);
  console.log(`Shard Pattern: ${SHARD_PREFIX}<VU>${SHARD_SUFFIX}`);
  console.log(`Virtual Users (VUs): ${TEST_VUS}`);
  console.log(`Total Connections: ${totalConnections}`);
  console.log(`Total Request Batches: ${totalRequestBatches}`);
  console.log(`Total Requests: ${totalRequests}`);
  console.log(`Connections per VU: ~${Math.ceil(totalConnections / TEST_VUS)}`);
  console.log(`Requests per VU: ~${Math.ceil(totalRequests / TEST_VUS)}`);
  console.log(`Debug mode: DEBUG_VU=${__ENV.DEBUG_VU || 'all'}`);
  console.log(`Log level: LOG_LEVEL=${__ENV.LOG_LEVEL || 'info'}`);
  
  // Show host mappings info
  if (Object.keys(HOST_MAPPINGS).length > 0) {
    console.log(`Host Mappings: ${Object.keys(HOST_MAPPINGS).length} hostname(s) configured`);
    // Show first few mappings as examples
    const hostKeys = Object.keys(HOST_MAPPINGS).slice(0, 3);
    hostKeys.forEach((host) => {
      console.log(`  ${host} → ${HOST_MAPPINGS[host]}`);
    });
    if (Object.keys(HOST_MAPPINGS).length > 3) {
      console.log(`  ... and ${Object.keys(HOST_MAPPINGS).length - 3} more`);
    }
  } else {
    console.log(`Host Mappings: None (using fallback: * → 198.18.134.240)`);
  }
  
  console.log('='.repeat(70));

  return {
    startTime: Date.now(),
    totalConnections: totalConnections,
    totalRequests: totalRequests,
  };
}

export function teardown(data) {
  console.log('='.repeat(70));
  console.log('Replay Execution Summary');
  console.log('='.repeat(70));
  console.log(`Total Connections: ${data.totalConnections || 'N/A'}`);
  console.log(`Total Requests: ${data.totalRequests || 'N/A'}`);
  console.log(`Validation Success Rate: ${(validationSuccessRate.value * 100).toFixed(2)}%`);
  console.log(`Bytes Transmitted: ${bytesThroughput.value} bytes`);
  console.log(`Avg Response Time: ${responseTime.value.toFixed(2)}ms`);
  console.log('='.repeat(70));
  console.log('✓ Test completed - failures collected in memory');
}

// ============================================================================
// CUSTOM SUMMARY
// ============================================================================
export function handleSummary(data) {
  // Extract metrics from data
  // Use http_reqs for total requests (actual HTTP requests made)
  const totalRequests = data.metrics.http_reqs ? data.metrics.http_reqs.values.count : 0;
  const successRateValue = data.metrics.validation_success_rate ? data.metrics.validation_success_rate.values.rate : 0;
  const failRate = 1 - successRateValue;
  const failedRequests = Math.round(totalRequests * failRate);

  const cpcodeFailCount = data.metrics.cpcode_failures ? data.metrics.cpcode_failures.values.count : 0;
  const statusFailCount = data.metrics.status_failures ? data.metrics.status_failures.values.count : 0;
  const bytesFailCount = data.metrics.bytes_failures ? data.metrics.bytes_failures.values.count : 0;

  console.log('='.repeat(70));
  console.log('Production Replay Summary');
  console.log('='.repeat(70));
  console.log(`Total Requests: ${totalRequests}`);
  console.log(`Failed Requests: ${failedRequests}`);
  console.log(`Success Rate: ${(successRateValue * 100).toFixed(2)}%`);
  console.log(`Fail Rate: ${(failRate * 100).toFixed(2)}%`);
  console.log('='.repeat(70));

  // Breakdown by failure type
  if (failedRequests > 0) {
    console.log('Failure Breakdown:');
    console.log(`  CPCode mismatches: ${cpcodeFailCount}`);
    console.log(`  Status mismatches: ${statusFailCount}`);
    console.log(`  Bytes mismatches: ${bytesFailCount}`);
    console.log('='.repeat(70));
    console.log('📝 Note: Failures are logged to console.');
    console.log('   To extract to files, run:');
    console.log(`   k6 run ... 2>&1 | tee k6_output.log`);
    console.log(`   python3 parse_k6_failures.py k6_output.log`);
  }

  const passed = failRate <= ACCEPTABLE_FAIL_RATE;
  const exitCode = passed ? 0 : 1;

  if (passed) {
    console.log(`✓ PASSED: Fail rate ${(failRate * 100).toFixed(2)}% is within acceptable threshold ${(ACCEPTABLE_FAIL_RATE * 100).toFixed(0)}%`);
  } else {
    console.error(`✗ FAILED: Fail rate ${(failRate * 100).toFixed(2)}% exceeds acceptable threshold ${(ACCEPTABLE_FAIL_RATE * 100).toFixed(0)}%`);
  }

  const summary = {
    mode: REPLAY_MODE,
    latencyMs: data.metrics.http_req_duration
      ? Math.round(data.metrics.http_req_duration.values['p(95)'] || 0)
      : 0,
    cacheHitRate: null,
    bandwidthSavedBytes: null,
    total_requests: totalRequests,
    failed_requests: failedRequests,
    success_rate: (successRateValue * 100).toFixed(2),
    fail_rate: (failRate * 100).toFixed(2),
    acceptable_fail_rate: (ACCEPTABLE_FAIL_RATE * 100).toFixed(2),
    passed: passed,
    exit_code: exitCode,
    validations: {
      bytes_tolerance_percent: (BYTES_TOLERANCE * 100).toFixed(0) + '%',
      bytes_tolerance_absolute: BYTES_ABSOLUTE_TOLERANCE + ' bytes',
    },
    failure_breakdown: {
      cpcode_mismatches: cpcodeFailCount,
      status_mismatches: statusFailCount,
      bytes_mismatches: bytesFailCount,
    },
    timestamp: new Date().toISOString(),
  };

  console.log('');
  console.log('SUMMARY_JSON_START');
  console.log(JSON.stringify(summary, null, 2));
  console.log('SUMMARY_JSON_END');
  console.log('');
  console.log(`EXIT_CODE:${exitCode}`);

  return {
    [`output/replay_metrics_${REPLAY_MODE}.json`]: JSON.stringify(summary),
    'stdout': textSummary(data, { indent: ' ', enableColors: true }),
  };
}
