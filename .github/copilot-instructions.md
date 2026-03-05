# Copilot Instructions for Ascend

## Project Overview
This library provides an `@ascend` decorator which runs user Python functions on Kubernetes.

## Workflows and automation
Refer to the Makefile for common workflows:
- `make setup`: Create a `.venv` virtual environment and install all dev dependencies via `uv`
- `make install-dev`: Install development dependencies
- `make test-unit`: Run unit tests
and run `make help` to see all available commands.
- `make test-integration`: Run integration tests that require Azure credentials and real AKS cluster access

NB: After every change that touches the backend or runtime, ensure to add or update integration tests in `tests/integration/` and run them with `make test-integration` as appropriate.

**Important**: All Make targets use `uv run` to execute inside the `.venv` automatically — no manual activation is needed. If running commands outside Make, use `uv run <cmd>`.

Continuous Integration (CI) is set up using GitHub Actions, with workflows defined in `.github/workflows/`.

## Architecture
- **Serverless architecture**: Direct Azure API interaction, no control plane server
- **Azure SDK integration**: Uses `azure-mgmt-*` packages for infrastructure operations
- **Kubernetes Python client**: For job creation and management
- **Cloud provider abstraction**: Code cleanly separates Kubernetes operations from Azure-specific functionality to enable future multi-cloud support

## Engineering conventions
- Avoid global variables
- Avoid mutable state as far as possible, rely on functional style (passing arguments) and immutable data structures as far as possible.
- Rely on type annotations, dataclasses with informative names and parameters to structure the application
- As soon as code is replicated more than once, extract it into the library and reuse it
- Do /not/ mock test assets. For expensive testing infra, create reusable fixtures and mark the tests as 'slow'/'integration'.
- DO NOT skip tests if they fail. A test failure is a signal that something is wrong and needs to be fixed. If an integration test fails due to an issue with the environment, investigate and resolve the underlying issue rather than skipping the test. If all reasonable fixing attempts fail, report to the user and instruct them to fix their environment, but do not skip the test.

## Code Style and Conventions
### Python Style
- Follow PEP 8 style guidelines
- Use type hints for function parameters and return values

### Documentation
- Use docstrings for all public functions and classes, include example usage in docstrings for user-facing APIs, document parameters with Args section
- the overall architecture document is ARCHITECTURE.md , and specific features are documented in the docs/ folder.
- Always ensure that documentation is up to date, concise and consistent.
- Only create new documents when significant new features are implemented, otherwise edit the relevant ones intelligently. When adding new architecture documents:
1. Create a new markdown file in the docs/ directory
2. Update the docs/README with a link and brief description
3. Ensure the document includes:
   - Clear table of contents
   - Executive summary
   - Detailed technical specifications
   - Code examples where applicable
   - Implementation roadmap
4. Follow the existing documentation style and structure

### Naming Conventions
- Use hash-based naming (e.g., `ascend{hash}acr`) for Azure resources to avoid naming conflicts and length limits
- User namespaces follow pattern: `ascend-users-{username}`
- 

## Package Structure

```
ascend/
├── __init__.py          # Public API exports
├── decorator.py         # Core @ascend decorator
├── config.py            # Configuration management
├── serialization.py     # Code and dependency serialization
├── cli/                 # Command-line interface (click-based)
├── cloud/               # Cloud provider abstraction
|   |-- kubernetes.py    # Kubernetes job management
│   └── azure/           # Azure-specific implementations
├── dependencies/        # Dependency detection and analysis
├── runtime/             # Execution orchestration
└── utils/               # Shared utilities
```

## Key Components

### Decorator
The core user-facing API in `ascend/decorator.py`. Decorates functions for remote execution with configurable CPU, memory, timeout, and requirements.


## Testing
- Tests are located in `tests/` directory
- Use `pytest` for testing, run tests with: `pytest`.
- Basic tests verify package imports, decorator application, and config initialization without requiring Azure credentials

## Building and Installing

```bash
# Install in development mode
pip install -e .

# Install with dev dependencies
pip install -e ".[dev]"
```

## Dependencies
See pyproject.toml

## Authentication
- Pods access Azure Blob Storage using managed identity (DefaultAzureCredential), not connection strings or secrets
- Users authenticate via Azure CLI credentials

## Important Guidelines

1. **Keep it simple**: Focus on the serverless architecture without a control plane
2. **Use Azure SDK**: All infrastructure operations must use Azure Python SDK
3. **Maintain abstractions**: Keep cloud provider abstraction clean for future multi-cloud support
4. **Serialization**: Use `cloudpickle` for function and closure serialization
5. **Error handling**: Provide clear, actionable error messages to users
