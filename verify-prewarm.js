import http from 'k6/http';
import { check } from 'k6';
import { SharedArray } from 'k6/data';
import { Counter, Rate } from 'k6/metrics';
import { textSummary } from 'https://jslib.k6.io/k6-summary/0.0.1/index.js';

// ============================================================================
// CONFIGURATION
// ============================================================================
const WAVE_FILE = __ENV.WAVE_FILE; // Required: Path to wave file (e.g., ./output/edge_wave1.jsonl)
const NUM_VUS = parseInt(__ENV.NUM_VUS || '1', 10); // Number of virtual users (default: 1)
const BYTES_TOLERANCE = parseFloat(__ENV.BYTES_TOLERANCE || '0.10'); // 10% tolerance
const BYTES_ABSOLUTE_TOLERANCE = parseInt(__ENV.BYTES_ABSOLUTE_TOLERANCE || '10', 10); // 10 bytes absolute tolerance
const ACCEPTABLE_FAIL_RATE = parseFloat(__ENV.ACCEPTABLE_FAIL_RATE || '0.05'); // 5%

if (!WAVE_FILE) {
  throw new Error('WAVE_FILE environment variable is required');
}

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

// ============================================================================
// COMPUTE WAVE TYPE (early, so we can use it for file operations)
// ============================================================================
const waveType = WAVE_FILE.split('/').pop().replace('.jsonl', '');

// ============================================================================
// FAILURE COLLECTION ARRAYS (global shared across all VUs)
// ============================================================================
// Note: k6 doesn't support file writing during test execution
// We collect failures in memory and write them in handleSummary()
let cpcodeFailureDetails = [];
let statusFailureDetails = [];
let bytesFailureDetails = [];

// ============================================================================
// CUSTOM METRICS
// ============================================================================
const validationSuccessRate = new Rate('validation_success_rate');
const cpcodeFailures = new Counter('cpcode_failures');
const statusFailures = new Counter('status_failures');
const bytesFailures = new Counter('bytes_failures');

// ============================================================================
// LOAD WAVE FILE
// ============================================================================
function parseJsonlWave(content, filePath) {
  const lines = content.split('\n');
  const requests = [];

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i].trim();
    if (!line) {
      continue;
    }

    try {
      const connection = JSON.parse(line);
      // Extract all requests from all batches
      connection.request_batches.forEach((batch) => {
        batch.requests.forEach((req) => {
          requests.push({
            request_id: req.request_id,
            url: req.r_record.canonical_url,
            method: req.r_record.method || 'GET',
            headers: req.headers || {},
            expected_cpcode: req.r_record.arl_cpcode,
            expected_status: req.r_record.status,
            expected_bytes: req.r_record.bytes_served,
            host: req.r_record.canonical_host,
            cache_path_class: req.r_record.cache_path_class || 'unknown',
          });
        });
      });
    } catch (error) {
      throw new Error(`Invalid JSONL at ${filePath}:${i + 1} - ${error.message}`);
    }
  }

  return requests;
}

const waveRequests = new SharedArray('wave_requests', function () {
  const content = open(WAVE_FILE);
  return parseJsonlWave(content, WAVE_FILE);
});

// ============================================================================
// K6 CONFIGURATION
// ============================================================================
function buildK6Options() {
  // Allow limiting iterations for testing
  const maxIterations = __ENV.MAX_ITERATIONS ? parseInt(__ENV.MAX_ITERATIONS, 10) : waveRequests.length;

  const opts = {
    scenarios: {
      distribute_requests: {
        executor: 'per-vu-iterations',
        vus: NUM_VUS,
        iterations: Math.ceil(maxIterations / NUM_VUS),
        maxDuration: '24h', // Allow long test runs
      },
    },
    insecureSkipTLSVerify: true,
    thresholds: {
      'validation_success_rate': [`rate>=${1 - ACCEPTABLE_FAIL_RATE}`],
    },
  };

  // Use HOST_MAPPINGS if provided
  if (Object.keys(HOST_MAPPINGS).length > 0) {
    opts.hosts = HOST_MAPPINGS;
    console.log(`Using custom host mappings: ${JSON.stringify(HOST_MAPPINGS)}`);
  }

  return opts;
}

