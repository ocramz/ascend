"""Tests for Optuna Trial serialization via cloudpickle.

Demonstrates and guards against the silent failure mode where a
``Trial`` object round-trips through cloudpickle but loses its
connection to the study's storage backend.  Suggestions made on the
deserialized trial are invisible to the study.
"""

from __future__ import annotations

import cloudpickle
import pytest

import optuna


# Silence Optuna's internal logging during tests
optuna.logging.set_verbosity(optuna.logging.WARNING)


def _roundtrip(obj):
    """Serialize → deserialize via cloudpickle."""
    return cloudpickle.loads(cloudpickle.dumps(obj))


# ── Trial round-trip degradation ─────────────────────────────────────


class TestTrialRoundTripDegradation:
    """Characterise how cloudpickle handles Optuna Trial objects.

    These tests document the *silent failure*: the trial serializes
    without error, the deserialized copy can call ``suggest_*``, but
    the suggestions are never recorded in the study.
    """

    def _make_trial(self) -> tuple[optuna.Study, optuna.trial.Trial]:
        study = optuna.create_study(direction="maximize")
        trial = study.ask()
        return study, trial

    def test_trial_serializes_without_error(self):
        """cloudpickle.dumps(trial) does NOT raise — that's the trap."""
        _, trial = self._make_trial()
        data = cloudpickle.dumps(trial)
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_trial_deserializes_without_error(self):
        """cloudpickle.loads succeeds — the deserialized trial looks valid."""
        _, trial = self._make_trial()
        restored = _roundtrip(trial)
        assert restored.number == trial.number

    @pytest.mark.parametrize(
        "suggest_call",
        [
            lambda t: t.suggest_float("lr", 1e-5, 1e-1, log=True),
            lambda t: t.suggest_int("n_layers", 1, 5),
            lambda t: t.suggest_categorical("optimizer", ["adam", "sgd"]),
        ],
        ids=["suggest_float", "suggest_int", "suggest_categorical"],
    )
    def test_suggestions_on_deserialized_trial_disconnected(self, suggest_call):
        """Suggestions on a deserialized trial are NOT reflected in the study.

        This is the core bug: the study never learns what parameters were
        chosen, so ``study.best_trial.params`` is empty or wrong.
        """
        study, trial = self._make_trial()

        restored_trial = _roundtrip(trial)

        # The suggest call on the deserialized trial may succeed or raise
        # depending on the Optuna version and storage state. Either way,
        # the study should NOT have recorded the suggestion.
        try:
            suggest_call(restored_trial)
        except Exception:
            # Some Optuna versions raise because the storage is disconnected.
            # This is actually *better* than silent success.
            pass

        # The study's storage has NO record of the parameter suggestion
        # made on the deserialized trial.
        original_trial_params = study.trials[0].params if study.trials else {}
        # The params dict should be empty because the only suggest_* call
        # was on the disconnected (deserialized) trial.
        assert original_trial_params == {}, (
            "Expected study.trials[0].params to be empty because suggest_*() "
            "was called only on the deserialized trial, but got: "
            f"{original_trial_params}"
        )


# ── FrozenTrial round-trips correctly ───────────────────────────────


class TestFrozenTrialRoundTrip:
    """``FrozenTrial`` is a read-only snapshot that round-trips cleanly."""

    def test_frozen_trial_preserves_params(self):
        study = optuna.create_study(direction="maximize")

        def objective(trial):
            x = trial.suggest_float("x", -10, 10)
            return -(x ** 2)

        study.optimize(objective, n_trials=3)

        for original in study.trials:
            restored = _roundtrip(original)
            assert restored.params == original.params
            assert restored.values == original.values
            assert restored.number == original.number
            assert type(restored) is type(original)

    def test_frozen_trial_best_trial_params(self):
        study = optuna.create_study(direction="minimize")

        def objective(trial):
            x = trial.suggest_float("x", -10, 10)
            y = trial.suggest_int("y", 0, 5)
            return x ** 2 + y

        study.optimize(objective, n_trials=5)

        best = study.best_trial
        restored = _roundtrip(best)
        assert restored.params == best.params
        assert "x" in restored.params
        assert "y" in restored.params


# ── Decorator serialize path ────────────────────────────────────────


class TestDecoratorSerializePath:
    """The decorator's serialize((args, kwargs)) silently succeeds for Trial."""

    def test_serialize_trial_args_no_error(self):
        """Proves the bug is *silent*: no exception, no warning."""
        from ascend.serialization import serialize, deserialize

        study = optuna.create_study()
        trial = study.ask()

        # This is exactly what the decorator does at line 128:
        #   serialized_args = serialize((args, kwargs))
        args = (trial,)
        kwargs = {}
        serialized = serialize((args, kwargs))

        # Deserialize succeeds
        restored_args, restored_kwargs = deserialize(serialized)
        assert len(restored_args) == 1
        # The trial looks fine on the surface
        assert restored_args[0].number == trial.number


# ── validate_serialization catches Trial ─────────────────────────────


class TestValidateSerializationCatchesTrial:
    """The validation helper should warn when a Trial is serialized."""

    def test_warns_on_trial_object(self):
        from ascend.serialization import validate_serialization

        study = optuna.create_study()
        trial = study.ask()

        with pytest.warns(UserWarning, match="serialization"):
            validate_serialization(trial, name="optuna Trial")
