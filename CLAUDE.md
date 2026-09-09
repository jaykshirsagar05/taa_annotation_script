# CLAUDE.md — TAA Annotation Module

## Project Overview

A **3D Slicer extension** for Thoracic Aortic Aneurysm (TAA) annotation, developed by the University of Ottawa Heart Institute. Provides a guided 5-step workflow: Load Case → Refine Mask → Extract Centerline → Place Zonal Landmarks → Export.

**This branch is offline-only.** Everything is read from and written to a local folder. There is no Orthanc, no AdminDashboard, no login, no worklist claiming, and no submission. The distribution model is: hand an annotator a zip of a dataset folder, they annotate, they zip it back.

## Directory Structure

```
taa_annotation_script/
├── TAAAnnotation/                        # Main Slicer module
│   ├── TAAAnnotation.py                  # Module entry point (Widget + Logic)
│   ├── VERSION                           # 2.0.0
│   └── TAAAnnotationLib/
│       ├── LocalDataset.py               # Case discovery, output naming, status (pure Python)
│       ├── CaseBrowserWidget.py          # Dataset folder picker + case table
│       ├── CheckpointManager.py          # Local save / resume
│       ├── DataLoader.py                 # CT + mask → MRML scene
│       ├── WorkflowWidget.py             # Steps 2-5 UI + zone picker UI
│       ├── RefinementLogic.py            # Segment Editor + VMTK setup
│       ├── CenterlinePicker.py           # Interactive 3D zone picking
│       ├── ZoneManager.py                # Zone fiducial CRUD
│       ├── ExportManager.py              # Writes outputs into the case folder
│       ├── DependencyInstaller.py        # requests + SlicerVMTK checks
│       ├── AutoUpdater.py                # GitHub release auto-update
│       └── config.py                     # CT display defaults only
└── tests/                                # pytest, runs outside Slicer via conftest stubs
```

## Tech Stack

- **Language:** Python 3
- **Platform:** 3D Slicer (4.11+)
- **GUI:** Qt (via Slicer)
- **3D Rendering:** VTK
- **Centerline Extraction:** VMTK (`SlicerVMTK` / ExtractCenterline)
- **Runtime pip dependency:** `requests` (auto-updater only)

`pydicom`, `DICOMLib`, and `ctk` are no longer used — the DICOM and DICOM SEG loading paths were removed.

## How to Run

1. Install **3D Slicer** (4.11+ with Python 3).
2. Copy the `TAAAnnotation/` folder into Slicer's scripted-modules path.
3. Restart Slicer → find the module under **Segmentation > TAA Annotation**.
4. On first startup `DependencyInstaller` offers to install `SlicerVMTK` and pip-installs `requests`.

### Tests

```bash
python -m pytest tests/ -q
```

Tests run outside Slicer. `tests/conftest.py` stubs `slicer`, `qt`, `vtk`, `numpy`, `SimpleITK`, and `sitkUtils` once for the whole session, and puts `TAAAnnotation/` on `sys.path`. `qt.QWidget` and `qt.QObject` must be **real classes**, not MagicMock attributes: project classes subclass them, and subclassing a MagicMock silently yields a MagicMock instance instead of a class (a PEP 560 `__mro_entries__` pitfall) with no import error.

## Architecture

Three-tier separation:

- **Widget layer** — Qt UI (`TAAAnnotationWidget`, `CaseBrowserWidget`, `WorkflowWidget`)
- **Logic layer** — MRML scene, workflow state (`TAAAnnotationLogic`)
- **Service layer** — file I/O, persistence, export (`LocalDataset`, `DataLoader`, `CheckpointManager`, `ExportManager`, `CenterlinePicker`)

**State ownership:** the case folder on disk is the single source of truth. Case status is derived by looking at which files exist — never cached in a database or a settings key. The only thing persisted outside the dataset is the last-used dataset root, in `QSettings` under `TAAAnnotation/DatasetRoot`, purely as a convenience.

`LocalDataset.py` owns **every** filename convention in the module. If you need to change how inputs are matched or outputs are named, that file is the only place to edit.

## Data Layout

```
TAA_batch_A/                          # dataset root — what the annotator picks
├── scan_001/                         # case dir — case ID is the folder name
│   ├── <anything>_ct.nii.gz          # CT
│   ├── <anything>_mask.nii.gz        # segmentation mask
│   ├── .taa_checkpoint/              # written by Save Progress
│   └── scan_001_*.{seg.nrrd,vtk,fcsv,txt,json}   # written by Export
└── scan_002/
```

**Input matching** (`LocalDataset.find_inputs`): a file counts when it ends in `.nii.gz`, `.nii`, or `.nrrd`; `_mask` in the name wins over `_ct`, so a name carrying both tokens is never read as the CT. Matching is case-insensitive and the first alphabetical match wins. Files whose names collide with this case's outputs are skipped, so re-opening a finished case still resolves its original inputs.

`.nrrd` is an accepted input extension because `DataLoader._fixFileExtension` renames an input whose content does not match its `.nii.gz` extension; the renamed file must remain discoverable on the next load.

