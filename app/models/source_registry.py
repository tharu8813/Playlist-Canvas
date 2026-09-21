"""Source definitions shared by the model and UI adapter layers.

The core registers the existing Source model/serializer. Canvas and Inspector
bind their legacy callbacks when imported, so model-only use never imports Qt.
Type-specific implementations can replace these bindings in later phases.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.models.source import Source, SourceType


@dataclass(slots=True)
class SourceDefinition:
    """One source type's model, codec and optional UI adapter callbacks."""

    type: SourceType
    component: type[Source] = Source
    serializer: type[Source] = Source
    renderer: Callable[..., None] | None = None
    inspector: Callable[..., None] | None = None


class SourceRegistry:
    """Explicit lookup; unknown types and duplicate registrations are errors."""

    def __init__(self) -> None:
        self._definitions: dict[SourceType, SourceDefinition] = {}

    def register(self, definition: SourceDefinition) -> None:
        if not isinstance(definition.type, SourceType):
            raise ValueError("Source definitions require a supported SourceType.")
        if definition.type in self._definitions:
            raise ValueError(f"Source type is already registered: {definition.type.value}")
        self._definitions[definition.type] = definition

    def get(self, source_type: SourceType | str) -> SourceDefinition:
        try:
            return self._definitions[SourceType(source_type)]
        except (KeyError, ValueError, TypeError) as error:
            raise ValueError(f"Unregistered source type: {source_type!r}") from error

    def serialize(self, source: Source) -> dict[str, Any]:
        return self.get(source.source_type).serializer.to_dict(source)

    def deserialize(self, data: dict[str, Any]) -> Source:
        if not isinstance(data, dict):
            raise ValueError("Project sources must be objects.")
        return self.get(data.get("source_type")).serializer.from_dict(data)


source_registry = SourceRegistry()
for _source_type in SourceType:
    source_registry.register(SourceDefinition(_source_type))
