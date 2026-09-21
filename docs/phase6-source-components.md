# Phase 6: Source Component V2

## Changes

- Added `app/models/source_components.py`: typed dataclass snapshots for image,
  video, album cover, background, text, time, lyrics, track list, now playing,
  shape, progress, visualizer, waveform, level meter and particles. Logo and
  watermark reuse the image component. All 17 SourceTypes are registered.
- `SourceDefinition.component` now identifies the component class. The former
  complete-Source constructor has the explicit name `source_factory`;
  MainWindow and the registry tests use that name.
- `SourceRegistry.component_for(source)` returns a fresh, detached snapshot.
- `SourceStore.update_component(id, **changes)` edits component fields, validates
  a candidate with the existing source codec, then applies changed legacy fields
  through the existing notification path. Invalid edits do not mutate the source
  or emit a change; successful edits preserve source identity and emit once.
- The video settings dialog reads the VideoComponent API. Its returned legacy
  change dictionary and Cancel behavior remain compatible with the Inspector.
- Added `tests/test_source_components.py`; updated the registry API tests.
  No files or existing features removed.

## Adapter and data ownership

The existing flat Source fields remain the single persisted state. Components
are not cached on Source, inserted into project JSON, or updated separately.
Shared transform, appearance and timeline fields remain on Source. Explicit
dataclass field metadata maps shorter component names to legacy keys.

```python
video = source_registry.component_for(source)
print(video.speed, video.paths)
store.update_component(source.id, speed=1.5, paths=["clip.mp4"])
```

Use `component_for` again after edits to read current state. Snapshot dataclass
attributes are frozen, while copied lists can be edited as local drafts. Neither
snapshot lists nor dictionaries returned by `to_source_changes()` alias Source
lists. `update_component` reads fresh state and applies only the supplied edits,
so changes to other component properties are retained.

Project version stays 2. v1/v2 files, existing source constructors, direct field
access, presets, clipboard code, history snapshots and renderer code keep using
the original flat representation. Existing codec migrations/validation are reused.
Component classes and the registry introduce no Qt imports or new dependencies.

## Validation

- Existing focused suite before migration: 18 tests passed.
- After migration: 24 focused tests passed, including six new component tests.
- Tests cover every type's field mapping and project round trip, per-type edits,
  unchanged common fields, source identity, notification counts, no-op edits,
  mutable-list isolation, fresh snapshots, atomic rejection of invalid/unknown
  fields, Undo/Redo and video dialog Cancel.
- Compilation, MainWindow import and an offscreen MainWindow create/add-video/
  edit-component/open-settings/cancel/close smoke check passed.
- The code relationship graph was updated.

## Remaining work

This is the component/legacy adapter foundation. Source fields are deliberately
not physically removed or migrated to a new schema. Most rendering and Inspector
code still uses legacy fields; Phase 7 can migrate its type-specific modules to
these component APIs. Snapshots are intended for model/edit boundaries, not for
reconstruction in every animation frame. No manual GPU export was performed.

Next: Phase 7, SourceItem / Inspector split.
