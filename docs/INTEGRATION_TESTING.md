# Integration Testing

Integration tests run against real Azure/AKS infrastructure. There is no simulated or emulated infrastructure.

## Table of Contents
- [Authentication](#authentication)
- [Required Environment Variables](#required-environment-variables)
- [Running Tests](#running-tests)
- [Test Structure](#test-structure)
- [CI Pipeline](#ci-pipeline)

## Authentication

All authentication uses `DefaultAzureCredential` from the Azure Identity SDK. This automatically resolves credentials based on the runtime environment:

| Environment | Credential Source |
|---|---|
| **CI (GitHub Actions)** | `EnvironmentCredential` — picks up `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET` env vars set by the workflow |
| **Local development** | `AzureCliCredential` — uses credentials from `az login` |
| **AKS pods (runner)** | `ManagedIdentityCredential` — uses workload identity attached to the pod's service account |

No credential type is ever selected explicitly in code. `DefaultAzureCredential` handles the resolution chain automatically.

## Required Environment Variables

Integration tests require these environment variables:

| Variable | Description |
|---|---|
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |
| `AZURE_RESOURCE_GROUP` | Resource group containing the AKS cluster |
| `AZURE_AKS_CLUSTER_NAME` | Name of the AKS cluster |

For CI, additional variables are needed for `DefaultAzureCredential` (set by the `azure/login` GitHub Action):

| Variable | Description |
|---|---|
| `AZURE_CLIENT_ID` | Service principal client ID |
| `AZURE_TENANT_ID` | Azure AD tenant ID |
| `AZURE_CLIENT_SECRET` | Service principal client secret |
| `AZURE_STORAGE_ACCOUNT` | Azure Storage account name |
| `AZURE_CONTAINER_REGISTRY` | Azure Container Registry name |

## Running Tests

### Unit tests (no infrastructure required)

```bash
make test-unit
```

### Integration tests (requires real AKS)

**Option A – `.env` file (recommended for local development)**

```bash
# One-time setup: copy the template and fill in your values
cp .env.example .env
# Edit .env with your Azure subscription, resource group, cluster, etc.

# Run (credentials are loaded automatically by the test suite):
make test-integration
```

**Option B – Azure CLI fallback**

```bash
az login

export AZURE_SUBSCRIPTION_ID=...
export AZURE_RESOURCE_GROUP=...
export AZURE_AKS_CLUSTER_NAME=...

make test-integration
```

> **Kubeconfig**: You do **not** need to run `az aks get-credentials` manually.
> The test fixtures fetch AKS credentials programmatically via the Azure SDK
> and write a temporary kubeconfig automatically.

> **Note:** The test suite always tries to load a `.env` file first (via
> `python-dotenv`). If the file is missing or incomplete, it falls back to
> ambient credentials (`az login`, environment variables) and emits a warning.
> Existing environment variables are **never** overwritten by `.env` values,
> so CI-injected secrets always take precedence.

### All tests

```bash
make test
```

## Test Structure

All integration tests are marked with `@pytest.mark.integration` and live in `tests/integration/`.

| File | Purpose |
|---|---|
| `conftest.py` | `real_aks_cluster` fixture — validates AKS cluster access via `DefaultAzureCredential` |
| `test_e2e.py` | End-to-end job lifecycle: submit function, wait for result, verify output |
| `test_storage_integration.py` | Blob storage upload/download through real Azure Storage |

### Fixtures

- **`real_aks_cluster`** (session-scoped): Verifies the AKS cluster is accessible using `DefaultAzureCredential` and `ContainerServiceClient`. Skips all tests if required env vars are missing or cluster is unreachable.
- **`ensure_kubeconfig`** (session-scoped): Fetches AKS user credentials via the Azure SDK and writes a temporary kubeconfig file. Sets `KUBECONFIG` env var so `kubernetes.config.load_kube_config()` works without a manual `az aks get-credentials` step. Fails the session if credentials cannot be obtained.
- **`debug_mode`**: Enables verbose logging when `--integration-debug` is passed.
- **`artifact_collector`**: Collects logs and manifests from failed tests for debugging.

## CI Pipeline

The GitHub Actions workflow (`.github/workflows/integration-tests.yml`) has two jobs:

1. **`unit-tests`**: Runs on every PR and push to `main`. No Azure credentials needed.
2. **`integration-tests-aks`**: Runs against real AKS using the `azure-test` environment. The `azure/login@v2` action authenticates a service principal. Kubeconfig is fetched automatically by the `ensure_kubeconfig` test fixture via the Azure SDK (the CI workflow's `az aks get-credentials` step is kept as a belt-and-suspenders fallback). `DefaultAzureCredential` in test code picks up the `AZURE_CLIENT_*` env vars automatically.
