# Ascend Documentation

This directory contains detailed architecture and design documents for the Ascend project.

## Documents

### Architecture Documents

- **[AUTOMATIC_IMAGE_BUILDING.md](AUTOMATIC_IMAGE_BUILDING.md)** - Automatic container image building
  - Kaniko-based in-cluster image builds
  - GPU-aware base image selection (PyTorch/CUDA auto-detect, ACR caching)
  - SHA256-based dependency hashing for deterministic image tags
  - Multi-level caching including GPU base image caching from Docker Hub
  - Opt-in via environment variable or config file

- **[GPU_SUPPORT.md](GPU_SUPPORT.md)** - GPU and node type configuration architecture
  - Multiple node types: standard, memory-optimized, and GPU-enabled
  - Full Azure NC-family support: V100, A100, H100
  - Automatic PyTorch/CUDA base image detection from requirements
  - ACR-cached GPU base images for fast builds
  - `base_image` parameter for explicit override
  - Admin CLI for pre-caching GPU images
  - Usage examples and best practices

- **[INTEGRATION_TESTING.md](INTEGRATION_TESTING.md)** - Integration testing infrastructure

- **[LOGGING_AND_ARTIFACTS.md](LOGGING_AND_ARTIFACTS.md)** - Logging and artifact indexing architecture
  - Content-addressable naming scheme for jobs, logs, and artifacts
  - Structured logging format (JSON Lines) for human and machine readability
  - Blob storage organization with project-scoped isolation
  - CLI interface for searching and retrieving logs and artifacts
  - Admin tools for bulk operations and cost management

### Architecture Documents (continued)

- **[MULTI_CLOUD.md](MULTI_CLOUD.md)** - Multi-cloud backend architecture (implemented)
  - Optional extras for cloud backends (`pip install ascend[azure]`)
  - fsspec-based storage abstraction replacing direct Azure Blob SDK
  - Revised ABCs (`CloudStorage`, `ContainerRegistry`, `ImageBuilder`, `ComputeBackend`)
  - Backend auto-detection with import-time fail-fast guard
  - Step-by-step migration checklist and guide for adding new cloud backends

- **[IMAGE_CACHE_BUSTING.md](IMAGE_CACHE_BUSTING.md)** - Plan for busting Docker image caches during integration tests
  - Pytest fixture (`fresh_runtime_image`) for forcing clean image rebuilds
  - ACR tag deletion and Kaniko `--cache=false` for full cache invalidation
  - `--rebuild-images` CLI flag for conditional use in CI

- **[AUDIT_AND_ROADMAP.md](AUDIT_AND_ROADMAP.md)** - Comprehensive project audit with 30 prioritized findings
  - P0–P3 priority matrix covering runtime correctness, UX, code quality, and packaging
  - Remediation plans for each finding with specific code locations
  - Security observations and suggested implementation phases

- **[ADMIN_SETUP.md](ADMIN_SETUP.md)** - Administrator setup and infrastructure bootstrap
  - Azure RBAC role requirements for bootstrap
  - Makefile-based and manual CLI setup flows
  - User provisioning and namespace creation

### Examples

- **[EXAMPLES.md](EXAMPLES.md)** - Practical examples for real-world workloads
  - Hyperparameter tuning with Optuna and XGBoost on Kubernetes
  - General "local orchestrator + remote compute" pattern
  - Guidelines for writing your own distributed examples

### Main Documentation

Additional documentation can be found in the repository root:

- **[ARCHITECTURE.md](../ARCHITECTURE.md)** - Overall system architecture and design decisions
- **[README.md](../README.md)** - Getting started guide and quick reference

## Contributing to Documentation

When adding new architecture documents:

1. Create a new markdown file in this directory
2. Update this README with a link and brief description
3. Ensure the document includes:
   - Clear table of contents
   - Executive summary
   - Detailed technical specifications
   - Code examples where applicable
   - Implementation roadmap or timeline
4. Follow the existing documentation style and structure
