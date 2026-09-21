"""AutoMix analysis, planning, and rendering (built incrementally by phase).

Phase 1 introduces only the analysis foundation: data models, cache,
provider/service boundary. No planner or renderer exists yet, and nothing
outside this package imports it -- legacy Preview/Export are unaffected.
"""
