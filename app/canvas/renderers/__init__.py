"""Per-SourceType Canvas renderers, registered individually in SourceRegistry.

Each renderer module here replaces SourceItem's legacy monolithic paint
dispatch for one SourceType at a time. Types not yet split still render
through SourceItem._paint_legacy via the shared legacy adapter -- see
app/canvas/source_item.py's registration loop.
"""
