"""Per-SourceType Inspector editors, registered individually in SourceRegistry.

Types not yet split still edit through
SourceInspector._update_legacy_source_specific_fields via the shared
legacy adapter -- see app/inspector/source_inspector.py's registration
loop.
"""
