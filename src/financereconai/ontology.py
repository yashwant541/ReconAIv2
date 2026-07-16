from __future__ import annotations
from pathlib import Path
import yaml
from .normalization import text

class Ontology:
    def __init__(self, concepts: dict[str, list[str]]) -> None:
        self.concepts = {key: {text(key).casefold(), *(text(v).casefold() for v in values)} for key, values in concepts.items()}
    @classmethod
    def from_yaml(cls, path: Path) -> "Ontology": return cls(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    def map(self, label: str | None) -> str | None:
        value = text(label).casefold()
        return next((name for name, aliases in self.concepts.items() if value in aliases), None)
    def add_alias(self, concept: str, alias: str) -> None: self.concepts.setdefault(concept, {concept.casefold()}).add(text(alias).casefold())
