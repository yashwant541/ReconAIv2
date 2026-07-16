from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import yaml


@dataclass(frozen=True, slots=True)
class MatchingConfig:
    minimum_confidence: Decimal = Decimal("0.65")
    tolerance: Decimal = Decimal("0.01")
    weights: dict[str, Decimal] | None = None
    assignment: str = "hungarian"

    def __post_init__(self) -> None:
        if self.weights is None:
            object.__setattr__(self, "weights", {"amount": Decimal(".4"), "text": Decimal(".25"), "date": Decimal(".15"), "ontology": Decimal(".2")})


def load_config(path: Path) -> MatchingConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    match = data.get("matching", {})
    return MatchingConfig(Decimal(str(match.get("minimum_confidence", ".65"))), Decimal(str(match.get("tolerance", ".01"))), {k: Decimal(str(v)) for k, v in match.get("weights", {}).items()} or None, str(match.get("assignment", "hungarian")))
