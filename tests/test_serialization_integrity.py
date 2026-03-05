"""Tests for cloudpickle serialization integrity.

These tests verify that cloudpickle round-tripping preserves object
semantics, and that objects with non-serializable internal state
(database connections, file handles, locks, etc.) are detected by the
validation helpers in ``ascend.serialization``.
"""

from __future__ import annotations

import dataclasses
import threading
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock

import cloudpickle
import pytest

from ascend.serialization import serialize, deserialize, validate_serialization
from ascend.utils.errors import SerializationError


# ── helpers ──────────────────────────────────────────────────────────


def _roundtrip(obj: Any) -> Any:
    """Serialize then deserialize *obj* via cloudpickle."""
    return deserialize(serialize(obj))


# ── plain values ─────────────────────────────────────────────────────


class TestPlainValueRoundTrip:
    """Values that must survive a serialize → deserialize cycle."""

    @pytest.mark.parametrize(
        "value",
        [
            42,
            3.14,
            "hello",
            b"bytes",
            True,
            None,
            [1, 2, 3],
            {"a": 1, "b": [2, 3]},
            (1, "two", 3.0),
            {1, 2, 3},
            {"nested": {"dict": [1, 2, {"deep": True}]}},
        ],
        ids=lambda v: type(v).__name__,
    )
    def test_plain_values_roundtrip(self, value):
        assert _roundtrip(value) == value


# ── dataclasses ──────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclasses.dataclass
class Config:
    name: str
    values: list[int]
    nested: dict[str, Any] = dataclasses.field(default_factory=dict)


class TestDataclassRoundTrip:
    def test_frozen_dataclass(self):
        p = Point(1.0, 2.0)
        restored = _roundtrip(p)
        assert restored == p
        assert type(restored) is Point

    def test_mutable_dataclass(self):
        c = Config(name="test", values=[1, 2, 3], nested={"k": "v"})
        restored = _roundtrip(c)
        assert restored == c
        assert type(restored) is Config


# ── callables ────────────────────────────────────────────────────────


def _add(a: int, b: int) -> int:
    return a + b


class TestCallableRoundTrip:
    def test_function(self):
        f = _roundtrip(_add)
        assert f(2, 3) == 5

    def test_lambda(self):
        sq = lambda x: x * x  # noqa: E731
        f = _roundtrip(sq)
        assert f(4) == 16

    def test_closure(self):
        multiplier = 10

        def scale(x):
            return x * multiplier

        f = _roundtrip(scale)
        assert f(3) == 30

    def test_closure_captures_mutable_state(self):
        """Captured mutable state is *snapshot* at serialization time."""
        counter = [0]

        def inc():
            counter[0] += 1
            return counter[0]

        inc()  # counter == [1]
        f = _roundtrip(inc)
        # The deserialized closure has a *copy* of counter == [1]; mutations
        # are independent from the original.
        assert f() == 2  # its own [1] -> [2]
        assert counter == [1]  # original unchanged


# ── objects with __slots__ ───────────────────────────────────────────


class SlottedPoint:
    __slots__ = ("x", "y")

    def __init__(self, x: float, y: float):
        self.x = x
        self.y = y


class TestSlottedRoundTrip:
    def test_slotted_object(self):
        p = SlottedPoint(1.0, 2.0)
        restored = _roundtrip(p)
        assert restored.x == 1.0
        assert restored.y == 2.0


# ── stateful objects that degrade silently ───────────────────────────


class FakeDBConnection:
    """Simulates an object with internal database-like state."""

    def __init__(self, dsn: str = "localhost:5432"):
        self.dsn = dsn
        self._connection = MagicMock(name="connection")
        self._connection.is_connected = True

    def query(self, sql: str) -> str:
        if not self._connection.is_connected:
            raise RuntimeError("Connection closed")
        return f"result of {sql}"


class ObjectWithLock:
    """Object whose internal lock cannot be pickled by cloudpickle."""

    def __init__(self):
        self.lock = threading.Lock()
        self.value = 0

    def increment(self):
        with self.lock:
            self.value += 1


