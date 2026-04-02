# Robot Orchestrator

AI-powered workflow orchestrator for ROS2 robot projects. Coordinates between Codex (planning/auditing), Claude Code (execution), and local validation scripts to automate robot development tasks.

## Overview

This system implements a structured workflow for robot project development:

1. **User Input**: Task description via Discord/OpenClaw or CLI
2. **Planning**: Codex analyzes the task and creates a structured plan
3. **Execution**: Claude Code implements the changes
4. **Validation**: Local scripts verify build, tests, and simulation
5. **Audit**: Codex reviews results and provides final assessment
6. **Retry**: Automatic rework if issues found (max 2 attempts)

## Architecture

### Core Components

- **Orchestrator**: State machine managing job lifecycle
- **Adapters**:
  - `codex_adapter`: Codex CLI for planning and auditing
  - `claude_adapter`: Claude Code CLI for implementation
  - `openclaw_adapter`: Discord integration (stub)
- **Storage**: SQLite database for job persistence
- **Validators**: Shell scripts for build/test/sim validation
- **Profiles**: Project-specific configurations

### Why This Architecture?

We chose **single local orchestrator + adapters** over **multiple OpenClaw instances** because:

- **Cost Efficiency**: No additional API calls beyond existing subscriptions
- **Local Control**: Full control over workflow and error handling
- **ROS2 Integration**: Direct access to build tools and simulation
- **Debugging**: Easier to debug and modify local Python code
- **Security**: No external API dependencies for core logic

## Installation

### Prerequisites

- Ubuntu 20.04+ or similar Linux
- Python 3.10+
- ROS2 Humble or Foxy installed
- Codex CLI (ChatGPT Plus subscription)
- Claude Code CLI (Claude Max subscription)
- OpenClaw (optional, for Discord integration)

### Setup

1. Clone or copy this repository:
   ```bash
   cd /path/to/your/workspace
   # Copy the robot_orchestrator directory here
   ```

2. Install Python dependencies (minimal):
   ```bash
   # No external dependencies required - uses only stdlib
   ```

3. Make validator scripts executable:
   ```bash
   chmod +x validators/*.sh
   ```

4. Configure environment (optional):
   ```bash
   export CODEX_CMD="codex"
   export CLAUDE_CMD="claude"
   ```

## Usage

### CLI Commands

```bash
# Submit a new job
python cli.py submit --task "Fix navigation stack lifecycle issues" --profile ros2_nav

# Run a job
python cli.py run --job-id <job-id>

# List jobs
python cli.py list [--status COMPLETED]

# Show job details
python cli.py show --job-id <job-id>

# Retry a failed job
python cli.py retry --job-id <job-id>
```

### Example Workflow

```bash
# 1. Submit task
python cli.py submit --task "Update navigation launch file to fix lifecycle order"

# 2. Run the job
python cli.py run --job-id 12345678-1234-1234-1234-123456789abc

# 3. Check status
python cli.py show --job-id 12345678-1234-1234-1234-123456789abc
```

## Profiles

### ros2_nav
For autonomous navigation robots using ROS2 Nav2 stack.

- Build: `colcon build`
- Test: `colcon test`
- Simulation: Gazebo with navigation stack

### manipulator
For robotic arm manipulation using MoveIt2.

- Build: `colcon build`
- Test: `colcon test`
- Simulation: Gazebo with manipulator

## Cost Optimization Strategy

To minimize API usage and costs:

### Small Tasks (< 30 min)
- Direct Claude Code execution + final Codex audit

### Medium Tasks (30 min - 2 hours)
- Codex plan → Claude execution → validation → Codex audit

### Large Tasks (> 2 hours)
- LLM handles only planning/auditing
- Local scripts perform long-running operations
- Simulation runs locally without API calls

### Optimizations
- Structured JSON output reduces token usage
- Template-based prompts minimize redundancy
- Profile-specific configurations reduce context
- Early validation catches issues before expensive rework

## ROS2/Simulation Considerations

### Build Integration
- Uses `colcon build` for compilation
- Supports `--packages-select` for targeted builds
- Handles build failures gracefully

### Testing
- Runs `colcon test` for unit/integration tests
- Parses test results automatically
- Continues on test failures for partial validation

### Simulation Smoke Tests
- Safe MVP implementation for environments without simulators
- Checks ROS2 workspace structure
- Performs launch file dry-run validation
- Timeout protection (2 minutes default)
- Logs results for audit review

### Future Extensions
- Gazebo headless simulation
- RViz configuration validation
- Parameter sweep automation
- rosbag replay testing

## Configuration

### Environment Variables
- `CODEX_CMD`: Path to codex CLI (default: "codex")
- `CLAUDE_CMD`: Path to claude CLI (default: "claude")
- `CODEX_MODEL`: Model for Codex (default: "o4-mini")

### Profiles
Customize `profiles/*.yaml` for different robot types.

### Templates
- `templates/CLAUDE.md`: Project guidelines for Claude
- `templates/codex_config.toml`: Codex CLI configuration

## Job States

- `RECEIVED`: Job submitted
- `PLANNING`: Codex analyzing task
- `EXECUTING`: Claude implementing changes
- `VALIDATING`: Running build/test/sim validation
- `AUDITING`: Codex reviewing results
- `REWORK_REQUESTED`: Issues found, retrying
- `COMPLETED`: Successfully finished
- `FAILED`: Failed permanently

## Error Handling

- **Timeout Protection**: All operations have configurable timeouts
- **Retry Logic**: Max 2 attempts for rework scenarios
- **Graceful Degradation**: Continues with partial results when possible
- **Logging**: Comprehensive logs in `state/logs/`
- **JSON Safety**: Validates all structured inputs

## OpenClaw Integration

Currently implemented as stub. To enable Discord integration:

1. Install OpenClaw
2. Configure webhook URLs in `openclaw_adapter.py`
3. Implement actual Discord API calls
4. Set up event handling for task reception

The orchestrator works independently of Discord for testing.

## Development

### Adding New Profiles
1. Create `profiles/new_profile.yaml`
2. Define build/test/sim commands
3. Update validator scripts if needed

### Extending Validators
- Add new shell scripts in `validators/`
- Update `orchestrator.py` to call them
- Ensure they return proper exit codes

### Testing
```bash
# Run basic CLI test
python cli.py list

# Test job creation
python cli.py submit --task "Test task"
```

## Troubleshooting

### Common Issues

**Codex/Claude CLI not found**
- Ensure CLIs are installed and in PATH
- Check subscription status

**ROS2 not found**
- Source ROS2 setup in validator scripts
- Install ROS2 if missing

**Build/Test failures**
- Check workspace structure
- Verify dependencies
- Review logs in `state/logs/`

**Simulation timeouts**
- Increase timeout in config
- Check for hanging processes
- Use dry-run mode for testing

### Logs
- Application logs: `orchestrator.log`
- Job-specific logs: `state/logs/job_<id>_*.log`
- Validation logs: `/tmp/*_<job_id>.log`

## Future Enhancements

- [ ] Full OpenClaw Discord integration
- [ ] Web UI for job monitoring
- [ ] Parallel job execution
- [ ] Advanced simulation scenarios
- [ ] Parameter optimization workflows
- [ ] Multi-robot coordination support
- [ ] Integration with CI/CD pipelines

## License

This project is part of a robot development toolkit. See individual components for licensing.</content>
<parameter name="filePath">/home/robros0/Desktop/tools/robot_orchestrator/README.md