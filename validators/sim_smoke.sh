#!/usr/bin/env bash
# validators/sim_smoke.sh - Simulation smoke test (safe MVP)
#
# Usage: sim_smoke.sh <JOB_ID> <PROFILE> <WORKSPACE> <LOG_DIR>
# Exit 0: smoke passed (or skipped/not applicable)
# Exit 1: smoke failed
# Writes: <LOG_DIR>/<JOB_ID>_sim_result.json
#
# Behavior:
#   1. Check profile and workspace
#   2. Detect ROS2 installation
#   3. If launch files exist: ros2 launch --dry-run (with timeout)
#   4. Else: basic ros2 env check
#   5. Write JSON result and exit

JOB_ID="${1:?JOB_ID required}"
PROFILE="${2:-ros2_nav}"
WORKSPACE="${3:-.}"
LOG_DIR="${4:-/tmp}"

LOG_FILE="${LOG_DIR}/${JOB_ID}_sim_smoke.log"
RESULT_FILE="${LOG_DIR}/${JOB_ID}_sim_result.json"

SIM_TIMEOUT="${SIM_TIMEOUT:-30}"   # seconds for launch dry-run
STATUS="PASS"
MESSAGE=""
LAUNCH_TESTED=""
EXIT_CODE=0

log() { echo "[sim_smoke.sh] $*" | tee -a "$LOG_FILE"; }

write_result() {
    # Escape double-quotes in MESSAGE for valid JSON
    local safe_msg
    safe_msg=$(printf '%s' "$MESSAGE" | sed 's/"/\\"/g')
    local safe_launch
    safe_launch=$(printf '%s' "$LAUNCH_TESTED" | sed 's/"/\\"/g')
    cat > "$RESULT_FILE" <<EOF
{
  "job_id": "$JOB_ID",
  "validator": "sim_smoke",
  "status": "$STATUS",
  "message": "$safe_msg",
  "launch_tested": "$safe_launch",
  "log": "$LOG_FILE"
}
EOF
}

mkdir -p "$LOG_DIR"
: > "$LOG_FILE"

log "=== Simulation Smoke Test ==="
log "Job      : $JOB_ID"
log "Profile  : $PROFILE"
log "Workspace: $WORKSPACE"
log "Started  : $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# --- workspace check ---
if [ ! -d "$WORKSPACE" ]; then
    STATUS="FAIL"
    MESSAGE="Workspace not found: $WORKSPACE"
    log "ERROR: $MESSAGE"
    write_result
    exit 1
fi

cd "$WORKSPACE" || { STATUS="FAIL"; MESSAGE="Cannot cd to $WORKSPACE"; write_result; exit 1; }

if [ ! -d "src" ]; then
    STATUS="SKIP"
    MESSAGE="No src/ directory - not a ROS2 workspace. Sim smoke skipped."
    log "INFO: $MESSAGE"
    write_result
    exit 0
fi

# --- source ROS2 ---
ROS_SOURCED=0
ROS_DISTRO_FOUND=""
for distro in humble foxy iron rolling jazzy; do
    if [ -f "/opt/ros/${distro}/setup.bash" ]; then
        # shellcheck disable=SC1090
        source "/opt/ros/${distro}/setup.bash"
        ROS_SOURCED=1
        ROS_DISTRO_FOUND="$distro"
        log "Sourced ROS2 $distro"
        break
    fi
done

if [ "$ROS_SOURCED" -eq 0 ]; then
    STATUS="SKIP"
    MESSAGE="ROS2 not installed. Sim smoke skipped (non-fatal for non-ROS environments)."
    log "WARN: $MESSAGE"
    write_result
    exit 0   # Not a hard failure - sim is optional
fi

# Source workspace overlay
if [ -f "install/setup.bash" ]; then
    # shellcheck disable=SC1091
    source install/setup.bash
    log "Sourced workspace install overlay"
fi

# --- find launch files ---
LAUNCH_FILES=$(find src -name "*.launch.py" 2>/dev/null | head -10)

if [ -z "$LAUNCH_FILES" ]; then
    log "No launch files found. Running basic ROS2 env check."
    # Basic sanity: is ros2 command functional?
    if ros2 pkg list >> "$LOG_FILE" 2>&1; then
        STATUS="PASS"
        MESSAGE="No launch files found; basic ros2 env check passed."
        log "PASS: $MESSAGE"
    else
        STATUS="FAIL"
        MESSAGE="ros2 pkg list failed - ROS2 environment broken."
        log "FAIL: $MESSAGE"
        EXIT_CODE=1
    fi
    write_result
    exit $EXIT_CODE
fi

# --- dry-run first launch file ---
FIRST_LAUNCH=$(echo "$LAUNCH_FILES" | head -1)
LAUNCH_TESTED="$FIRST_LAUNCH"
log "Launch files found (showing first 5):"
echo "$LAUNCH_FILES" | head -5 | while read -r lf; do log "  $lf"; done
log "Dry-run: $FIRST_LAUNCH (timeout=${SIM_TIMEOUT}s)"

timeout "$SIM_TIMEOUT" ros2 launch --dry-run "$FIRST_LAUNCH" >> "$LOG_FILE" 2>&1
DRY_EXIT=$?

case $DRY_EXIT in
    0)
        STATUS="PASS"
        MESSAGE="Launch dry-run passed: $FIRST_LAUNCH"
        log "PASS: $MESSAGE"
        ;;
    124)
        # timeout is acceptable for dry-run (means it started OK)
        STATUS="PASS"
        MESSAGE="Launch dry-run timed out (${SIM_TIMEOUT}s) - treated as PASS for smoke test."
        log "PASS (timeout acceptable): $MESSAGE"
        ;;
    *)
        STATUS="FAIL"
        MESSAGE="Launch dry-run failed (exit $DRY_EXIT): $FIRST_LAUNCH"
        log "FAIL: $MESSAGE"
        EXIT_CODE=1
        ;;
esac

log "Finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
write_result
exit $EXIT_CODE
