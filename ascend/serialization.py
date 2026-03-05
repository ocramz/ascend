"""Serialization utilities using cloudpickle.

Provides ``serialize`` / ``deserialize`` wrappers around cloudpickle and a
``validate_serialization`` helper that detects objects whose internal state
is unlikely to survive a round-trip (database connections, file handles,
storage backends, etc.).
"""

from __future__ import annotations

import inspect
import warnings
from typing import Any

import cloudpickle

from .utils.errors import SerializationError

# Attribute names that signal non-portable internal state.  Objects
# holding any of these are likely to degrade silently after round-tripping.
_SUSPICIOUS_ATTRS: frozenset[str] = frozenset({
    "_storage",
    "storage",
    "_connection",
    "connection",
    "_conn",
    "_session",
    "session",
    "_engine",
    "engine",
    "_socket",
    "_lock",
    "_cursor",
    "_pool",
    "_file",
    "_fd",
    "_stream",
})

# Types whose instances are safe despite having storage/engine attrs
# (e.g. our own config objects).  Checked by fully-qualified name.
_SAFE_TYPE_PREFIXES: tuple[str, ...] = (
    "ascend.",
)


def serialize(obj: Any, *, by_value: bool = False) -> bytes:
    """Serialize a Python object using cloudpickle.

    Args:
        obj: The object to serialize.
        by_value: If ``True`` and *obj* is a function or callable, temporarily
            register its defining module for by-value pickling.  This ensures
            that module-level classes and functions referenced by *obj* are
            serialized inline rather than by import-path reference — critical
            when the module (e.g. a user script) will not be importable on
            the remote pod.

    Raises:
        SerializationError: If cloudpickle cannot pickle *obj*.
    """
    registered_module = None
    try:
        if by_value:
            import importlib
            import types

            mod_name = getattr(obj, "__module__", None)
            if mod_name and mod_name != "__main__":
                try:
                    mod = importlib.import_module(mod_name)
                    if isinstance(mod, types.ModuleType):
                        cloudpickle.register_pickle_by_value(mod)
                        registered_module = mod
                except ImportError:
                    pass  # Module not importable — cloudpickle will handle it

        return cloudpickle.dumps(obj)
    except Exception as exc:
        raise SerializationError(
            f"Failed to serialize object of type {type(obj).__name__}: {exc}"
        ) from exc
    finally:
        if registered_module is not None:
            try:
                cloudpickle.unregister_pickle_by_value(registered_module)
            except Exception:
                pass  # Best-effort cleanup


def deserialize(data: bytes) -> Any:
    """Deserialize a Python object using cloudpickle.

    Raises:
        SerializationError: If cloudpickle cannot unpickle *data*.
    """
    try:
        return cloudpickle.loads(data)
    except Exception as exc:
        raise SerializationError(
            f"Failed to deserialize data ({len(data)} bytes): {exc}"
        ) from exc


def validate_serialization(
    obj: Any,
    *,
    name: str = "object",
) -> None:
    """Validate that *obj* can faithfully survive a cloudpickle round-trip.

    Checks performed:

    1. **Truly unpicklable objects** (generators, coroutines) raise
       ``SerializationError`` immediately — cloudpickle will fail on these
       anyway, but giving a clear message is better.
    2. **Round-trip type preservation** — ``type(restored)`` must match
       ``type(obj)`` after ``dumps`` → ``loads``.
    3. **Suspicious attributes** — if *obj* has instance attributes whose
       names match known non-portable patterns (``_storage``, ``_connection``,
       etc.), a ``UserWarning`` is emitted.  The object *can* still be
       serialized, but its behaviour after deserialization is likely to be
       degraded.

    Args:
        obj: The object to validate.
        name: Human-readable label used in warning / error messages.

    Raises:
        SerializationError: If *obj* is fundamentally non-serializable or
            the round-trip changes its type.
    """
    # ── 1. Reject fundamentally non-serializable types ──────────────
    if inspect.isgenerator(obj):
        raise SerializationError(
            f"Cannot serialize {name}: generator objects are not "
            "serializable.  Consume the generator into a list first."
        )
    if inspect.isgeneratorfunction(obj):
        raise SerializationError(
            f"Cannot serialize {name}: generator functions are not "
            "faithfully serializable — the yielded state will be lost."
        )
    if inspect.iscoroutine(obj):
        raise SerializationError(
            f"Cannot serialize {name}: coroutine objects are not "
            "serializable.  Await the coroutine first."
        )

    # ── 2. Round-trip type preservation ─────────────────────────────
    try:
        pickled = cloudpickle.dumps(obj)
    except Exception as exc:
        raise SerializationError(
            f"Cannot serialize {name} (type {type(obj).__name__}): {exc}"
        ) from exc

    try:
        restored = cloudpickle.loads(pickled)
    except Exception as exc:
        raise SerializationError(
            f"Serialized {name} but could not deserialize it: {exc}"
        ) from exc

    if type(restored) is not type(obj):
        raise SerializationError(
            f"Type changed after round-trip for {name}: "
            f"{type(obj).__name__} → {type(restored).__name__}"
        )

    # ── 3. Heuristic: suspicious internal-state attributes ─────────
    fqn = f"{type(obj).__module__}.{type(obj).__qualname__}"
    if any(fqn.startswith(prefix) for prefix in _SAFE_TYPE_PREFIXES):
        return

    attrs = set(getattr(obj, "__dict__", {}).keys())
    bad = attrs & _SUSPICIOUS_ATTRS
    if bad:
        warnings.warn(
            f"Potential serialization issue with {name} "
            f"(type {type(obj).__name__}): attributes {sorted(bad)} "
            f"suggest internal state that will not survive cloudpickle "
            f"round-tripping.  The deserialized object may be silently "
            f"degraded.  Consider extracting the needed values into plain "
            f"Python types before serialization.",
            UserWarning,
            stacklevel=2,
        )
