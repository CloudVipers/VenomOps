"""Statistics used in the methodology: Wilson intervals, exact McNemar test, bootstrap and precision/recall/F1.

Only the standard library is used, so results do not depend on the version of a numeric package.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence


def wilson(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float, float]:
    """Proportion and its Wilson score interval (95 % by default). ``n == 0`` gives (0, 0, 1)."""
    if n == 0:
        return 0.0, 0.0, 1.0
    if not 0 <= successes <= n:
        raise ValueError("successes must be between 0 and n")
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, centre - half), min(1.0, centre + half)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for paired binary outcomes.

    ``b``: pairs where only the first condition succeeded; ``c``: pairs where only the second did.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail: float = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def bootstrap_ci(
    values: Sequence[float],
    statistic: Callable[[Sequence[float]], float] = lambda v: sum(v) / len(v),
    *,
    resamples: int = 10_000,
    alpha: float = 0.05,
    seed: int = 20261123,
) -> tuple[float, float, float]:
    """Percentile bootstrap: (statistic, low, high). Deterministic for a given seed."""
    if not values:
        raise ValueError("bootstrap needs at least one value")
    rng = random.Random(seed)  # noqa: S311 - statistics, not cryptography
    n = len(values)
    stats = sorted(statistic([values[rng.randrange(n)] for _ in range(n)]) for _ in range(resamples))
    lo = stats[int(alpha / 2 * resamples)]
    hi = stats[min(resamples - 1, int((1 - alpha / 2) * resamples))]
    return statistic(values), lo, hi
