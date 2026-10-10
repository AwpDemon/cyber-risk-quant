"""Parameter registry: every number the model uses, with its provenance.

``config/parameters.yaml`` is the single place where model inputs live. Each
entry carries a distribution plus a ``basis``:

* ``sourced``    - taken directly from a cited public study (``sources`` required)
* ``derived``    - computed by this repo from public data (``sources`` required)
* ``assumption`` - analyst judgement; MUST be a range (not a fixed value) so the
                   uncertainty is carried into the simulation and the tornado chart.

``validate_registry`` enforces those rules and is run by the test-suite, so a
parameter cannot silently lose its citation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path

import numpy as np
import yaml

from .distributions import Distribution, Fixed, from_spec

BASES = {"sourced", "derived", "assumption"}


@dataclass(frozen=True)
class Parameter:
    name: str
    dist: Distribution
    basis: str
    unit: str
    description: str
    sources: tuple[str, ...] = ()
    note: str = ""
    spec: Mapping = field(default_factory=dict)

    def describe_range(self) -> str:
        s = self.spec
        kind = s["dist"]
        if kind == "fixed":
            return f"{s['value']:g}"
        if kind == "pert":
            return f"PERT({s['low']:g}, {s['mode']:g}, {s['high']:g})"
        if kind == "uniform":
            return f"U({s['low']:g}, {s['high']:g})"
        if kind in ("lognormal", "lognormal90"):
            return f"LN 90% CI [{s['p05']:,.4g}, {s['p95']:,.4g}]"
        if kind == "lognormal_median_mean":
            return f"LN median {s['median']:,.4g}, mean {s['mean']:,.4g}"
        return str(s)


def _config_dir() -> Path:
    """Locate ``config/``: repo checkout first, then the copy shipped in the package."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "config"
        if (cand / "parameters.yaml").exists():
            return cand
    return Path(str(resources.files("crq") / "config"))


CONFIG_DIR = _config_dir()


@lru_cache(maxsize=16)
def _load_yaml_cached(name: str) -> str:
    return (CONFIG_DIR / name).read_text(encoding="utf-8")


def load_yaml(name: str) -> dict:
    """Parse a config file (text cached; a fresh dict is returned so callers may mutate it)."""
    return yaml.safe_load(_load_yaml_cached(name))


def load_sources() -> dict[str, dict]:
    return load_yaml("sources.yaml")["sources"]


def load_registry(path: Path | None = None) -> dict[str, Parameter]:
    raw = yaml.safe_load(open(path, encoding="utf-8")) if path else load_yaml("parameters.yaml")
    reg: dict[str, Parameter] = {}
    for name, entry in raw["parameters"].items():
        spec = {k: v for k, v in entry.items() if k in {"dist", "value", "low", "mode", "high", "p05", "p95", "median", "mean", "lamb"}}
        reg[name] = Parameter(
            name=name,
            dist=from_spec(spec),
            basis=entry["basis"],
            unit=entry.get("unit", ""),
            description=entry.get("description", ""),
            sources=tuple(entry.get("sources", ())),
            note=entry.get("note", ""),
            spec=spec,
        )
    return reg


def validate_registry(reg: Mapping[str, Parameter], sources: Mapping[str, dict]) -> list[str]:
    """Return a list of provenance problems (empty list = registry is clean)."""
    problems = []
    for p in reg.values():
        if p.basis not in BASES:
            problems.append(f"{p.name}: unknown basis {p.basis!r}")
        if p.basis in ("sourced", "derived") and not p.sources:
            problems.append(f"{p.name}: basis={p.basis} but no sources listed")
        if p.basis == "assumption" and isinstance(p.dist, Fixed):
            problems.append(f"{p.name}: assumptions must be ranges, not fixed values")
        for s in p.sources:
            if s not in sources:
                problems.append(f"{p.name}: source id {s!r} not in sources.yaml")
        if not p.description:
            problems.append(f"{p.name}: missing description")
    return problems


class ParameterSet:
    """Samples every registered parameter, with optional overrides.

    ``overrides`` maps a parameter name to a fixed float; it is how the tornado
    analysis pins one input at its P10 or P90 while everything else varies.
    """

    def __init__(self, registry: Mapping[str, Parameter] | None = None, overrides: Mapping[str, float] | None = None):
        self.registry = dict(registry or load_registry())
        self.overrides = dict(overrides or {})
        unknown = set(self.overrides) - set(self.registry)
        if unknown:
            raise KeyError(f"override for unknown parameter(s): {sorted(unknown)}")

    def sample(self, rng: np.random.Generator, n: int) -> dict[str, np.ndarray]:
        # Draw in a fixed (sorted) order so a given seed always maps to the same
        # draws, and so an override does not shift the random stream of others
        # (we still consume the draws for overridden parameters).
        out = {}
        for name in sorted(self.registry):
            draws = self.registry[name].dist.sample(rng, n)
            if name in self.overrides:
                draws = np.full(n, float(self.overrides[name]))
            out[name] = draws
        return out

    def central(self, name: str) -> float:
        if name in self.overrides:
            return float(self.overrides[name])
        return self.registry[name].dist.central()
