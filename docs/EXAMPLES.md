# Examples

Practical examples showing how to use Ascend for real-world workloads.

## Table of Contents

- [Hyperparameter Tuning with Optuna](#hyperparameter-tuning-with-optuna)
- [GPU Training with PyTorch Lightning](#gpu-training-with-pytorch-lightning)
- [Serialization Caveats](#serialization-caveats)
- [Writing Your Own](#writing-your-own)

---

## Hyperparameter Tuning with Optuna

**Example file**: [`examples/optuna_xgboost.py`](../examples/optuna_xgboost.py)

### The Pattern: Local Orchestrator + Remote Compute

Many ML workflows follow a loop where a lightweight orchestrator selects
what to compute, and an expensive function does the heavy lifting. Ascend
fits naturally into this pattern — the orchestrator runs locally while
each compute step runs on a Kubernetes pod.

```
Local machine                            K8s cluster
─────────────                            ───────────
Optuna study.optimize
  └─ objective(trial)
       ├─ trial.suggest_*  (pick params)
       └─ evaluate_params(params)  ──────→  Pod: train XGBoost
                                              │
            accuracy  ←──────────────────────┘
```

### Why Not Send the Trial Object?

Optuna's `Trial` holds internal references to the study's database storage.
It is **not serializable** with cloudpickle, so we cannot pass it to a
remote function. The solution is simple:

1. Call `trial.suggest_*()` **locally** to sample hyperparameters.
2. Pack the values into a plain `dict`.
3. Pass only the `dict` to the `@ascend`-decorated function.

This separation keeps the serialization boundary clean — only plain Python
data types cross the wire.

### The Remote Function

```python
@ascend(
    cpu="2",
    memory="4Gi",
    timeout=600,
    requirements=["xgboost>=2.0", "scikit-learn>=1.3", "numpy>=1.24"],
)
def evaluate_params(params: dict) -> float:
    """Train XGBoost with the given params, return validation accuracy."""
    import numpy as np
    import sklearn.datasets
    import sklearn.metrics
    from sklearn.model_selection import train_test_split
    import xgboost as xgb

    data, target = sklearn.datasets.load_breast_cancer(return_X_y=True)
    train_x, valid_x, train_y, valid_y = train_test_split(
        data, target, test_size=0.25, random_state=42
    )

    dtrain = xgb.DMatrix(train_x, label=train_y)
    dvalid = xgb.DMatrix(valid_x, label=valid_y)

    bst = xgb.train(params, dtrain)
    preds = bst.predict(dvalid)
    pred_labels = np.rint(preds)
    return float(sklearn.metrics.accuracy_score(valid_y, pred_labels))
```

Key choices:
- **Data is loaded inside the pod** (via `sklearn.datasets`) rather than
  serialized from the local machine. For real workloads, read from cloud
  storage instead.
- **Imports are inside the function body** so they resolve on the pod
  where the packages are installed, not on the local machine.
- **`requirements` lists only what the pod needs.** Optuna is *not*
  included — it only runs locally.

### The Local Objective

```python
def objective(trial) -> float:
    params = {
        "verbosity": 0,
        "objective": "binary:logistic",
        "tree_method": "exact",
        "booster": trial.suggest_categorical("booster", ["gbtree", "gblinear", "dart"]),
        "lambda": trial.suggest_float("lambda", 1e-8, 1.0, log=True),
        "alpha": trial.suggest_float("alpha", 1e-8, 1.0, log=True),
        "subsample": trial.suggest_float("subsample", 0.2, 1.0),
    }
    if params["booster"] in ("gbtree", "dart"):
        params["max_depth"] = trial.suggest_int("max_depth", 3, 9, step=2)
        params["eta"] = trial.suggest_float("eta", 1e-8, 1.0, log=True)

    return evaluate_params(params)  # runs on K8s
```

### Running the Example

```bash
# Install Optuna locally (not needed on the cluster)
pip install optuna

# Run the study (10 trials, each training on a K8s pod)
python examples/optuna_xgboost.py
```

---

## GPU Training with PyTorch Lightning

**Example file**: [`examples/lightning_mnist.py`](../examples/lightning_mnist.py)

### The Pattern: Plain Dict In, Metrics Dict Out

Deep-learning training is a natural fit for Ascend — the heavy computation
runs on a GPU pod while you iterate on hyperparameters locally. The key
constraint is the same as the Optuna example: only plain Python types should
cross the cloudpickle serialization boundary.

```
Local machine                             K8s cluster (GPU)
─────────────                             ─────────────────
hparams = {"lr": 1e-3, …}
  │
  └─ train_and_validate(hparams)  ──────→  Pod (gpu_small: 1× V100)
           ↑                                 │  ▸ download MNIST
           │                                 │  ▸ build SmallResNet
  val_metrics (dict)  ←──────────────────────┘  ▸ trainer.fit + validate

print(val_metrics)
```

### Zero Code Changes: Local ↔ Remote

PyTorch Lightning's `accelerator="auto"` is the cornerstone of the "no code
changes" design. On your laptop (CPU) it trains on CPU. On a `gpu_small` pod
(1× NVIDIA V100) it uses the GPU. Remove the `@ascend` decorator and the
script still works — no conditional logic needed.

### The Model

`SmallResNet` is a lightweight ResNet-style CNN defined at module level:

```python
class SmallResNet(pl.LightningModule):
    """Conv(1→32) → ResBlock(32) → Conv(32→64, stride=2)
    → ResBlock(64) → AdaptiveAvgPool → Linear(64→10)"""

    def __init__(self, lr: float = 1e-3):
        ...
```

`cloudpickle` captures the class automatically when serializing the decorated
function because `train_and_validate` references `SmallResNet` in its body.

### The Remote Function

```python
@ascend(
    node_type="gpu_small",
    timeout=3600,
    requirements=[
        "torch>=2.1",
        "pytorch-lightning>=2.0",
        "torchvision>=0.16",
        "torchmetrics>=1.0",
    ],
    git_check=False,
)
def train_and_validate(hparams: dict[str, Any]) -> dict[str, float]:
    ...
    trainer = pl.Trainer(
        max_epochs=hparams.get("max_epochs", 5),
        accelerator="auto",
        devices="auto",
        enable_checkpointing=False,
        logger=False,
    )
    trainer.fit(model, train_loader, val_loader)
    results = trainer.validate(model, val_loader)
    return {k: float(v) for k, v in results[0].items()}
```

Key choices:

- **`node_type="gpu_small"`** — maps to `Standard_NC6s_v3` (1× V100, 16 GB
  VRAM). See [GPU_SUPPORT.md](GPU_SUPPORT.md) for all available node types.
- **Data (MNIST, ~12 MB) is downloaded inside the pod** via
  `torchvision.datasets.MNIST(download=True)`.
- **`enable_checkpointing=False, logger=False`** — avoids writing files on
  the pod. Re-enable for real workloads.
- **Return value is `{k: float(v)}`** — explicitly converts metrics to plain
  floats for clean serialization.

### The Local Launcher

```python
if __name__ == "__main__":
    hparams = {"lr": 1e-3, "batch_size": 64, "max_epochs": 5}
    val_metrics = train_and_validate(hparams)
    print(val_metrics)
```

### Running the Example

```bash
# Install PyTorch locally (needed for both local and remote execution)
pip install torch pytorch-lightning torchvision torchmetrics

# Run locally on CPU (prototype)
python examples/lightning_mnist.py

# With @ascend active + .ascend.yaml configured → trains on GPU pod
python examples/lightning_mnist.py
```

---

## Serialization Caveats

Ascend serializes function arguments and return values using
[cloudpickle](https://github.com/cloudpipe/cloudpickle). While cloudpickle
handles most Python objects, some objects will **silently degrade** after a
round-trip — they serialize without error but behave incorrectly after
deserialization.

### Objects That Degrade Silently

| Object type | What breaks | Example |
|---|---|---|
| **Optuna `Trial`** | `._storage` disconnected; `suggest_*()` writes are lost | `examples/optuna_xgboost_compact.py` |
| **Database connections** | `._connection` / `._session` become stale | SQLAlchemy sessions, psycopg2 connections |
| **File handles** | File descriptor invalid after deserialization | Open files, sockets |
| **Threading primitives** | Lock/Event is a new, unrelated instance | `threading.Lock`, `threading.Event` |
| **Framework internals** | State tied to a local process/registry | Ray object refs, Spark contexts |

The dangerous pattern is that `cloudpickle.dumps()` succeeds and
`cloudpickle.loads()` also succeeds — no exception is raised. The
deserialized object *looks* correct but has broken internal state.

### Built-in Validation

Ascend now validates function arguments before serialization using
`ascend.serialization.validate_serialization()`. This helper:

1. **Rejects** truly unpicklable types (generators, coroutines) with a
   clear `SerializationError`.
2. **Warns** when an argument has attributes matching known non-portable
   patterns (`_storage`, `_connection`, `_session`, etc.) via a
   `UserWarning`.
3. **Verifies** that the type is preserved after a round-trip.

```python
from ascend.serialization import validate_serialization

# Raises SerializationError:
validate_serialization(my_generator, name="generator arg")

# Emits UserWarning:
validate_serialization(optuna_trial, name="trial arg")

# Passes silently:
validate_serialization({"lr": 0.01}, name="params dict")
```

### Best Practice: Keep the Serialization Boundary Simple

Pass only plain Python types to `@ascend`-decorated functions:

```python
# ✗ BAD: framework object crosses the wire
@ascend(cpu="2", memory="4Gi")
def objective(trial):           # ← Trial serialized, storage lost
    params = trial.suggest_*()
    return train(params)

# ✓ GOOD: extract values locally, pass plain dict
def objective(trial):
    params = {
        "lr": trial.suggest_float("lr", 1e-5, 1e-1),
        "depth": trial.suggest_int("depth", 3, 9),
    }
    return evaluate(params)     # ← only dict crosses the wire

@ascend(cpu="2", memory="4Gi")
def evaluate(params: dict) -> float:
    ...
```

---

## Writing Your Own

The Optuna example illustrates a general pattern that applies to any
framework with a **suggest → evaluate** loop:

| Framework | Local (orchestrator) | Remote (`@ascend`) |
|-----------|---------------------|--------------------|
| Optuna | `trial.suggest_*()` → `dict` | `train_and_score(params)` || PyTorch Lightning | `hparams` dict | `train_and_validate(hparams)` || Ray Tune | `config` dict from search space | `trainable(config)` |
| Hyperopt | `fmin` + `hp.choice/uniform` | `objective(params)` |
| Manual grid search | `itertools.product(...)` | `evaluate(combo)` |

### Guidelines

1. **Keep the serialization boundary simple.** Pass only plain Python
   types (`dict`, `list`, `float`, `int`, `str`) to the decorated
   function. Avoid framework-specific objects that may not survive
   cloudpickle serialization.

2. **Load data inside the pod.** Serializing large datasets through
   cloudpickle is slow and fragile. Read from cloud storage
   (Azure Blob, S3, GCS) or use built-in datasets for prototyping.

3. **Return simple results.** The decorated function should return a
   scalar metric or a small dict of metrics. Do not return large model
   objects unless you specifically need them.

4. **Set appropriate timeouts.** Each trial has its own timeout via the
   `@ascend(timeout=...)` parameter. Set it high enough for the slowest
   expected trial.

5. **List only remote dependencies in `requirements`.** The orchestration
   framework (Optuna, Ray Tune, etc.) runs locally and should be installed
   separately — it does not need to be in the pod's `requirements` list.

6. **Suppress Git clean-tree warnings if needed.** By default, Ascend
   warns when the Git working tree has uncommitted changes. During
   iterative development you can disable this check:

   ```python
   # Per-function:
   @ascend(cpu="2", memory="4Gi", git_check=False)
   def experiment(params):
       ...
   ```

   Or globally in `.ascend.yaml`:

   ```yaml
   git_check: false
   ```

   The decorator parameter takes precedence over the YAML value.
   Note: `project=True` always requires a clean Git tree regardless
   of the `git_check` setting.
