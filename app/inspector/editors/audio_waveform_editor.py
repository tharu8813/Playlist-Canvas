"""Inspector editor for SourceType.AUDIO_WAVEFORM, split out of
SourceInspector._update_legacy_source_specific_fields."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.inspector.editors.base import editing
from app.models.source import Source

if TYPE_CHECKING:
    from app.inspector.source_inspector import SourceInspector


def edit(inspector: "SourceInspector", source: Source) -> None:
    with editing(inspector, source):
        inspector._set_field_visible("waveform_style", True)
