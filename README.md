# Karbot Rage!

A sophisticated automated trading system for prediction markets.

## Project Structure

```
karbotrage_v1/
├── config/                 # Configuration files
│   ├── config.example.yaml # Example configuration
│   └── config.yaml         # Actual configuration (not in repo)
├── core/                   # Core system components
│   └── config.py          # Configuration system
├── agents/                 # Trading agents
├── compliance/             # Compliance tools
├── data/                   # Data handling
├── docs/                   # Documentation
├── execution/              # Execution engine
├── intelligence/           # Intelligence modules
├── monitoring/             # Monitoring and alerting
├── scripts/                # Utility scripts
└── tests/                  # Test suite
```

## Setup

1. Copy `.env.example` to `.env` and fill in your API keys
2. Copy `config/config.example.yaml` to `config/config.yaml` and configure as needed
3. Install dependencies: `pip install -e .`

## Configuration

The system uses a hierarchical configuration approach:
- All operational parameters are configurable via `config/config.yaml`
- Secrets are never stored in config files — they must be provided via environment variables
- Hard limits are constants in code and cannot be configured away for safety
- Config is validated on startup with clear error messages
- Config changes are logged for audit trail

## Security

- All secrets are loaded from environment variables only
- Configuration files are never committed to version control
- Hard limits in code prevent misconfiguration that could lead to losses