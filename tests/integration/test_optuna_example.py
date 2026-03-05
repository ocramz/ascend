"""Integration tests for the Optuna hyperparameter tuning example.

These tests validate that the ``evaluate_params`` function from
``examples/optuna_xgboost.py`` executes correctly on a real AKS cluster.
The first test exercises the decorated function directly (no Optuna
dependency required); the second runs a small Optuna study end-to-end.

The compact-example tests verify the known silent-failure mode where
the Trial object is serialized and shipped to the pod, causing
suggestions to be disconnected from the study's storage.
"""

import pytest


@pytest.mark.integration
class TestOptunaExample:
    """Integration tests for the Optuna + XGBoost example."""

    def test_evaluate_params_single_call(self, real_aks_cluster):
        """Validate that evaluate_params runs on K8s and returns a valid accuracy."""
        from examples.optuna_xgboost import evaluate_params

        params = {
            "verbosity": 0,
            "objective": "binary:logistic",
            "tree_method": "exact",
            "booster": "gbtree",
            "lambda": 1e-3,
            "alpha": 1e-3,
            "subsample": 0.8,
            "max_depth": 5,
            "eta": 0.1,
        }

        accuracy = evaluate_params(params)

        assert isinstance(accuracy, float)
        assert 0.0 <= accuracy <= 1.0
        # Breast cancer dataset with reasonable params should beat random
        assert accuracy > 0.8

    @pytest.mark.slow
    def test_optuna_study_two_trials(self, real_aks_cluster):
        """Run a minimal Optuna study (2 trials) end-to-end on AKS."""
        import optuna

        from examples.optuna_xgboost import objective

        # Suppress Optuna's verbose trial logging
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=2, timeout=1200)

        assert len(study.trials) == 2
        assert study.best_value > 0.5


@pytest.mark.integration
class TestCompactOptunaExample:
    """Integration tests for the compact Optuna example (Trial serialization).

    The compact example ships the Trial object to the pod via cloudpickle.
    This causes trial.suggest_*() calls on the pod to be disconnected from
    the study's storage — the study never learns what hyperparameters were
    used.  These tests document that failure mode.
    """

    @pytest.mark.xfail(
        reason=(
            "Trial storage disconnected after cloudpickle round-trip: "
            "study.best_trial.params is empty because suggest_*() calls "
            "on the pod write to a cloned, disconnected storage backend."
        ),
        strict=False,
    )
    @pytest.mark.slow
    def test_compact_optuna_trial_params_preserved(self, real_aks_cluster):
        """Run compact example and verify that best_trial.params is populated.

        This test is expected to FAIL (xfail) because the Trial object
        loses its storage connection after cloudpickle serialization.
        """
        import optuna

        from examples.optuna_xgboost_compact import objective

        optuna.logging.set_verbosity(optuna.logging.WARNING)

        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=3, timeout=1200)

        assert len(study.trials) == 3

        # Each trial should have returned a valid accuracy
        for trial in study.trials:
            assert trial.value is not None
            assert isinstance(trial.value, float)
            assert 0.0 <= trial.value <= 1.0

        # This is the critical assertion — it will fail because
        # suggest_*() calls happened on the deserialized (disconnected)
        # trial inside the pod.
        best = study.best_trial
        assert len(best.params) > 0, (
            "study.best_trial.params is empty — the Trial object was "
            "serialized via cloudpickle and its storage connection was "
            "lost.  Suggestions made on the pod were not recorded."
        )
        expected_keys = {"booster", "lambda", "alpha", "subsample"}
        assert expected_keys.issubset(set(best.params.keys())), (
            f"Expected at least {expected_keys} in best_trial.params, "
            f"got: {set(best.params.keys())}"
        )
