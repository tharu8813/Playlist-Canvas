"""Layer group model used to organize visual sources."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class LayerGroup:
    """A named container for related canvas sources."""

    name: str
    id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> dict[str, Any]:
        """Convert a group to JSON-compatible data."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LayerGroup":
        """Restore a group from JSON-compatible data."""
        if not isinstance(data, dict):
            raise ValueError("Project groups must be objects.")
        group = cls(**data)
        if not isinstance(group.id, str) or not group.id.strip():
            raise ValueError("Every layer group must have a non-empty string ID.")
        if not isinstance(group.name, str) or not group.name.strip():
            raise ValueError("Every layer group must have a non-empty name.")
        return group
