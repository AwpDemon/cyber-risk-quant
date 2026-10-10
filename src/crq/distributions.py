"""Probability distributions used by the FAIR engine.

Every uncertain input in ``config/parameters.yaml`` is described by one of the
specs below. Each spec knows how to draw ``n`` samples from a NumPy
``Generator`` and how to report a central value and a P10/P90 range (used by
the tornado sensitivity analysis).

Why these distributions:

* **PERT** (a re-scaled Beta) is the FAIR-community default for expert ranges
  ("min / most likely / max"): bounded, skewed towards the mode, and smoother
  than a triangular distribution.
* **Lognormal from a 90% interval** is the natural choice for money and time
  (strictly positive, right-skewed, multiplicative errors). Analysts are better
  at giving a 5th-95th percentile range than a mu/sigma pair, so we accept the
  range and solve for mu/sigma.
* **Uniform** for "somewhere between a and b, no idea where".
* **Fixed** for values taken directly from a source with no stated spread.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

Z90 = stats.norm.ppf(0.95)  # 1.6449: half-width of a central 90% interval in z units


@dataclass(frozen=True)
class Fixed:
    value: float

    def sample(self, rng: np.random.Generator, n: int) -> np.ndarray:
        return np.full(n, float(self.value))

    def central(self) -> float:
        return float(self.value)

    def ppf(self, q: float) -> float:
        return float(self.value)


@dataclass(frozen=True)
class Uniform:
    low: float
    high: float

    def __post_init__(self) -> None:
        if not self.low <= self.high:
            raise ValueError(f"Uniform needs low <= high, got {self.low}, {self.high}")

    def sample(self, rng: np.random.Generator, n: int) -> np.ndarray:
        return rng.uniform(self.low, self.high, n)

    def central(self) -> float:
        return 0.5 * (self.low + self.high)

    def ppf(self, q: float) -> float:
        return self.low + q * (self.high - self.low)


@dataclass(frozen=True)
class PERT:
    """Beta-PERT on [low, high] with the given mode.

    Shape parameters: alpha = 1 + lamb*(mode-low)/(high-low),
    beta = 1 + lamb*(high-mode)/(high-low); lamb=4 is the classic PERT weight,
    which makes the mean (low + 4*mode + high)/6.
    """

    low: float
    mode: float
    high: float
    lamb: float = 4.0

    def __post_init__(self) -> None:
        if not (self.low <= self.mode <= self.high) or self.low == self.high:
            raise ValueError(f"PERT needs low <= mode <= high and low < high: {self}")

    @property
    def _ab(self) -> tuple[float, float]:
        span = self.high - self.low
        a = 1 + self.lamb * (self.mode - self.low) / span
        b = 1 + self.lamb * (self.high - self.mode) / span
        return a, b

    def sample(self, rng: np.random.Generator, n: int) -> np.ndarray:
        a, b = self._ab
        return self.low + (self.high - self.low) * rng.beta(a, b, n)

    def central(self) -> float:
        return (self.low + self.lamb * self.mode + self.high) / (self.lamb + 2)

    def ppf(self, q: float) -> float:
        a, b = self._ab
        return float(self.low + (self.high - self.low) * stats.beta.ppf(q, a, b))


@dataclass(frozen=True)
class LogNormal90:
    """Lognormal specified by its 5th and 95th percentiles (a 90% interval)."""

    p05: float
    p95: float

    def __post_init__(self) -> None:
        if not 0 < self.p05 < self.p95:
            raise ValueError(f"LogNormal90 needs 0 < p05 < p95: {self}")

    @property
    def mu(self) -> float:
        return float(0.5 * (np.log(self.p05) + np.log(self.p95)))

    @property
    def sigma(self) -> float:
        return float((np.log(self.p95) - np.log(self.p05)) / (2 * Z90))

    def sample(self, rng: np.random.Generator, n: int) -> np.ndarray:
        return rng.lognormal(self.mu, self.sigma, n)

    def central(self) -> float:
        """Median (not mean): the mean of a wide lognormal is dominated by its tail."""
        return float(np.exp(self.mu))

    def mean(self) -> float:
        return float(np.exp(self.mu + 0.5 * self.sigma**2))

    def ppf(self, q: float) -> float:
        return float(np.exp(self.mu + self.sigma * stats.norm.ppf(q)))


@dataclass(frozen=True)
class LogNormalMedianMean:
    """Lognormal pinned by a published median and a published mean.

    For a lognormal, mean = median * exp(sigma^2 / 2), so
    sigma = sqrt(2 * ln(mean / median)). This lets us combine two public
    statistics from different reports (e.g. a median from one study and a
    mean from another) into a single severity curve.
    """

    median: float
    mean_: float

    def __post_init__(self) -> None:
        if not 0 < self.median < self.mean_:
            raise ValueError(f"needs 0 < median < mean: {self}")

    @property
    def mu(self) -> float:
        return float(np.log(self.median))

    @property
    def sigma(self) -> float:
        return float(np.sqrt(2 * np.log(self.mean_ / self.median)))

    def sample(self, rng: np.random.Generator, n: int) -> np.ndarray:
        return rng.lognormal(self.mu, self.sigma, n)

    def central(self) -> float:
        return float(self.median)

    def mean(self) -> float:
        return float(self.mean_)

    def ppf(self, q: float) -> float:
        return float(np.exp(self.mu + self.sigma * stats.norm.ppf(q)))


Distribution = Fixed | Uniform | PERT | LogNormal90 | LogNormalMedianMean


def from_spec(spec: dict) -> Distribution:
    """Build a distribution from a YAML dict such as ``{dist: pert, low: 1, mode: 2, high: 4}``."""
    kind = spec["dist"].lower()
    if kind == "fixed":
        return Fixed(spec["value"])
    if kind == "uniform":
        return Uniform(spec["low"], spec["high"])
    if kind == "pert":
        return PERT(spec["low"], spec["mode"], spec["high"], spec.get("lamb", 4.0))
    if kind in ("lognormal", "lognormal90"):
        return LogNormal90(spec["p05"], spec["p95"])
    if kind == "lognormal_median_mean":
        return LogNormalMedianMean(spec["median"], spec["mean"])
    raise ValueError(f"unknown distribution kind: {kind!r}")