export const options = buildK6Options();

// ============================================================================
// HELPER FUNCTIONS
// ============================================================================

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
  const actualCpcode = response.headers['X-Trident-Cpcode'] || response.headers['x-trident-cpcode'];
  const actualRequestId = response.headers['X-Trident-Request-ID'] || response.headers['x-trident-request-id'];
  if (actualCpcode === request.expected_cpcode) {
    validations.cpcode = true;
  } else {
    cpcodeFailures.add(1);
    failures.push({
      validation: 'cpcode',
      expected: request.expected_cpcode,
      actual: actualCpcode || 'missing',
    });
  }

  // 2. Validate status code
  if (response.status === request.expected_status) {
    validations.status = true;
  } else {
    statusFailures.add(1);
    failures.push({
      validation: 'status',
      expected: request.expected_status,
      actual: response.status,
    });
  }

  // 3. Validate bytes (with tolerance: 10% OR 10 bytes, whichever is more lenient)
  const actualBytes = response.body ? response.body.length : 0;
  const withinTolerance = isBytesWithinTolerance(request.expected_bytes, actualBytes);

  if (withinTolerance) {
    validations.bytes = true;
  } else {
    bytesFailures.add(1);
    const absoluteDiff = Math.abs(actualBytes - request.expected_bytes);
    const percentDiff = request.expected_bytes > 0
      ? (absoluteDiff / request.expected_bytes * 100).toFixed(2)
      : 'N/A';
    failures.push({
      validation: 'bytes',
      expected: request.expected_bytes,
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
    actualRequestId,
  };
}

// ============================================================================
// MAIN TEST EXECUTION
// ============================================================================
export default function (data) {
  // Get start time from setup data
  const startTime = data?.startTime || Date.now();
  
  // Calculate global request index based on VU and iteration
  const requestIndex = (__VU - 1) * Math.ceil(waveRequests.length / NUM_VUS) + __ITER;
  const request = waveRequests[requestIndex];

  if (!request) {
    console.warn(`[WARN] No request found for VU ${__VU} iteration ${__ITER} (global index ${requestIndex})`);
    return;
  }

  // Build headers
  const headers = {
    ...request.headers,
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
  };

  // Add If-Modified-Since header for 304 responses
  if (request.expected_status === 304) {
    const lastModifiedDate = new Date(startTime);
    headers['If-Modified-Since'] = lastModifiedDate.toUTCString();
  }

  // Add replay headers (for debugging/monitoring)
  headers['x-replay-expected-status'] = request.expected_status;
  headers['x-replay-expected-size'] = Math.max(request.expected_bytes,1) || 1;
  headers['x-replay-expected-cpcode'] = request.expected_cpcode || 'missing';
  
  // Add prewarm-specific headers
  headers['x-replay-step'] = 'prewarm';
  headers['x-replay-expected-path'] = request.cache_path_class || 'unknown';

  // Add random query parameter for testing (to avoid cache hits)
  const separator = request.url.includes('?') ? '&' : '?';
  const randomParam = `_test=${Date.now()}_${Math.random().toString(36).substring(7)}`;
  const testUrl = `${request.url}${separator}${randomParam}`;

  // Make request
  const params = {
    headers: headers,
    tags: {
      request_id: request.request_id,
      wave_type: WAVE_FILE.split('/').pop().replace('.jsonl', ''),
    },
    timeout: '30s',
  };

  const response = http.request(request.method, testUrl, null, params);

  // Validate response
  const result = validateResponse(request, response);

  // Track success rate
  validationSuccessRate.add(result.passed ? 1 : 0);

  // Log failures and collect in memory
  if (!result.passed) {
    const failureLog = {
      timestamp: new Date().toISOString(),
      vu: __VU,
      iteration: __ITER,
      global_index: requestIndex,
      request_id: request.request_id,
      url: request.url,
      method: request.method,
      host: request.host,
      failures: result.failures,
      expected: {
        cpcode: request.expected_cpcode,
        status: request.expected_status,
        bytes: request.expected_bytes,
      },
      actual: {
        actual_request_id: result.actualRequestId || 'missing',
        actual_cpcode: result.actualCpcode,
        status: response.status,
        bytes: result.actualBytes,
      },
    };
    console.warn(`FAILURE: ${JSON.stringify(failureLog)}`);

    // Collect failures by type in memory (per-VU instance)
    result.failures.forEach((failure) => {
      const failureDetail = {
        timestamp: new Date().toISOString(),
        vu: __VU,
        iteration: __ITER,
        global_index: requestIndex,
        request_id: request.request_id,
        url: request.url,
        method: request.method,
        host: request.host,
        expected: failure.validation === 'cpcode' ? request.expected_cpcode : 
                  failure.validation === 'status' ? request.expected_status :
                  failure.validation === 'bytes' ? request.expected_bytes : null,
        actual: failure.validation === 'cpcode' ? result.actualCpcode :
                failure.validation === 'status' ? response.status :
                failure.validation === 'bytes' ? result.actualBytes : null,
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

  // Log progress every 100 requests (across all VUs)
  if ((requestIndex + 1) % 100 === 0) {
    console.log(`Progress: ${requestIndex + 1}/${waveRequests.length} requests completed`);
  }
}

// ============================================================================
// SETUP AND TEARDOWN
// ============================================================================
export function setup() {
  // Extract wave type from filename (e.g., edge_wave1, peer_wave1)
  const waveType = WAVE_FILE.split('/').pop().replace('.jsonl', '');

  console.log('='.repeat(70));
  console.log(`Prewarm Verification Test - ${waveType}`);
  console.log('='.repeat(70));
  console.log(`Wave File: ${WAVE_FILE}`);
  console.log(`Total Requests: ${waveRequests.length}`);
  console.log(`Virtual Users (VUs): ${NUM_VUS}`);
  console.log(`Requests per VU: ${Math.ceil(waveRequests.length / NUM_VUS)}`);
  console.log(`Bytes Tolerance: ${(BYTES_TOLERANCE * 100).toFixed(0)}% OR ${BYTES_ABSOLUTE_TOLERANCE} bytes`);
  console.log(`Acceptable Fail Rate: ${(ACCEPTABLE_FAIL_RATE * 100).toFixed(0)}%`);
  console.log('='.repeat(70));

  return {
    startTime: Date.now(),
    waveType: waveType,
  };
}

export function teardown(data) {
  // No file handles to close (k6 doesn't support file writing during execution)
  console.log(`✓ Test completed - failures collected in memory`);
}

// ============================================================================
// CUSTOM SUMMARY
// ============================================================================
export function handleSummary(data) {
  const waveType = WAVE_FILE.split('/').pop().replace('.jsonl', '');

  // Extract metrics from data
  const totalRequests = data.metrics.iterations.values.count || 0;
  const successRateValue = data.metrics.validation_success_rate ? data.metrics.validation_success_rate.values.rate : 0;
  const failRate = 1 - successRateValue;
  const failedRequests = Math.round(totalRequests * failRate);

  const cpcodeFailCount = data.metrics.cpcode_failures ? data.metrics.cpcode_failures.values.count : 0;
  const statusFailCount = data.metrics.status_failures ? data.metrics.status_failures.values.count : 0;
  const bytesFailCount = data.metrics.bytes_failures ? data.metrics.bytes_failures.values.count : 0;

  console.log('='.repeat(70));
  console.log('Prewarm Verification Summary');
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
    wave_file: WAVE_FILE,
    wave_type: waveType,
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
    'stdout': textSummary(data, { indent: ' ', enableColors: true }),
  };
}
