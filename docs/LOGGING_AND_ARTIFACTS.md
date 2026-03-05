# Logging and Artifact Indexing Architecture

## Table of Contents
1. [Executive Summary](#executive-summary)
2. [Content-Addressable Naming Scheme](#content-addressable-naming-scheme)
3. [Structured Logging Format](#structured-logging-format)
4. [Blob Storage Organization](#blob-storage-organization)
5. [Indexing and Metadata](#indexing-and-metadata)
6. [Implementation Roadmap](#implementation-roadmap)
7. [Example Usage](#example-usage)

## Executive Summary

This document describes the architecture for a comprehensive logging and artifact indexing system for Ascend. The system enables:

- **Content-addressable naming**: Jobs, logs, and artifacts are named using a deterministic scheme that enables filtering by user, project, date range, dependencies, and other metadata
- **Structured logging**: All logs follow a consistent format that is both human-readable and machine-parsable (JSON Lines format)
- **Search and retrieval**: Users can search and retrieve logs and artifacts from previous runs using intuitive CLI commands
- **Bulk operations**: Administrators can search, retrieve, and delete logs/jobs/artifacts in bulk using query filters
- **Project-scoped storage**: All logs and artifacts are stored in blob storage with per-project isolation

### Design Principles

1. **Deterministic naming**: Job IDs incorporate metadata for easy filtering without requiring a database
2. **Human-readable**: Logs are viewable in terminal with rich formatting while remaining machine-parsable
3. **Efficient storage**: Logs and artifacts use blob storage with lifecycle policies for automatic archival
4. **Fast retrieval**: Metadata is encoded in path structure for quick filtering without full scanning
5. **Privacy-aware**: User namespaces provide isolation while allowing admin oversight

## Content-Addressable Naming Scheme

### Job ID Format

Job IDs follow a structured format that encodes metadata for filtering and discovery:

```
{timestamp}-{user}-{project}-{dep_hash}-{run_hash}
```

**Components:**
- `timestamp`: ISO 8601 format `YYYYMMDD-HHMMSS` (UTC) for time-based filtering
- `user`: Username (sanitized, lowercase, max 16 chars)
- `project`: Project name (sanitized, lowercase, max 16 chars, default: "default")
- `dep_hash`: First 8 characters of dependency set hash (from `DependencySet.calculate_hash()`)
- `run_hash`: First 8 characters of SHA256(function_name + args_hash + random_nonce)

**Example:**
```
20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a
```

This ID tells us:
- Created on 2025-12-04 at 14:30:22 UTC
- By user "alice"
- In project "frauddetect"
- With dependency hash a3f5d2c8 (can correlate with image tags)
- Unique run identifier 7b9e4f1a

### Artifact Naming

Artifacts follow a similar pattern with type suffixes:

```
{job_id}.{artifact_type}.{extension}
```

**Examples:**
```
20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a.log.jsonl
20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a.result.pkl
20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a.model.pkl
20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a.plot.png
```

### Image Tag Correlation

Image tags already use dependency hashing (format: `user-{hash}`). The `dep_hash` in job IDs matches the first 8 chars of the 12-char image hash, enabling correlation:

**Image tag:** `alice-a3f5d2c8e9b1` (12 chars)  
**Job ID contains:** `a3f5d2c8` (first 8 chars match)

This allows queries like "show me all jobs using this Docker image".

## Structured Logging Format

### Log Format: JSON Lines (JSONL)

All logs use JSON Lines format - one JSON object per line. This format is:
- **Human-readable**: Can be viewed with `cat`, `less`, or `tail -f`
- **Machine-parsable**: Each line is valid JSON for easy processing
- **Streamable**: Can process logs line-by-line without loading entire file
- **Standard**: Wide tool support (jq, LogStash, Fluentd, etc.)

### Log Entry Schema

```json
{
  "timestamp": "2025-12-04T14:30:22.123456Z",
  "level": "INFO",
  "logger": "ascend.runner",
  "job_id": "20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a",
  "user": "alice",
  "project": "frauddetect",
  "message": "Starting function execution",
  "context": {
    "function_name": "train_model",
    "node_type": "gpu_small",
    "pod_name": "ascend-20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a-xyz",
    "namespace": "ascend-users-alice"
  },
  "extra": {}
}
```

**Required fields:**
- `timestamp`: ISO 8601 with microseconds (UTC)
- `level`: One of DEBUG, INFO, WARNING, ERROR, CRITICAL
- `logger`: Logger name (hierarchical, e.g., "ascend.runner.executor")
- `job_id`: Full job ID for correlation
- `user`: Username
- `project`: Project name
- `message`: Human-readable log message

**Optional fields:**
- `context`: Structured context (function name, pod info, etc.)
- `extra`: Additional metadata specific to log entry
- `exception`: Exception details (type, message, stacktrace) for ERROR/CRITICAL
- `duration_ms`: Duration in milliseconds for timing logs
- `metrics`: Performance metrics (memory usage, GPU utilization, etc.)

### Log Levels

| Level    | Usage                                           | Examples                                    |
|----------|-------------------------------------------------|---------------------------------------------|
| DEBUG    | Detailed debugging information                  | Function arguments, internal state          |
| INFO     | Normal operational events                       | "Job started", "Dependencies installed"     |
| WARNING  | Non-critical issues                             | "Package not found, using fallback"         |
| ERROR    | Error events that don't stop execution          | "Failed to upload artifact, retrying"       |
| CRITICAL | Fatal errors requiring job termination          | "Out of memory", "Function crashed"         |

### Example Log Sequence

```jsonl
{"timestamp":"2025-12-04T14:30:22.001Z","level":"INFO","logger":"ascend.runner","job_id":"20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a","user":"alice","project":"frauddetect","message":"Job started","context":{"pod_name":"ascend-...-xyz","namespace":"ascend-users-alice"}}
{"timestamp":"2025-12-04T14:30:23.456Z","level":"INFO","logger":"ascend.runner.deps","job_id":"20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a","user":"alice","project":"frauddetect","message":"Installing 5 dependencies","context":{"packages":["numpy","pandas","scikit-learn","torch","transformers"]}}
```



## Blob Storage Organization

### Storage Structure

All logs and artifacts are stored in Azure Blob Storage with the following hierarchy:

```
<storage-account>/
└── ascend-data/
    ├── projects/
    │   └── {project-name}/          # Per-project isolation
    │       ├── users/
    │       │   └── {username}/      # Per-user within project
    │       │       ├── jobs/
    │       │       │   └── {job-id}/
    │       │       │       ├── package.pkl         # Serialized function + args
    │       │       │       ├── metadata.json       # Job metadata (see schema below)
    │       │       │       ├── job.log.jsonl       # Structured logs
    │       │       │       ├── result.pkl          # Execution result
    │       │       │       └── artifacts/          # User-generated artifacts
    │       │       │           ├── model.pkl
    │       │       │           ├── plot.png
    │       │       │           └── metrics.json
    │       │       └── images/
    │       │           └── {dep-hash}/
    │       │               ├── requirements.txt
    │       │               └── Dockerfile
    │       └── logs/
    │           └── by-date/
    │               └── {YYYY}/{MM}/{DD}/
    │                   └── {job-id}.log.jsonl  # Symlink or copy for date-based lookup
    └── shared/
        └── base-images/                        # Shared base images (not user-specific)
```

### Metadata Schema

Each job has a `metadata.json` file with comprehensive information:

```json
{
  "job_id": "20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a",
  "created_at": "2025-12-04T14:30:22.000Z",
  "updated_at": "2025-12-04T14:35:23.456Z",
  "status": "completed",
  "user": "alice",
  "project": "frauddetect",
  "function_name": "train_model",
  "config": {
    "cpu": "4",
    "memory": "8Gi",
    "timeout": 3600,
    "node_type": "gpu_small"
  },
  "dependencies": {
    "hash": "a3f5d2c8e9b1",
    "python_version": "3.11",
    "packages": [
      "numpy==1.24.0",
      "pandas==2.0.0",
      "scikit-learn==1.3.0",
      "torch==2.0.0",
      "transformers==4.30.0"
    ],
    "system_packages": [],
    "use_gpu": true
  },
  "execution": {
    "start_time": "2025-12-04T14:30:46.001Z",
    "end_time": "2025-12-04T14:35:22.123Z",
    "duration_seconds": 276.122,
    "pod_name": "ascend-20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a-xyz",
    "namespace": "ascend-users-alice",
    "node_name": "aks-gpu-12345-vmss000001",
    "exit_code": 0
  },
  "resources": {
    "cpu_requested": "4",
    "memory_requested": "8Gi",
    "gpu_requested": "1",
    "peak_memory_mb": 4096,
    "avg_cpu_percent": 65,
    "avg_gpu_utilization_percent": 85
  },
  "artifacts": [
    {
      "name": "model.pkl",
      "type": "model",
      "size_bytes": 104857600,
      "created_at": "2025-12-04T14:35:21.000Z"
    },
    {
      "name": "plot.png",
      "type": "visualization",
      "size_bytes": 524288,
      "created_at": "2025-12-04T14:35:22.000Z"
    }
  ],
  "tags": {
    "experiment": "baseline",
    "model_type": "random_forest",
    "dataset_version": "v2.1"
  }
}
```

### Storage Access Patterns

**By Job ID (primary):**
```
projects/{project}/users/{user}/jobs/{job-id}/
```

**By Date (secondary index):**
```
projects/{project}/logs/by-date/{YYYY}/{MM}/{DD}/{job-id}.log.jsonl
```

**By User (list all user jobs):**
```
projects/{project}/users/{user}/jobs/
```

### Lifecycle Policies

Blob storage lifecycle policies for cost optimization:

| Storage Tier | Age      | Access Pattern | Cost/GB |
|--------------|----------|----------------|---------|
| Hot          | 0-30d    | Frequent       | $0.018  |
| Cool         | 30-90d   | Infrequent     | $0.010  |
| Archive      | 90d+     | Rare           | $0.002  |
| Delete       | 365d+    | None           | $0.000  |

**Exceptions:**
- Tagged jobs with `retain=true` are never deleted
- Admin can configure retention per-project
- Critical artifacts (e.g., production models) can have custom retention

## Indexing and Metadata

### Index-Free Querying

The content-addressable naming scheme enables queries without a database by using blob storage listing with prefix filters.

**Query patterns:**

```python
# List all jobs by user alice in project frauddetect
prefix = "projects/frauddetect/users/alice/jobs/"

# List all jobs in date range (Dec 1-7, 2025)
prefix = "projects/{project}/logs/by-date/2025/12/"
start_date = "01"
end_date = "07"

# List all jobs using specific dependency hash
# Scan jobs, filter by dep_hash in job_id
pattern = "*-*-*-a3f5d2c8-*"
```

### Search Index (Optional Future Enhancement)

For complex queries across projects, a lightweight search index can be maintained:

**Azure Search Index:**
```json
{
  "job_id": "string",
  "created_at": "datetime",
  "user": "string",
  "project": "string",
  "function_name": "string",
  "status": "string",
  "duration_seconds": "float",
  "node_type": "string",
  "dep_hash": "string",
  "tags": ["string"],
  "python_version": "string",
  "packages": ["string"]
}
```

**Enables queries like:**
- "Find all failed jobs in the last week"
- "Show jobs using torch>=2.0.0"
- "List all GPU jobs by cost"

The index is populated asynchronously by a background process that watches blob storage.

## CLI Interface

### User Commands

Users can manage and inspect their jobs via the `ascend jobs` CLI group:

```bash
# List recent jobs (default: last 20)
uv run ascend jobs list
uv run ascend jobs list --status running
uv run ascend jobs list --status failed --limit 5
uv run ascend jobs list --project myproject

# Show detailed status for a specific job
uv run ascend jobs status <job-id>

# Cancel a running job (deletes K8s job and marks metadata as cancelled)
uv run ascend jobs cancel <job-id>

# View stored logs for a completed job
uv run ascend jobs logs <job-id>

# Stream live logs from a running job
uv run ascend jobs logs <job-id> --follow
```

**`ascend jobs list`** reads job metadata from blob storage, filters and
sorts by creation time. The `--status` filter supports: `queued`, `running`,
`completed`, `failed`, `cancelled`.

**`ascend jobs status`** displays full job details including resource config,
dependency info, and — for running jobs — live K8s pod status.

**`ascend jobs cancel`** deletes the K8s Job resource (with background
propagation so pods are cleaned up) and updates the metadata to `cancelled`.

**`ascend jobs logs`** reads stored log files (`job.log.jsonl`) from blob
storage. With `--follow`, it streams live logs from the pod via the K8s API.






## Technical Considerations

### Security and Privacy

1. **Namespace Isolation**: Jobs, logs, and artifacts are isolated by user namespace
2. **RBAC**: Users can only access their own data; admins can access all data within a project
3. **Audit Logging**: All admin operations (especially deletions) are logged


### Performance

1. **Lazy Loading**: Don't download full logs unless requested
2. **Pagination**: List commands return paginated results
3. **Streaming**: Use streaming for large file downloads
4. **Caching**: Cache frequently accessed metadata locally
5. **Parallel Operations**: Bulk operations should use parallelism

### Reliability

1. **Retry Logic**: Failed log uploads should be retried
2. **Partial Uploads**: If artifact upload fails, job still succeeds
3. **Graceful Degradation**: Missing metadata shouldn't break log viewing
4. **Data Integrity**: Use checksums for artifact downloads

### Cost Optimization

1. **Compression**: Compress logs before archiving
2. **Lifecycle Policies**: Automatically move old data to cheaper storage tiers
3. **Deduplication**: Shared dependencies should not be duplicated
4. **Selective Retention**: Allow users to mark important jobs for longer retention


## Conclusion

This architecture provides a comprehensive logging and artifact management system that:

- ✅ Enables efficient search and retrieval without requiring a database
- ✅ Provides human-readable logs while maintaining machine parseability
- ✅ Supports both user self-service and admin bulk operations
- ✅ Optimizes storage costs through lifecycle policies
- ✅ Maintains security and privacy through namespace isolation
- ✅ Scales efficiently with growing data volumes

The content-addressable naming scheme and structured logging format form a solid foundation that can evolve with additional features while remaining backward compatible.