**Case ID** is the case folder's name, and prefixes every output file.

### Export outputs (flat, in the case folder)

| Logical name | Filename |
|--------------|----------|
| `refined_mask` | `{case}_refined_mask.seg.nrrd` |
| `centerline` | `{case}_centerline.vtk` |
| `network` | `{case}_network.vtk` |
| `endpoints` | `{case}_endpoints.fcsv` |
| `zones` | `{case}_zones.fcsv` |
| `notes` | `{case}_notes.txt` |
| `metadata` | `{case}_annotation.json` |

`REQUIRED_OUTPUTS = (refined_mask, centerline, zones, metadata)` — all four must exist for a case to read as Complete.

## Checkpoints

`Save Progress` writes `<case dir>/.taa_checkpoint/` holding `state.json` plus the WIP nodes (`refined_mask.seg.nrrd`, `centerline.vtk`, `network.vtk`, `endpoints.fcsv`, `zones.fcsv`). It lives **inside the case folder** so progress travels with the data when the folder is copied or re-zipped — Slicer's temp dir would not survive a machine change.

`state.json`: `{version, saved_at, case_id, phase, zone_count, notes, files}`.

Loading a case with a checkpoint prompts to resume. Resuming replaces the freshly loaded input mask with the checkpoint's refined mask, restores the other nodes, sets the phase, and refills the notes box. Declining starts fresh and leaves the checkpoint on disk. Export clears the checkpoint.

There is **no timed autosave** — this was a deliberate choice, so nothing writes to the annotator's data folder without an explicit click.

## Reopening Completed Work

`TAAAnnotationWidget._restorePreviousWork()` decides what a load offers, in this order:

1. A checkpoint exists → offer resume (`CheckpointManager.restore`). Wins over an export, because export clears the checkpoint, so a checkpoint next to exported outputs is newer work.
2. The case is complete → offer to reopen for review (`CheckpointManager.restoreExport`), which loads the exported outputs and sets phase 3 so landmarks can be adjusted and re-exported over the same filenames.
3. Neither → plain load of the CT and input mask.

Both restore paths share `CheckpointManager._restoreNodes(paths)`. It expects **every path it is given to exist** and reports a missing one as an error rather than skipping it, so a corrupt checkpoint is not silently mistaken for a partial one. `restoreExport()` therefore filters to the outputs actually on disk before calling it, since a case legitimately need not have every output.

## Case Status

Derived in `LocalDataset.case_status()`, checked in this order:

1. `complete` — all `REQUIRED_OUTPUTS` exist. Zone count read from the exported `.fcsv`.
2. `in_progress` — a readable checkpoint exists. Zone count read from `state.json`.
3. `not_started` — neither.

A corrupt or unreadable `state.json` degrades to `not_started` rather than raising.

## Code Conventions

- **Classes:** PascalCase (`TAAAnnotationLogic`, `CheckpointManager`)
- **Methods:** camelCase, event handlers prefixed with `on` (`onCaseSelected`, `onSaveProgress`)
- **Module-level functions in pure-logic modules:** snake_case (`LocalDataset.find_inputs`)
- **Constants:** UPPERCASE (`LocalDataset.MASK_TOKEN`, `CHECKPOINT_VERSION`)
- **Variables:** camelCase in Slicer-facing code (`currentId`, `volNode`), snake_case in `LocalDataset`
- **Error display:** `slicer.util.errorDisplay()` / `warningDisplay()` for user-facing errors
- **Debug logging:** print statements with a `[prefix]` (`[Loading]`, `[Checkpoint]`, `[Export]`)
- **Qt signals/slots** for UI-to-logic communication

## Ordering Constraint

`CenterlinePicker.cleanup()` (or `disable()`) **must** run before `slicer.mrmlScene.Clear()` or `logic.reset()`. It removes VTK observers and MRML node references while the nodes are still alive; skipping it leaves dangling state that crashes Slicer on the next `disable()`. `TAAAnnotationWidget.onCaseSelected` and `resetForNextCase` both honour this.

## SVS/STS Zone Landmarks

Ten landmarks, defined in `CenterlinePicker.SVS_STS_LANDMARKS`. The 10-point cap in `ZoneManager.addZonePoint` and `CenterlinePicker.MAX_ZONES` is a clinical standard — do not relax it.

`RefinementLogic.buildPickingSurface()` is what makes zone picking work at all: `CenterlinePicker`'s `vtkCellPicker` hits whatever is frontmost in the 3D view and snaps that position to the nearest centerline point, so the aorta wall must be translucent (opacity 0.3) and the opaque segmentations hidden. `setupVMTK()` calls it, and so does `TAAAnnotationWidget._prepareForZonePicking()` on any restore at phase ≥ 3 — a checkpoint or export stores nodes, not view state, so without it the centerline comes back buried inside a solid model and the picker appears dead.

`CenterlinePicker.landmarkPointIds` maps zone index → centerline point ID. Landmarks restored from a checkpoint have no known point ID and are stored as `-1`; `syncLandmarksFromZoneNode()` rebuilds the map from the zone node's control-point labels after a restore.
