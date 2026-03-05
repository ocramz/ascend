"""Functions requiring external dependencies."""


def numpy_sum(data):
    """Sum using numpy."""
    import numpy as np

    return np.array(data).sum()


def pandas_describe(data):
    """Create DataFrame and describe."""
    import pandas as pd

    df = pd.DataFrame(data)
    return df.describe().to_dict()
