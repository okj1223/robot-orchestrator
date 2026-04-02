#!/usr/bin/env bash
# validators/test.sh - colcon test validator
#
# Usage: test.sh <JOB_ID> <PROFILE> <WORKSPACE> <LOG_DIR>
# Exit 0: tests passed (or skipped)
# Exit 1: tests failed
# Writes: <LOG_DIR>/<JOB_ID>_test_result.json

JOB_ID="${1:?JOB_ID required}"
PROFILE="${2:-ros2_nav}"
WORKSPACE="${3:-.}"
LOG_DIR="${4:-/tmp}"

LOG_FILE="${LOG_DIR}/${JOB_ID}_test.log"
RESULT_FILE="${LOG_DIR}/${JOB_ID}_test_result.json"

STATUS="PASS"
MESSAGE=""
EXIT_CODE=0

log() { echo "[test.sh] $*" | tee -a "$LOG_FILE"; }

write_result() {
    cat > "$RESULT_FILE" <<EOF
{
  "job_id": "$JOB_ID",
  "validator": "test",
  "status": "$STATUS",
  "message": "$MESSAGE",
  "log": "$LOG_FILE"
}
EOF
}

mkdir -p "$LOG_DIR"
: > "$LOG_FILE"

log "=== Test Validator ==="
log "Job      : $JOB_ID"
log "Profile  : $PROFILE"
log "Workspace: $WORKSPACE"
log "Started  : $(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [ ! -d "$WORKSPACE" ]; then
    STATUS="FAIL"
    MESSAGE="Workspace not found: $WORKSPACE"
    log "ERROR: $MESSAGE"
    write_result
    exit 1
fi

cd "$WORKSPACE" || { STATUS="FAIL"; MESSAGE="Cannot cd to $WORKSPACE"; write_result; exit 1; }

# --- detect workspace type ---
if [ ! -d "src" ] && [ ! -f "package.xml" ]; then
    STATUS="SKIP"
    MESSAGE="Not a ROS2 workspace. Tests skipped."
    log "INFO: $MESSAGE"
    write_result
    exit 0
fi

# --- check if build artifacts exist (need to build before testing) ---
if [ ! -d "build" ] && [ ! -d "install" ]; then
    STATUS="SKIP"
    MESSAGE="No build artifacts found. Run build first. Tests skipped."
    log "INFO: $MESSAGE"
    write_result
    exit 0
fi

# --- source ROS2 ---
ROS_SOURCED=0
for distro in humble foxy iron rolling jazzy; do
    if [ -f "/opt/ros/${distro}/setup.bash" ]; then
        # shellcheck disable=SC1090
        source "/opt/ros/${distro}/setup.bash"
        ROS_SOURCED=1
        log "Sourced ROS2 $distro"
        break
    fi
done

if [ "$ROS_SOURCED" -eq 0 ]; then
    STATUS="FAIL"
    MESSAGE="ROS2 not found in /opt/ros/."
    log "ERROR: $MESSAGE"
    write_result
    exit 1
fi

# Source workspace overlay if available
if [ -f "install/setup.bash" ]; then
    # shellcheck disable=SC1091
    source install/setup.bash
    log "Sourced workspace overlay"
fi

# --- colcon test ---
log "Running: colcon test --continue-on-error"
colcon test --continue-on-error >> "$LOG_FILE" 2>&1
TEST_EXIT=$?

log "Running: colcon test-result --verbose"
colcon test-result --verbose >> "$LOG_FILE" 2>&1
RESULT_EXIT=$?

if [ $TEST_EXIT -eq 0 ] && [ $RESULT_EXIT -eq 0 ]; then
    STATUS="PASS"
    MESSAGE="All tests passed"
    log "SUCCESS: $MESSAGE"
else
    STATUS="FAIL"
    MESSAGE="Tests failed (colcon test=$TEST_EXIT, test-result=$RESULT_EXIT)"
    log "FAIL: $MESSAGE"
    EXIT_CODE=1
fi

log "Finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
write_result
exit $EXIT_CODE
