"""Sample functions for integration testing."""


def add(a, b):
    """Simple addition."""
    return a + b


def multiply(a, b):
    """Simple multiplication."""
    return a * b


def concatenate(*strings):
    """Concatenate strings."""
    return "".join(strings)


def identity(x):
    """Return input unchanged."""
    return x
