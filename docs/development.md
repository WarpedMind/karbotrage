# Development Guide

## Setting Up Development Environment

### Prerequisites
- Python 3.8 or higher
- pip (Python package installer)
- Git

### Installation Steps

1. **Clone the repository**:
   ```bash
   git clone https://github.com/WarpedMind/karbotrage_v1.git
   cd karbotrage_v1
   ```

2. **Create virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -e .
   ```

4. **Set up environment variables**:
   ```bash
   cp .env.example .env
   # Edit .env to add your API keys and secrets
   ```

5. **Configure system**:
   ```bash
   cp config/config.example.yaml config/config.yaml
   # Edit config/config.yaml to set your trading parameters
   ```

## Project Structure

```
karbotrage_v1/
├── config/                 # Configuration files
│   ├── config.example.yaml # Example configuration
│   └── config.yaml         # Actual configuration (not in repo)
├── core/                   # Core system components
│   ├── __init__.py         # Package initialization
│   └── config.py          # Configuration system
├── agents/                 # Trading agents
│   ├── __init__.py         # Package initialization
│   ├── floor/              # Floor trading agents
│   │   ├── __init__.py
│   │   ├── arb_scanner.py  # Arbitrage scanner
│   │   ├── price_watcher.py # Price watcher
│   │   └── risk_gate.py    # Risk gatekeeper
│   ├── management/         # Management agents
│   │   ├── __init__.py
│   │   └── reflection.py   # Reflection agent
│   └── research/           # Research agents
│       ├── __init__.py
│       └── market_analyst.py # Market analyst
├── compliance/             # Compliance tools
│   ├── __init__.py
│   └── officer.py         # Compliance officer
├── data/                   # Data handling
│   ├── __init__.py
│   └── handler.py         # Data handler
├── docs/                   # Documentation
│   ├── architecture.md    # System architecture
│   └── development.md     # Development guide
├── execution/              # Execution engine
│   ├── __init__.py
│   └── engine.py          # Execution engine
├── intelligence/           # Intelligence modules
│   ├── __init__.py
│   └── world_intelligence.py # World intelligence module
├── monitoring/             # Monitoring and alerting
│   ├── __init__.py
│   └── logger.py          # Logger
├── scripts/                # Utility scripts
│   ├── __init__.py
│   └── setup_env.py        # Environment setup script
├── tests/                  # Test suite
│   ├── __init__.py
│   ├── test_config.py      # Configuration tests
│   └── test_core_config.py # Core config tests
├── karbot/                 # Main package
│   ├── __init__.py
│   └── main.py            # Main entry point
├── .env.example            # Example environment variables
├── .gitignore              # Git ignore file
├── README.md               # This file
├── pyproject.toml          # Project configuration
├── requirements.txt        # Python dependencies
└── LICENSE                 # License file
```

## Testing

### Running Tests
```bash
# Run all tests
python -m pytest tests/

# Run tests with coverage
python -m pytest tests/ --cov=.

# Run specific test file
python -m pytest tests/test_config.py
```

### Test Structure
Tests are organized by module:
- `tests/test_config.py`: Configuration system tests
- `tests/test_core_config.py`: Core configuration tests

## Development Workflow

### Branching Strategy
- `main`: Stable production code
- `develop`: Development branch for new features
- Feature branches: For specific features or fixes

### Commit Guidelines
- Use clear, descriptive commit messages
- Follow the format: `type(scope): description`
- Examples:
  - `feat(config): add new configuration validation`
  - `fix(agents): correct risk gate calculation`
  - `docs(readme): update installation instructions`

## Code Style

### Python Style Guide
- Follow PEP 8 style guide
- Use 4-space indentation
- Maximum line length of 88 characters
- Use type hints where appropriate

### Documentation
- All public functions must have docstrings
- Module-level documentation for complex components
- Inline comments for non-obvious code sections

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for your changes
5. Run all tests to ensure nothing is broken
6. Submit a pull request

## Deployment

### Local Development
```bash
python karbot/main.py
```

### Production Deployment
The system can be containerized using Docker for consistent deployment across environments.

## Troubleshooting

### Common Issues
1. **Import Errors**: Make sure you're in the virtual environment and dependencies are installed
2. **Configuration Issues**: Check that config files are properly formatted and environment variables are set
3. **API Connection Issues**: Verify API keys and network connectivity

### Logging
The system logs all operations to help with debugging. Check the logs in the monitoring system for detailed information.