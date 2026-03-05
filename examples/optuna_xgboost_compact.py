"""
Optuna + Ascend: Distributed Hyperparameter Tuning on Kubernetes
================================================================

**WARNING — Known silent-failure mode**

This example serializes the Optuna ``Trial`` object via ``cloudpickle``
and ships it to a remote Kubernetes pod.  While cloudpickle can pickle
the Trial without raising an error, the deserialized copy has a
**disconnected storage backend**: ``trial.suggest_*()`` calls execute on
the pod, but the suggestions are never recorded in the study's storage.
As a result, ``study.best_trial.params`` may be empty or incorrect.

**Recommended alternative**: Use the non-compact version in
``examples/optuna_xgboost.py``, which calls ``trial.suggest_*()``
locally and passes only a plain ``dict`` to the remote function.
See that file's docstring for the architecture diagram.

This file is preserved as a **demonstration of the failure mode** and
as a test case for the serialization validation mechanisms in
``ascend.serialization.validate_serialization``.

Architecture (broken)
---------------------

::

    Local machine                         K8s pod
    ─────────────                         ───────
    study.optimize(objective, ...)
      └─ objective(trial)                   ← Trial serialized via cloudpickle
           └─ @ascend wrapper
                ├─ cloudpickle.dumps(trial)
                └─ upload to pod ──────────→ cloudpickle.loads(trial)
                                              trial.suggest_*()  ← writes to
                                                 disconnected storage!
                                              return accuracy
                accuracy ←─────────────────── OK, but study has no params

Prerequisites
-------------

Install Optuna locally ::

    pip install optuna

Ensure you have a valid ``.ascend.yaml`` configuration and Azure/AKS access.

Usage
-----

::

    python examples/optuna_xgboost_compact.py

"""

from __future__ import annotations

from time import time

import optuna

from ascend import ascend


# ---------------------------------------------------------------------------
# Objective — the entire trial (sampling + training) runs on Kubernetes
# ---------------------------------------------------------------------------

@ascend(
    cpu="2",
    memory="4Gi",
    timeout=600,
    requirements=["optuna>=3.0", "xgboost>=2.0", "scikit-learn>=1.3", "numpy>=1.24"],
    git_check=False,  # Disable Git validation for this example (optional)
)
def objective(trial) -> float:
    """Optuna objective: sample hyperparameters and evaluate — all on a K8s pod.

    The ``trial`` object is serialized and shipped to a remote pod so that
    ``trial.suggest_*`` calls, model training, and evaluation all happen
    remotely.

    Args:
        trial: An ``optuna.trial.Trial`` instance (serialized via cloudpickle).

    Returns:
        Validation accuracy (maximised by Optuna).
    """
    import numpy as np
    import sklearn.datasets
    import sklearn.metrics
    from sklearn.model_selection import train_test_split
    import xgboost as xgb

    # --- Sample hyperparameters (runs on the pod) ---
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

    # --- Train and evaluate (runs on the pod) ---
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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    

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
