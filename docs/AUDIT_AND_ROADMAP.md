# Ascend Project Audit & Roadmap

Comprehensive audit of the Ascend codebase with prioritized findings and remediation plans, optimizing for both **data scientist (DS)** and **infrastructure maintainer (OPS)** experiences.

**Date:** March 2026

---

## Table of Contents

- [Executive Summary](#executive-summary)
- [P0 — Broken or Misleading at Runtime](#p0--broken-or-misleading-at-runtime)
  - [1. `packaging` Not Declared but Imported at Runtime](#1-packaging-not-declared-but-imported-at-runtime)
  - [2. PLACEHOLDER Values Silently Written to Config](#2-placeholder-values-silently-written-to-config)
  - [3. Python Version Hardcoded to 3.11 in Multiple Places](#3-python-version-hardcoded-to-311-in-multiple-places)
  - [4. Remote Exceptions Not Faithfully Propagated](#4-remote-exceptions-not-faithfully-propagated)
- [P1 — UX Gaps That Hit Users Quickly](#p1--ux-gaps-that-hit-users-quickly)
  - [5. Dead Dependencies Bloat Install](#5-dead-dependencies-bloat-install)
  - [6. `serialization.py` Is Dead Code](#6-serializationpy-is-dead-code)
  - [7. `NodeType.STANDARD` Duplicates `STANDARD_MEDIUM`](#7-nodetypestandard-duplicates-standard_medium)
  - [8. ~~No Job Lifecycle CLI Commands~~ ✅](#8-no-job-lifecycle-cli-commands)
  - [9. ~~No Job Cancellation on Ctrl+C~~ ✅](#9-no-job-cancellation-on-ctrlc)
  - [10. ~~`wait_for_completion` Treats 404 Job as Success~~ ✅](#10-wait_for_completion-treats-404-job-as-success)
  - [11. ~~No Config Format Validation~~ ✅](#11-no-config-format-validation)
  - [12. ~~`NodePoolValidator` Exists but Is Never Called~~ ✅](#12-nodepoolvalidator-exists-but-is-never-called)
- [P2 — Code Quality & Ops Ergonomics](#p2--code-quality--ops-ergonomics)
  - [13. ACR Token in K8s Secret Expires and Never Rotates](#13-acr-token-in-k8s-secret-expires-and-never-rotates)
  - [14. No Teardown / Cleanup Commands](#14-no-teardown--cleanup-commands)
  - [15. Debug `print()` Statements in Production Code](#15-debug-print-statements-in-production-code)
  - [16. Silent Exception Swallowing Throughout](#16-silent-exception-swallowing-throughout)
  - [17. Config Loaded Twice per Execution](#17-config-loaded-twice-per-execution)
  - [18. `AscendConfig` Is a Plain Class, Not a Dataclass](#18-ascendconfig-is-a-plain-class-not-a-dataclass)
  - [19. `backoff_limit=4` Hardcoded, Not Configurable](#19-backoff_limit4-hardcoded-not-configurable)
  - [20. Result Download Has Hardcoded 60s Timeout](#20-result-download-has-hardcoded-60s-timeout)
- [P3 — Polish, Docs, Packaging](#p3--polish-docs-packaging)
  - [21. Global Mutable Singletons](#21-global-mutable-singletons)
  - [22. `image_pull_policy="Always"` Hardcoded](#22-image_pull_policyalways-hardcoded)
  - [23. Git Validation Runs on Every Function Call](#23-git-validation-runs-on-every-function-call)
  - [24. `ARCHITECTURE.md` Is Stale](#24-architecturemd-is-stale)
  - [25. Missing `.env.example`](#25-missing-envexample)
  - [26. `SIMPLIFICATION_PLAN.md` Is Stale](#26-simplification_planmd-is-stale)
  - [27. No Release / Publish Workflow](#27-no-release--publish-workflow)
  - [28. Runtime Docker Image Doesn't Include `ascend` Package](#28-runtime-docker-image-doesnt-include-ascend-package)
  - [29. No `py.typed` Marker](#29-no-pytyped-marker)
  - [30. `import ascend` Crashes Without `[azure]` Extra](#30-import-ascend-crashes-without-azure-extra)
- [Priority Matrix](#priority-matrix)

---

## Executive Summary

Ascend aims to be **turn-key** for two audiences:

| Audience | Goal | Current gaps |
|----------|------|--------------|
| **Data scientists** | `pip install`, decorate function, get results | Broken remote exception propagation, no job lifecycle CLI, silent config errors, noisy Git warnings |
| **Infra maintainers** | Bootstrap, monitor, maintain, tear down | No teardown commands, no ACR token rotation, no cost tooling, debug prints instead of structured logging |

The findings below are grouped into four priority tiers (P0–P3) based on impact. Each item includes the **affected audience**, **location in code**, **current behavior**, **expected behavior**, and a **remediation plan**.

---

## P0 — Broken or Misleading at Runtime

These issues cause crashes, silent data corruption, or fundamentally wrong behavior.

### 1. `packaging` Not Declared but Imported at Runtime

| | |
|---|---|
| **Affects** | DS |
| **Location** | `docker/runner.py` (line ~118) |
| **Current behavior** | Runner imports `from packaging.requirements import Requirement`. This works by accident because `pip` installs `packaging` as its own dependency — but this is not guaranteed and `packaging` is listed in neither `pyproject.toml` nor `docker/Dockerfile.runtime`. |
| **Risk** | If the base image or pip version changes, the runner pod crashes with `ModuleNotFoundError` at job execution time. |
| **Remediation** | Add `packaging>=23.0` to `pyproject.toml` core dependencies and add `RUN pip install packaging` to `docker/Dockerfile.runtime`. |

### 2. PLACEHOLDER Values Silently Written to Config

| | |
|---|---|
| **Affects** | DS |
| **Location** | `ascend/cli/user.py` (~line 68–72), `ascend/cloud/azure/cli.py` (~line 113–127) |
| **Current behavior** | When storage account or container registry discovery fails during `ascend user init`, the CLI writes the literal string `"PLACEHOLDER"` into `.ascend.yaml` and prints a warning. |
| **Risk** | Users who miss the warning will attempt to submit jobs against `PLACEHOLDER` and get cryptic Azure errors (e.g., `StorageAccountNotFound`). |
| **Remediation** | Either (a) **fail the init command** with a clear error and exit code 1, or (b) after writing the config, run a validation pass and print a prominent `[ACTION REQUIRED]` block listing every field that still contains `PLACEHOLDER`. Additionally, validate at job submission time that no config values equal `"PLACEHOLDER"`. |

### 3. Python Version Hardcoded to 3.11 in Multiple Places

| | |
|---|---|
| **Affects** | BOTH |
| **Location** | `ascend/decorator.py` (line ~168), `ascend/runtime/executor.py` (lines ~47, ~85) |
| **Current behavior** | `create_dependency_set(python_version="3.11", ...)` is hardcoded in three places. Meanwhile, `docker/Dockerfile.runtime` defaults to `ARG PYTHON_VERSION=3.12`, and CI builds images for both 3.11 and 3.12. |
| **Risk** | Dependency hashes and image tags are computed assuming Python 3.11 even when the user's environment or the runtime image runs 3.12, leading to mismatched images and potential package incompatibilities. |
| **Remediation** | (a) Detect `sys.version_info` at decoration time and pass it through. (b) Make `python_version` a configurable field in `.ascend.yaml` with `sys.version_info` as the default. (c) Remove all hardcoded `"3.11"` strings. |

### 4. Remote Exceptions Not Faithfully Propagated

| | |
|---|---|
| **Affects** | DS |
| **Location** | `docker/runner.py` (~line 167–172), `ascend/cloud/base.py` |
| **Current behavior** | When a function raises an exception on the pod, the runner calls `sys.exit(1)` and uploads the exception as a log entry. The client receives a generic `ExecutionError("Job failed or timed out")` — the original exception type, message, and traceback are lost. |
| **Risk** | Data scientists cannot debug remote failures. This is the single most impactful UX gap for the target audience. |
| **Remediation** | (a) In the runner, serialize the exception info (type, message, formatted traceback) to a well-known blob key (e.g., `{job_id}/exception.pkl`). (b) In `download_result()`, check for the exception blob first and re-raise with the original traceback. (c) Consider using `tblib` or `traceback` module formatting so the user sees the remote stack trace locally. |

Example of what the user experience should look like:

```
RemoteExecutionError: Remote function raised ValueError

Remote traceback (most recent call last):
  File "/workspace/runner.py", line 165, in execute
    result = func(*args, **kwargs)
  File "<cloudpickle>", line 12, in train_model
    raise ValueError("Invalid input shape")
ValueError: Invalid input shape
```

---

## P1 — UX Gaps That Hit Users Quickly

These won't crash, but they create friction or confusion within the first few minutes of use.

### 5. Dead Dependencies Bloat Install

| | |
|---|---|
| **Affects** | DS |
| **Location** | `pyproject.toml` (lines ~18, ~22) |
| **Current behavior** | `docker>=7.0` and `requests>=2.31.0` are listed as core dependencies. No module in the codebase imports either. |
| **Risk** | Unnecessary install time, dependency conflicts, and larger environments. The `docker` package in particular pulls in significant transitive dependencies. |
| **Remediation** | Remove both from `[project] dependencies`. If they are intended for future multi-cloud use, move them to an optional extra (e.g., `[project.optional-dependencies] docker = ["docker>=7.0"]`). |

### 6. `serialization.py` Is Dead Code

| | |
|---|---|
| **Affects** | BOTH |
| **Location** | `ascend/serialization.py` |
| **Current behavior** | Defines `serialize()` and `deserialize()` wrappers around `cloudpickle`. Nothing imports them — all callers use `cloudpickle.dumps()`/`cloudpickle.loads()` directly. |
| **Remediation** | Either (a) **delete** the module and update `__init__.py`, or (b) **use it consistently** everywhere (decorator, runner, storage) so serialization logic has a single entry point. Option (b) is preferred — it gives a natural place to add integrity checks (see Security, item on `cloudpickle.loads` on untrusted data). |

### 7. `NodeType.STANDARD` Duplicates `STANDARD_MEDIUM`

| | |
|---|---|
| **Affects** | DS |
| **Location** | `ascend/node_types.py` (line ~56) |
| **Current behavior** | `STANDARD = "standard_medium"` and `STANDARD_MEDIUM = "standard_medium"` have identical values. Python treats the second as an alias of the first, but it creates confusion in documentation, `--help` text, and auto-complete. |
| **Remediation** | Remove `STANDARD` and update any references to use `STANDARD_MEDIUM`, or rename `STANDARD` to a distinct value like `"standard_default"`. |

### 8. No Job Lifecycle CLI Commands

> **RESOLVED** — Implemented `ascend jobs list|status|cancel|logs` CLI commands in `ascend/cli/jobs.py`. Registered in `ascend/cli/main.py`. Added `list()` to `CloudStorage` ABC and `delete_job()`, `list_jobs()`, `get_job_status()` to `ComputeBackend` ABC. Executor now sets metadata status to `running` after job creation.

| | |
|---|---|
| **Affects** | DS |
| **Location** | `ascend/cli/` (only `admin.py`, `user.py`, `main.py` exist) |
| **Current behavior** | CLI has 3 commands: `admin setup`, `admin bootstrap`, `user init`. There is no way to list, inspect, cancel, or retrieve logs/artifacts for jobs from the command line. |
| **Planned (per `LOGGING_AND_ARTIFACTS.md`)** | `ascend jobs list`, `ascend jobs status <id>`, `ascend logs <id>`, `ascend artifacts download`. |
| **Remediation** | Implement a `jobs` CLI group with at least: |

```
ascend jobs list [--status running|completed|failed] [--limit N]
ascend jobs status <job-id>
ascend jobs cancel <job-id>
ascend jobs logs <job-id> [--follow]
```

This is critical for DS users who need to monitor long-running training jobs. The metadata infrastructure (`storage/metadata.py`, `storage/paths.py`) already exists — the CLI just needs to query it.

### 9. No Job Cancellation on Ctrl+C

> **RESOLVED** — `RemoteExecutor.execute()` now wraps the streaming/wait/download phase in `try/except KeyboardInterrupt`. On Ctrl+C: deletes the K8s job via `ComputeBackend.delete_job()`, best-effort updates metadata to `cancelled`, then re-raises.

| | |
|---|---|
| **Affects** | DS, OPS |
| **Location** | `ascend/runtime/executor.py`, `ascend/cloud/kubernetes/jobs.py` |
| **Current behavior** | When the user interrupts a running call (`Ctrl+C`), the local process exits but the K8s job continues running. |
| **Risk** | GPU jobs at $10+/hr continue to burn resources. Users have no way to stop them without `kubectl`. |
| **Remediation** | Register a `signal.SIGINT` handler in `RemoteExecutor.execute()` that calls `BatchV1Api.delete_namespaced_job()` on interrupt, then re-raises `KeyboardInterrupt`. |

### 10. `wait_for_completion` Treats 404 Job as Success

> **RESOLVED** — `wait_for_completion()` now raises `ExecutionError` when a job returns 404 and no succeeded pods are found, instead of silently returning `True`.

| | |
|---|---|
| **Affects** | DS |
| **Location** | `ascend/cloud/kubernetes/jobs.py` (~line 208–226) |
| **Current behavior** | When the K8s API returns 404 for a job and no pods are found, the function returns `True` (success). |
| **Risk** | Jobs deleted by TTL, manual cleanup, or K8s garbage collection are reported as successful. Silent data loss. |
| **Remediation** | Return `False` or raise `ExecutionError("Job not found — it may have been deleted or expired")`. |

### 11. ~~No Config Format Validation~~ ✅

| | |
|---|---|
| **Affects** | DS |
| **Location** | `ascend/config.py` (~line 57–67) |
| **Current behavior** | `load_config()` only validates that required **keys** exist. Does not validate: |
| | - `username`: must be lowercase alphanumeric for K8s namespace names |
| | - `storage_account`: must be 3–24 lowercase alphanumeric chars (Azure rule) |
| | - `cpu`: must be a valid K8s resource quantity (e.g., `"1"`, `"500m"`) |
| | - `memory`: must be a valid K8s resource quantity (e.g., `"2Gi"`, `"512Mi"`) |
| | - `namespace`: must follow K8s naming rules (lowercase, alphanumeric, `-`) |
| **Risk** | Invalid values pass through silently and only error deep in the Azure or K8s API with unhelpful messages. |
| **Remediation** | Add a `validate_config_values(cfg: dict)` function that applies regex/range checks and raises `ConfigurationError` with a clear message on failure. Call it in `load_config()`. |

Example validation:

```python
import re

def validate_config_values(cfg: dict) -> None:
    username = cfg.get("username", "")
    if not re.match(r'^[a-z0-9][a-z0-9-]*$', username):
        raise ConfigurationError(
            f"Invalid username '{username}': must be lowercase alphanumeric "
            f"(may contain hyphens, must start with letter or digit)"
        )

    storage = cfg.get("storage_account", "")
    if not re.match(r'^[a-z0-9]{3,24}$', storage):
        raise ConfigurationError(
            f"Invalid storage_account '{storage}': must be 3-24 lowercase "
            f"alphanumeric characters (Azure naming requirement)"
        )
```

**Resolution:** Added `_validate_config_values()` in `ascend/config.py` with regex validation for `username` (K8s RFC 1123 label), `namespace` (K8s naming rules), `storage_account` (Azure 3-24 lowercase alphanumeric), and optional `cpu`/`memory` (K8s resource quantity format). Called from `load_config()` after key-presence and placeholder checks. All errors are collected and reported in a single `ConfigError`. Tests: `tests/test_config_validation.py` (32 tests).

### 12. ~~`NodePoolValidator` Exists but Is Never Called~~ ✅

| | |
|---|---|
| **Affects** | DS, OPS |
| **Location** | `ascend/cloud/azure/node_pool_validator.py`, `ascend/cloud/node_pool_validator.py` |
| **Current behavior** | `NodePoolValidator` can check whether a cluster has node pools matching the requested `NodeType`. It is exported from the cloud module but never invoked in any execution path. |
| **Risk** | Users request `gpu_large` on a cluster without GPU node pools. The pod is created, sits in `Unschedulable` until the timeout, then fails with a generic timeout error. |
| **Remediation** | Call `NodePoolValidator.validate()` in `RemoteExecutor.execute()` before creating the K8s job — fail fast with an actionable message: `"Node type 'gpu_large' requires a GPU node pool, but none are available in cluster '{cluster}'. Ask your admin to run 'ascend admin setup --gpu'."` |

**Resolution:** Added `_validate_node_pool()` method to `RemoteExecutor` in `ascend/runtime/executor.py`. When `node_type` is set, it is called before `create_job()`. Uses `NodePoolValidator` (K8s API first, Azure API fallback) and raises `ExecutionError` with an actionable message including the `ascend admin setup --gpu` hint. Gracefully degrades when Azure credentials are unavailable (K8s-only validation still works). Tests: `tests/test_node_pool_wiring.py` (7 tests).

---

## P2 — Code Quality & Ops Ergonomics

### 13. ~~ACR Token in K8s Secret Expires and Never Rotates~~ ✅

| | |
|---|---|
| **Affects** | OPS |
| **Location** | `ascend/cloud/azure/infrastructure.py` (~line 584–610) |
| **Current behavior** | During `admin bootstrap`, an ACR refresh token is exchanged from an ARM access token and stored in a K8s `registry-credentials` Secret. These tokens have a limited lifetime (typically hours). |
| **Risk** | Kaniko image builds fail with auth errors after the token expires. Admins must manually re-run bootstrap. |
| **Remediation** | Options: (a) Create a CronJob or controller that refreshes the token periodically, (b) switch to an ACR admin password (less secure but doesn't expire), or (c) use Azure Workload Identity for ACR access (preferred — tokens are auto-refreshed by the identity webhook). |

**Resolution:** `AzureImageBuilder` now calls `ensure_registry_credentials_secret()` before every Kaniko build via a new `_refresh_registry_credentials()` method. The function accepts `credential` and `login_server` constructor parameters (threaded from the backend factory in `ascend/cloud/azure/backend.py`). `ensure_registry_credentials_secret()` gained a `quiet=True` parameter to suppress `console.print()` output when called automatically. Gracefully degrades when credentials are unavailable. Tests: `tests/test_logging_and_exceptions.py` (`TestImageBuilderTokenRefresh`, `TestBackendPassesCredential`, `TestEnsureRegistryCredentialsQuietParam` — 7 tests).

### 14. No Teardown / Cleanup Commands

| | |
|---|---|
| **Affects** | OPS |
| **Location** | `ascend/cli/admin.py` |
| **Current behavior** | `admin setup` and `admin bootstrap` create infrastructure. There is no inverse operation. |
| **Needed commands** | |

```
ascend admin teardown              # Remove all Ascend resources from cluster
ascend admin cleanup --older-than 7d  # Delete jobs/images/blobs older than N days
ascend admin remove-user <username>   # Remove a user namespace and resources
```

| **Risk** | Infra maintainers must use raw `kubectl`/`az` commands for cleanup, which is error-prone and breaks the "turn-key" promise. |
| **Remediation** | Implement teardown as the reverse of bootstrap: delete namespaces, service accounts, secrets, RBAC bindings. Implement cleanup as a blob/ACR/job GC sweep. |

### 15. ~~Debug `print()` Statements in Production Code~~ ✅

| | |
|---|---|
| **Affects** | BOTH |
| **Locations** | |
| | `ascend/cloud/kubernetes/jobs.py` (lines ~161–178): 5× `print(..., file=sys.stderr)` with `[CREATE_JOB]` prefix |
| | `ascend/runtime/executor.py`: 10+ `print()` calls for status updates |
| | `ascend/cloud/azure/image_builder.py`: emoji `print()` for build status |
| | `ascend/runtime/streaming.py`: emoji `print()` for warnings |
| **Current behavior** | Ad-hoc `print()` calls scattered across the codebase. The project has a full `AscendLogger` class in `ascend/utils/structured_logging.py` that is **never used by any production module**. |
| **Remediation** | (a) Replace all `print()` calls in library code with `AscendLogger` or Python's standard `logging` module. (b) Reserve `print()` for CLI output only (`ascend/cli/*.py`). (c) Use `rich` (already a dependency) for CLI status display. |

**Resolution:** Replaced all 29 bare `print()` calls in non-CLI library code with `logging.getLogger(__name__)` at appropriate levels: `logger.info()` for user-facing status, `logger.debug()` for internal progress, `logger.warning()` for errors/fallbacks. Added module-level `logger` to `executor.py`, `streaming.py`, `jobs.py`, `image_builder.py`, `registry.py`, `storage.py`, `kaniko.py`. Added `NullHandler` to root `ascend` logger in `__init__.py` per library best practices. Only remaining `print(line)` is in `streaming.py` for intentional pod log relay to stdout. CLI and infrastructure code retains `rich` `console.print()`. Tests: `tests/test_logging_and_exceptions.py` (`TestNullHandlerSetup`, `TestLibraryModulesUseLogging`, `TestNoPrintInLibrary` — 11 tests).

### 16. ~~Silent Exception Swallowing Throughout~~ ✅

| | |
|---|---|
| **Affects** | BOTH |
| **Locations** | |

| File | Line(s) | Pattern |
|------|---------|---------|
| `ascend/cloud/azure/registry.py` | ~42 | `except Exception: return False` — swallows auth failures |
| `ascend/cloud/azure/storage.py` | ~36–40 | `except Exception: pass` — ignores container creation errors |
| `ascend/cloud/kubernetes/kaniko.py` | ~129–131 | `except Exception: pass` — ignores job deletion during cleanup |
| `ascend/cli/user.py` | ~119–120 | `except Exception: pass` — ignores JWT token parsing failures |
| `docker/runner.py` | ~187–188 | `except Exception: pass` — ignores log upload failures |

| **Risk** | Network outages, auth failures, and storage errors are silently ignored, making debugging nearly impossible. |
| **Remediation** | (a) At minimum, log the exception at `WARNING` or `DEBUG` level. (b) For non-critical paths (cleanup), catch only the specific expected exception (e.g., `ApiException`). (c) For critical paths (storage, auth), let the exception propagate. |

**Resolution:** Fixed 7 critical/moderate exception-swallowing sites: (1) `registry.py` `image_exists()` / `delete_tag()` now distinguish `ResourceNotFoundError` (silent) from other errors (logged at WARNING). (2) `executor.py` image build failure now logged with `exc_info=True` instead of bare `print()`. (3) `storage.py` `ensure_container()` logs at DEBUG instead of `pass`. (4) `kaniko.py` `get_job_status()` catches `ApiException` specifically (others re-raise); `delete_job()` silences 404 but logs other `ApiException` at WARNING. (5) `runner.py` log upload failure now prints to stderr. Additionally, resolved duplicate `ImageBuildError` — unified in `ascend/utils/errors.py` with `logs` parameter; `kaniko.py` re-exports from `errors.py`. `ImageBuildTimeout` also moved to `errors.py` (inherits `TimeoutError`). Tests: `tests/test_logging_and_exceptions.py` (`TestRegistryExceptionHandling`, `TestStorageExceptionHandling`, `TestKanikoExceptionHandling`, `TestImageBuildErrorUnification`, `TestExecutorImageBuildWarning` — 15 tests).

### 17. Config Loaded Twice per Execution

| | |
|---|---|
| **Affects** | DS |
| **Location** | `ascend/decorator.py` (~line 108–109), `ascend/runtime/executor.py` (~line 19–20) |
| **Current behavior** | `load_config()` is called once in the decorator wrapper and again in `RemoteExecutor.__init__()`. The YAML file is parsed from disk twice per function invocation. |
| **Remediation** | Pass the already-loaded config dict from the decorator into `RemoteExecutor` instead of re-loading it. |

### 18. `AscendConfig` Is a Plain Class, Not a Dataclass

| | |
|---|---|
| **Affects** | BOTH |
| **Location** | `ascend/decorator.py` (lines ~12–36) |
| **Current behavior** | Manual `__init__` with attribute assignment. The project convention (used by `NodeTypeInfo`, `JobMetadata`, `DependencySet`, etc.) is `@dataclass`. |
| **Remediation** | Convert to `@dataclass` (or `@dataclass(frozen=True)` for immutability). Move `_requirements_provided` logic to a `__post_init__` method. |

### 19. `backoff_limit=4` Hardcoded, Not Configurable

| | |
|---|---|
| **Affects** | DS, OPS |
| **Location** | `ascend/cloud/kubernetes/jobs.py` (line ~155) |
| **Current behavior** | K8s jobs retry up to 4 times on failure. |
| **Risk** | For GPU jobs at $10+/hr, 4 retries = $40+ in wasted compute. For idempotent CPU jobs, 4 retries might be reasonable. One size doesn't fit all. |
| **Remediation** | Add `retries: int = 0` to `AscendConfig`. Default to 0 for GPU workloads and 2 for CPU workloads. Pass through to `backoff_limit`. |

### 20. Result Download Has Hardcoded 60s Timeout

| | |
|---|---|
| **Affects** | DS |
| **Location** | `ascend/cloud/base.py` (~line 85–92) |
| **Current behavior** | `download_result()` polls blob storage for 60 seconds with 2-second intervals. If the job completes but takes time to write a large result, the client gives up with `RuntimeError("Result not found in storage")`. |
| **Remediation** | Make the timeout proportional to the job's configured `timeout`, or remove the fixed limit entirely and rely on the job-level timeout instead. |

---

## P3 — Polish, Docs, Packaging

### 21. Global Mutable Singletons

| | |
|---|---|
| **Affects** | BOTH |
| **Location** | `ascend/cloud/registry.py` (`_detected`), `ascend/cloud/azure/auth.py` (`_cached_credential`) |
| **Current behavior** | Module-level mutable variables with `reset_*()` functions for testing. |
| **Risk** | Test pollution if `reset_*()` isn't called, thread-safety issues. Violates stated project convention ("avoid global variables / mutable state"). |
| **Remediation** | Use a `contextvars.ContextVar` or pass the backend/credential explicitly through the call chain. |

### 22. `image_pull_policy="Always"` Hardcoded

| | |
|---|---|
| **Affects** | OPS |
| **Location** | `ascend/cloud/kubernetes/jobs.py` (line ~99) |
| **Current behavior** | Every job run re-pulls the image from ACR, even if the same tag was pulled seconds ago. |
| **Impact** | Adds 10–30s latency per job and increases ACR egress costs. |
| **Remediation** | Use `"IfNotPresent"` for content-addressed image tags (which are immutable by design). Only use `"Always"` for mutable tags like `latest`. |

### 23. Git Validation Runs on Every Function Call

| | |
|---|---|
| **Affects** | DS |
| **Location** | `ascend/decorator.py` (~line 94–117) |
| **Current behavior** | `validate_git_repository()` is called every time a decorated function is invoked. For non-project usage outside a Git repo (e.g., Jupyter notebooks, quick scripts), this emits a `UserWarning` every time. |
| **Remediation** | Only call `validate_git_repository()` when `project=True`. For non-project usage, skip Git validation entirely. |

### 24. `ARCHITECTURE.md` Is Stale

| | |
|---|---|
| **Affects** | BOTH |
| **Location** | `ARCHITECTURE.md` |
| **Issues** | |
| | - Still references `@job` as the decorator name (should be `@ascend`) |
| | - Lists Prometheus metrics (`ascend_cost_estimate_dollars`, etc.) that don't exist |
| | - Describes a "Job Template" YAML format that differs from actual implementation |
| **Remediation** | Audit and update the document to match current implementation. Remove aspirational sections or clearly mark them as "Planned". |

### 25. Missing `.env.example`

| | |
|---|---|
| **Affects** | BOTH |
| **Location** | Referenced in `docs/INTEGRATION_TESTING.md` (~line 63–66) |
| **Current behavior** | The integration testing doc instructs users to `cp .env.example .env`, but no `.env.example` file exists in the repository. |
| **Remediation** | Create `.env.example` with placeholder values and comments explaining each variable. |



### 27. No Release / Publish Workflow

| | |
|---|---|
| **Affects** | BOTH |
| **Location** | `.github/workflows/` |
| **Current behavior** | No CI workflow for PyPI publishing, GitHub Releases, changelog generation, or version bumping. Version is stuck at `0.1.0`. |
| **Remediation** | Add a release workflow triggered by Git tags (e.g., `v*`) that: (a) runs tests, (b) builds wheel and sdist, (c) publishes to PyPI with trusted publishing, (d) creates a GitHub Release with auto-generated notes. |

### 28. Runtime Docker Image Doesn't Include `ascend` Package

| | |
|---|---|
| **Affects** | BOTH |
| **Location** | `docker/Dockerfile.runtime` |
| **Current behavior** | The Dockerfile installs `cloudpickle`, `fsspec`, `adlfs` and copies `runner.py`. It does **not** install the `ascend` package. |
| **Impact** | `AscendLogger`, structured logging, and any utility from `ascend.*` are unavailable inside the pod. The runner must re-implement any shared logic. |
| **Remediation** | Either (a) install `ascend` in the runtime image (`COPY . /app && pip install /app`), or (b) if the image must stay minimal, factor out the shared utilities (logging, serialization) into a separate lightweight package. |

### 29. No `py.typed` Marker

| | |
|---|---|
| **Affects** | DS |
| **Location** | `ascend/` (missing file) |
| **Current behavior** | No `py.typed` marker file. Type checkers (`mypy`, `pyright`) used by downstream consumers won't resolve the inline type annotations. |
| **Remediation** | Create an empty `ascend/py.typed` file and ensure it's included in the built wheel (add to `[tool.hatch.build]` if needed). |

### 30. `import ascend` Crashes Without `[azure]` Extra

| | |
|---|---|
| **Affects** | DS |
| **Location** | `ascend/__init__.py` (lines ~10–11) |
| **Current behavior** | `detect_backend_name()` is called at import time. If no cloud backend extra is installed, it raises with `"No cloud backend installed"` but does not suggest the fix. |
| **Remediation** | Improve the error message: `"No cloud backend installed. Install one with: pip install ascend[azure]"`. Consider making the detection lazy — only fail when a backend is actually needed, not at import time. This would allow `from ascend import AscendConfig` for configuration-only use. |

---

## Priority Matrix

| Priority | Items | Theme | Effort |
|----------|-------|-------|--------|
| **P0** | 1, 2, 3, 4 | Broken or misleading at runtime | Small–Medium |
| **P1** | 5, 6, 7, 8, 9, 10, 11, 12 | UX gaps that hit users quickly | Medium–Large |
| **P2** | 13, 14, 15, 16, 17, 18, 19, 20 | Code quality & ops ergonomics | Medium |
| **P3** | 21, 22, 23, 24, 25, 27, 28, 29, 30 | Polish, docs, packaging | Small–Medium |

### Suggested Implementation Order

**Phase 1 — Fix what's broken (P0):** Items 1–4. These are small, isolated fixes that prevent runtime crashes and debugging nightmares. Can ship in one PR.

**Phase 2 — Close UX gaps (P1):** Items 5–7 (cleanup, 1 day), items 11–12 (validation, 1 day), items 8–10 (CLI + cancellation, 3–5 days). The job lifecycle CLI (item 8) is the largest item but has the highest impact.

**Phase 3 — Production hardening (P2):** Items 13–20. Focus on logging migration (item 15), exception handling (item 16), and ACR token rotation (item 13) first. The rest are incremental improvements.

**Phase 4 — Polish (P3):** Items 21–30. Documentation updates, release automation, and packaging improvements. Can be done opportunistically alongside other work.

---

## Security Notes (Non-Prioritized)

These are architectural observations worth tracking but don't have immediate fixes:

| Concern | Location | Notes |
|---------|----------|-------|
| `cloudpickle.loads` on untrusted data | `docker/runner.py` (~line 103) | Inherent to cloudpickle design. Mitigate with package integrity checks (HMAC/signature on uploaded blob). |
| No K8s `NetworkPolicy` resources | `ascend/cloud/azure/infrastructure.py` | Pods can communicate freely within the cluster. Add default-deny policies per user namespace. |
| MD5 for resource naming | `ascend/utils/naming.py` (~line 15) | Not a security hash (used for uniqueness), but SHA256 would be more future-proof. |
| No input sanitization of `package_url` | `ascend/cloud/kubernetes/jobs.py` (~line 70–71) | Validate the URL points to the expected storage account before passing as env var. |
| Project namespace uses default SA | `ascend/cloud/kubernetes/jobs.py` (~line 135–138) | `ascend-projects-*` namespaces set `service_account_name=None`, running with default permissions. Should create project-scoped service accounts. |