class ObjectWithLockRef:
    """Object that holds a *reference* to a lock but not the lock itself.

    The lock-holder attribute name is not in _SUSPICIOUS_ATTRS so this
    exercises the case where cloudpickle fails hard.
    """

    def __init__(self):
        self._lock_holder = threading.Lock()
        self.value = 0


class ObjectWithFileHandle:
    """Object holding an open file-like handle."""

    def __init__(self):
        self._buffer = BytesIO(b"hello")
        self.name = "test"

    def read_all(self) -> bytes:
        self._buffer.seek(0)
        return self._buffer.read()


class ObjectWithStorage:
    """Simulates an Optuna-Trial-like object where ._storage is critical."""

    def __init__(self, storage: Any):
        self._storage = storage
        self.trial_id = 42

    def suggest_float(self, name: str, low: float, high: float) -> float:
        # In real Optuna, this would write to ._storage
        return self._storage.record(name, low, high)


class _FakeStorage:
    """In-memory storage mock that tracks calls."""

    def __init__(self):
        self.calls: list[tuple] = []

    def record(self, name: str, low: float, high: float) -> float:
        self.calls.append((name, low, high))
        return (low + high) / 2


class TestStatefulObjectDegradation:
    """Characterise how cloudpickle handles objects with internal state.

    These tests document the *failure mode*: cloudpickle serializes the
    object successfully, but the deserialized copy is functionally broken
    or disconnected from the original data source.
    """

    def test_db_connection_degrades(self):
        """A mock DB connection round-trips but the mock is a new instance."""
        obj = FakeDBConnection()
        restored = _roundtrip(obj)
        # Structurally it looks fine
        assert restored.dsn == "localhost:5432"
        # But the mock connection is a *separate* object — mutations on the
        # restored copy are invisible to the original.
        assert restored._connection is not obj._connection

    def test_lock_cannot_be_pickled(self):
        """threading.Lock is not picklable — cloudpickle raises."""
        obj = ObjectWithLock()
        obj.increment()
        with pytest.raises(SerializationError, match="cannot pickle"):
            _roundtrip(obj)

    def test_file_handle_contents_preserved(self):
        obj = ObjectWithFileHandle()
        restored = _roundtrip(obj)
        # BytesIO round-trips, but if it were a real file descriptor it
        # would be invalid after deserialization.
        assert restored.read_all() == b"hello"

    def test_storage_disconnected_after_roundtrip(self):
        """The canonical Trial-like failure: storage is cloned, not shared."""
        storage = _FakeStorage()
        obj = ObjectWithStorage(storage)

        restored = _roundtrip(obj)

        # The restored object has its own copy of _storage
        restored.suggest_float("lr", 0.001, 0.1)

        # The original storage has NO record of the call
        assert len(storage.calls) == 0
        # But the restored copy's storage does
        assert len(restored._storage.calls) == 1


# ── validate_serialization ───────────────────────────────────────────


class TestValidateSerialization:
    """Test the validate_serialization helper."""

    def test_plain_values_pass(self):
        for val in [42, "hello", [1, 2], {"a": 1}, (1, 2)]:
            validate_serialization(val)  # should not raise

    def test_dataclass_passes(self):
        validate_serialization(Point(1.0, 2.0))

    def test_function_passes(self):
        validate_serialization(_add)

    def test_lambda_passes(self):
        validate_serialization(lambda x: x)

    def test_warns_on_storage_attribute(self):
        """Objects with _storage attribute should trigger a warning."""
        storage = _FakeStorage()
        obj = ObjectWithStorage(storage)
        with pytest.warns(UserWarning, match="serialization"):
            validate_serialization(obj, name="trial-like object")

    def test_warns_on_connection_attribute(self):
        """Objects with _connection attribute should trigger a warning."""
        obj = FakeDBConnection()
        with pytest.warns(UserWarning, match="serialization"):
            validate_serialization(obj, name="db-connected object")

    def test_generator_raises_serialization_error(self):
        def gen():
            yield 1

        with pytest.raises(SerializationError, match="generator"):
            validate_serialization(gen(), name="generator")

    def test_validates_type_preserved(self):
        """Round-trip must preserve type."""
        validate_serialization(Point(1.0, 2.0))  # should not raise
