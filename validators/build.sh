#!/usr/bin/env bash
# validators/build.sh - colcon build validator
#
# Usage: build.sh <JOB_ID> <PROFILE> <WORKSPACE> <LOG_DIR>
# Exit 0: build passed (or workspace not applicable)
# Exit 1: build failed
# Writes: <LOG_DIR>/<JOB_ID>_build_result.json

JOB_ID="${1:?JOB_ID required}"
PROFILE="${2:-ros2_nav}"
WORKSPACE="${3:-.}"
LOG_DIR="${4:-/tmp}"

LOG_FILE="${LOG_DIR}/${JOB_ID}_build.log"
RESULT_FILE="${LOG_DIR}/${JOB_ID}_build_result.json"

STATUS="PASS"
MESSAGE=""
EXIT_CODE=0

log() { echo "[build.sh] $*" | tee -a "$LOG_FILE"; }

write_result() {
    cat > "$RESULT_FILE" <<EOF
{
  "job_id": "$JOB_ID",
  "validator": "build",
  "status": "$STATUS",
  "message": "$MESSAGE",
  "log": "$LOG_FILE"
}
EOF
}

mkdir -p "$LOG_DIR"
: > "$LOG_FILE"

log "=== Build Validator ==="
log "Job      : $JOB_ID"
log "Profile  : $PROFILE"
log "Workspace: $WORKSPACE"
log "Started  : $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# --- workspace check ---
if [ ! -d "$WORKSPACE" ]; then
    STATUS="FAIL"
    MESSAGE="Workspace directory not found: $WORKSPACE"
    log "ERROR: $MESSAGE"
    write_result
    exit 1
fi

cd "$WORKSPACE" || { STATUS="FAIL"; MESSAGE="Cannot cd to $WORKSPACE"; write_result; exit 1; }

# --- detect workspace type ---
HAS_SRC=0
HAS_PACKAGE_XML=0
[ -d "src" ] && HAS_SRC=1
[ -f "package.xml" ] && HAS_PACKAGE_XML=1

if [ "$HAS_SRC" -eq 0 ] && [ "$HAS_PACKAGE_XML" -eq 0 ]; then
    STATUS="SKIP"
    MESSAGE="Not a ROS2 workspace (no src/ and no package.xml). Build skipped."
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
    MESSAGE="ROS2 not found in /opt/ros/. Install ROS2 first."
    log "ERROR: $MESSAGE"
    write_result
    exit 1
fi

# --- colcon build ---
log "Running: colcon build --continue-on-error"
colcon build --continue-on-error >> "$LOG_FILE" 2>&1
BUILD_EXIT=$?

if [ $BUILD_EXIT -eq 0 ]; then
    STATUS="PASS"
    MESSAGE="colcon build succeeded"
    log "SUCCESS: $MESSAGE"
else
    STATUS="FAIL"
    MESSAGE="colcon build failed (exit $BUILD_EXIT)"
    log "FAIL: $MESSAGE"
    EXIT_CODE=1
fi

log "Finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
write_result
exit $EXIT_CODE
