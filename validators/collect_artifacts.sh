#!/usr/bin/env bash
# validators/collect_artifacts.sh - Collect build/test artifacts
#
# Usage: collect_artifacts.sh <JOB_ID> <PROFILE> <WORKSPACE> <LOG_DIR>
# Stdout (last line): path to artifacts directory
# Exit 0 always (non-fatal)

JOB_ID="${1:?JOB_ID required}"
PROFILE="${2:-ros2_nav}"
WORKSPACE="${3:-.}"
LOG_DIR="${4:-/tmp}"

ARTIFACTS_DIR="${LOG_DIR}/artifacts_${JOB_ID}"
LOG_FILE="${LOG_DIR}/${JOB_ID}_artifacts.log"

log() { echo "[collect_artifacts.sh] $*" | tee -a "$LOG_FILE"; }

mkdir -p "$ARTIFACTS_DIR" "$LOG_DIR"
: > "$LOG_FILE"

log "=== Artifact Collection ==="
log "Job      : $JOB_ID"
log "Workspace: $WORKSPACE"
log "Output   : $ARTIFACTS_DIR"
log "Started  : $(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [ ! -d "$WORKSPACE" ]; then
    log "WARN: Workspace not found: $WORKSPACE"
    echo "$ARTIFACTS_DIR"
    exit 0
fi

cd "$WORKSPACE" || { log "WARN: Cannot cd to $WORKSPACE"; echo "$ARTIFACTS_DIR"; exit 0; }

COLLECTED=0

# colcon log directory
if [ -d "log" ]; then
    cp -r log "$ARTIFACTS_DIR/colcon_log" 2>/dev/null && log "Copied: log/" && COLLECTED=$((COLLECTED+1)) || true
fi

# Test result XML files
if [ -d "build" ]; then
    XMLS=$(find build -name "*.xml" -path "*test*" 2>/dev/null | head -20)
    if [ -n "$XMLS" ]; then
        mkdir -p "$ARTIFACTS_DIR/test_results"
        echo "$XMLS" | xargs -I {} cp {} "$ARTIFACTS_DIR/test_results/" 2>/dev/null || true
        XML_COUNT=$(echo "$XMLS" | wc -l)
        log "Copied $XML_COUNT test XML(s)"
        COLLECTED=$((COLLECTED+XML_COUNT))
    fi
fi

# Sim smoke result JSON (produced by sim_smoke.sh)
SIM_JSON="${LOG_DIR}/${JOB_ID}_sim_result.json"
if [ -f "$SIM_JSON" ]; then
    cp "$SIM_JSON" "$ARTIFACTS_DIR/" 2>/dev/null && log "Copied: sim_result.json" || true
fi

# Build/test result JSONs
for suffix in build_result test_result; do
    RFILE="${LOG_DIR}/${JOB_ID}_${suffix}.json"
    [ -f "$RFILE" ] && cp "$RFILE" "$ARTIFACTS_DIR/" 2>/dev/null || true
done

# Write summary
cat > "$ARTIFACTS_DIR/summary.json" <<EOF
{
  "job_id": "$JOB_ID",
  "workspace": "$WORKSPACE",
  "collected_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "artifact_count": $COLLECTED,
  "artifacts_dir": "$ARTIFACTS_DIR"
}
EOF

log "Done. $COLLECTED artifact(s) collected."
log "Finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Last line MUST be the artifacts directory path (parsed by orchestrator)
echo "$ARTIFACTS_DIR"
exit 0
