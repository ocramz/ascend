"""
Optuna + Ascend: Distributed Hyperparameter Tuning on Kubernetes
================================================================

This example demonstrates how to use Ascend with Optuna for hyperparameter
tuning, where each trial's expensive model training step runs remotely on a
Kubernetes cluster.

Architecture
------------

    ┌─────────────────────────┐
    │   Local machine         │
    │                         │
    │  Optuna study.optimize  │
    │    ↓                    │
    │  objective(trial)       │
    │    ├─ trial.suggest_*   │  ← Optuna picks hyperparams locally
    │    ├─ build params dict │
    │    └─ evaluate(params) ─┼──→  K8s pod (train + evaluate XGBoost)
    │         ↑               │         │
    │         └── accuracy ───┼─────────┘  result flows back
    │                         │
    │  print best trial       │
    └─────────────────────────┘

Key design decisions:

1. **Serialization boundary**: Optuna's ``Trial`` object is not serializable
   (it holds internal references to the study's storage backend). We solve
   this by calling ``trial.suggest_*()`` *locally* and passing only a plain
   ``dict`` of hyperparameter values to the remote function.

2. **Data loading in the pod**: The breast cancer dataset is loaded inside
   the remote function via scikit-learn. For real workloads you would read
   training data from cloud storage (e.g. Azure Blob, S3).

3. **Optuna is a local-only dependency**: It orchestrates trials on your
   machine and is *not* shipped to the pod. Only ``xgboost``, ``scikit-learn``,
   and ``numpy`` are installed remotely (via the ``requirements`` parameter).

Prerequisites
-------------

Install Optuna locally (it is not needed on the cluster)::

    pip install optuna

Ensure you have a valid ``.ascend.yaml`` configuration and Azure/AKS access.

Usage
-----

::

    python examples/optuna_xgboost.py

"""

from __future__ import annotations

from time import time

from ascend import ascend


# ---------------------------------------------------------------------------
# Remote function — runs on Kubernetes
# ---------------------------------------------------------------------------

@ascend(
    cpu="1",
    memory="1Gi",
    timeout=600,
    requirements=["xgboost>=2.0", "scikit-learn>=1.3", "numpy>=1.24"],
    git_check=False,  # Disable Git validation for this example (optional)
)
def evaluate_params(params: dict) -> float:
    """Train an XGBoost model with the given hyperparameters and return accuracy.

    This function executes on a remote Kubernetes pod. It loads the dataset,
    trains the model, and returns the validation accuracy as a plain float.

    Args:
        params: Dictionary of XGBoost hyperparameters (must be JSON-
                serializable so it survives cloudpickle round-tripping).

    Returns:
        Validation accuracy as a float in [0, 1].
    """
    import numpy as np
    import sklearn.datasets
    import sklearn.metrics
    from sklearn.model_selection import train_test_split
    import xgboost as xgb

    # Load dataset inside the pod
    data, target = sklearn.datasets.load_breast_cancer(return_X_y=True)
    train_x, valid_x, train_y, valid_y = train_test_split(
        data, target, test_size=0.25, random_state=42
    )

    dtrain = xgb.DMatrix(train_x, label=train_y)
    dvalid = xgb.DMatrix(valid_x, label=valid_y)

    bst = xgb.train(params, dtrain)
    preds = bst.predict(dvalid)
    pred_labels = np.rint(preds)
    accuracy = float(sklearn.metrics.accuracy_score(valid_y, pred_labels))
    return accuracy


# ---------------------------------------------------------------------------
# Local objective — runs on your machine
# ---------------------------------------------------------------------------

def objective(trial) -> float:
    """Optuna objective: sample hyperparameters locally, evaluate remotely.

    The ``trial.suggest_*`` calls run on your machine; the resulting plain
    ``dict`` is shipped to a K8s pod via :func:`evaluate_params`.

    Args:
        trial: An ``optuna.trial.Trial`` instance (not serializable).

    Returns:
        Validation accuracy (maximised by Optuna).
    """
    params: dict = {
        "verbosity": 0,
        "objective": "binary:logistic",
        "tree_method": "exact",
        "booster": trial.suggest_categorical("booster", ["gbtree", "gblinear", "dart"]),
        "lambda": trial.suggest_float("lambda", 1e-8, 1.0, log=True),
        "alpha": trial.suggest_float("alpha", 1e-8, 1.0, log=True),
        "subsample": trial.suggest_float("subsample", 0.2, 1.0),
    }

    # Tree / dart-specific parameters
    if params["booster"] in ("gbtree", "dart"):
        params["max_depth"] = trial.suggest_int("max_depth", 3, 9, step=2)
        params["eta"] = trial.suggest_float("eta", 1e-8, 1.0, log=True)

    # Evaluate remotely on Kubernetes
    accuracy = evaluate_params(params)
    return accuracy


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import optuna

    study = optuna.create_study(direction="maximize")

    # t0 = time()
    # study.optimize(objective, n_trials=10, timeout=3600)
    # t1 = time()
    # dt_serial = t1 - t0
    # print(f"\nFinished {len(study.trials)} trials in {dt_serial:.2f} seconds (serial execution).")

    t0 = time()
    study.optimize(objective, n_trials=10, timeout=3600, n_jobs=10)
    t1 = time()
    dt_parallel = t1 - t0

    print(f"\nFinished {len(study.trials)} trials in {dt_parallel:.2f} seconds (parallel execution with 10 jobs).")

    print(f"Best accuracy: {study.best_value:.4f}")
    print("Best hyperparameters:")
    for key, value in study.best_trial.params.items():
        print(f"  {key}: {value}")
