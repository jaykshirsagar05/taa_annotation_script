# TAA Annotation Module for 3D Slicer

A 3D Slicer extension for Thoracic Aortic Aneurysm (TAA) annotation. Developed by the University of Ottawa Heart Institute.

This module runs **entirely offline**. It reads a case from a local folder and writes every annotation output back into that same folder. There is no server, no login, and no network transfer of imaging data.

Current version: **2.0.0**

## Table of contents

- [Overview](#overview)
- [Data layout](#data-layout)
- [Workflow](#workflow)
- [Checkpoints](#checkpoints)
- [Module structure](#module-structure)
- [Components](#components)
- [Installation](#installation)
- [Configuration](#configuration)
- [Auto-update](#auto-update)
- [API reference](#api-reference)
- [Extending the module](#extending-the-module)
- [Version history](#version-history)

---

## Overview

The module provides a guided 5-step workflow:

1. **Load case** — pick a dataset folder, then load a case (CT + segmentation mask NIfTI).
2. **Refine mask** — correct the aorta segmentation in Segment Editor.
3. **Extract centerline** — run VMTK centerline extraction.
4. **Zone landmarks** — place the 10 SVS/STS zonal landmarks on the centerline.
5. **Export** — write all outputs into the case folder.

You can save a checkpoint at any point after step 1, close Slicer, and resume later.

---

## Data layout

Distribute a dataset as one folder containing one subfolder per case:

```
TAA_batch_A/
├── scan_001/
│   ├── <anything>_ct.nii.gz      # CT scan
│   └── <anything>_mask.nii.gz    # segmentation mask
├── scan_002/
│   ├── ...
```

### Input matching rules

- A file is an input when its name ends in `.nii.gz`, `.nii`, or `.nrrd`.
- The file whose name contains `_mask` is the segmentation mask.
- The file whose name contains `_ct` and not `_mask` is the CT scan.
- Matching is case-insensitive. When several files match, the first in alphabetical order wins.
- Annotation outputs are skipped, so re-opening a finished case still finds its original inputs.

### Case ID

The case ID is the **folder name**. Every output file is prefixed with it.

### Outputs

Export writes these files into the case folder:

| File | Format | Description |
|------|--------|-------------|
| `{case}_refined_mask.seg.nrrd` | NRRD | Refined segmentation |
| `{case}_centerline.vtk` | VTK | Extracted centerline model |
| `{case}_network.vtk` | VTK | Vascular network model |
| `{case}_endpoints.fcsv` | Fiducial CSV | Centerline endpoints |
| `{case}_zones.fcsv` | Fiducial CSV | SVS/STS zone landmarks |
| `{case}_notes.txt` | Text | Annotator notes (only when notes were entered) |
| `{case}_annotation.json` | JSON | Case ID, export time, module version, input filenames, output filenames, zone count and labels, notes |

A finished case therefore contains its inputs and its outputs side by side. Re-zip the dataset folder to collect the results.

---

## Workflow

### Case list

Click **Choose Dataset Folder…** and select the unzipped dataset root. The table lists every case with its status:

| Status | Meaning |
|--------|---------|
| ○ Not started | No checkpoint and no outputs |
| ⏳ In progress | A checkpoint exists |
| ✔ Complete | `refined_mask`, `centerline`, `zones`, and `annotation.json` all exist |

Status is derived from the files on disk, so it is correct even after copying the folder to another machine. The chosen folder is remembered between Slicer sessions.

Double-click a case, or select it and click **Load Selected Case**.

If you select a case folder instead of the dataset root, the browser lists that single case.

### Steps 2 to 5

| Step | Button | Enabled when |
|------|--------|--------------|
| 2 | Refine Mask | A case is loaded |
| 3 | Extract VMTK Centerline | Step 2 has been opened |
| 4 | Zonal Landmarks | Step 3 has been set up |
| 5 | Export & Finish Case | Step 3 has been set up |

The mask is binarized to a single segment before Segment Editor opens.

Export warns when fewer than 10 landmarks are placed, then writes the outputs, deletes the checkpoint, clears the scene, and refreshes the case list.

---

## Checkpoints

**Save Progress** writes a checkpoint into `<case folder>/.taa_checkpoint/`:

```
scan_001/.taa_checkpoint/
├── state.json
├── refined_mask.seg.nrrd
├── centerline.vtk          # when extracted
├── network.vtk             # when extracted
├── endpoints.fcsv          # when extracted
└── zones.fcsv              # when at least one landmark is placed
```

`state.json` records the checkpoint version, save time, case ID, workflow phase, landmark count, notes, and which files were written.

The checkpoint lives inside the case folder, so progress survives copying, moving, or re-zipping the dataset.

When you load a case that has a checkpoint, the module shows its save time, phase, and landmark count, and asks whether to resume. Choosing **No** starts fresh from the original segmentation mask and leaves the checkpoint untouched. Choosing **Yes** replaces the loaded mask with the checkpoint's refined mask and restores the centerline, network, endpoints, landmarks, and notes.

Exporting a case deletes its checkpoint.

## Reopening a completed case

Loading a case that reads as **✔ Complete** offers to load its exported annotation for review. Accepting loads the refined mask, centerline, network, endpoints, landmarks, and notes from the exported files and reopens the case at the zone-landmark step, so you can adjust landmarks and re-export over the same filenames. Declining starts fresh from the original mask and leaves the exported files untouched.

A checkpoint takes precedence when both exist. Export clears the checkpoint, so a checkpoint sitting beside exported outputs is newer work.

Only files that exist are loaded — a case exported without a network model reopens without one, and that is not an error.

---

## Module structure

```
TAAAnnotation/
├── TAAAnnotation.py           # Main module: Widget, Logic, Test classes
├── VERSION                    # Current version string
└── TAAAnnotationLib/
    ├── __init__.py            # Package exports
    ├── AutoUpdater.py         # GitHub-based auto-update
    ├── CaseBrowserWidget.py   # Dataset folder picker and case table
    ├── CenterlinePicker.py    # Interactive 3D centerline zone picking
    ├── CheckpointManager.py   # Local save and resume
    ├── config.py              # CT display defaults
    ├── DataLoader.py          # CT and mask loading into the MRML scene
    ├── DependencyInstaller.py # Python package and Slicer extension checks
    ├── ExportManager.py       # Writes outputs into the case folder
    ├── LocalDataset.py        # Case discovery, output naming, status
    ├── RefinementLogic.py     # Segment Editor and VMTK setup
    ├── WorkflowWidget.py      # Steps 2-5 UI
    └── ZoneManager.py         # Zone fiducial CRUD and navigation
```

---

## Components

### LocalDataset

Pure Python, no Slicer imports, so it is unit-testable outside Slicer. Owns every filename convention in the module.

```python
case_id(case_dir) -> str                    # folder name
find_inputs(case_dir) -> dict               # {"ct": path, "mask": path}
output_path(case_dir, kind) -> str          # kind: refined_mask, centerline, ...
list_cases(root_dir) -> list[dict]          # {case_id, path, ct, mask, status, zone_count}
case_status(case_dir) -> (status, zone_count)
read_checkpoint_state(case_dir) -> dict | None
describe_missing_inputs(case_dir) -> str    # user-facing error text
```

### CaseBrowserWidget

Dataset folder picker and case table.

```python
caseSelected = qt.Signal(str)   # case directory path

setRootDir(path) -> bool
refresh()
setCurrentCase(caseDir)
selectedCaseDir() -> str
setEnabledState(enabled)
```

### CheckpointManager

```python
save(notes="") -> (bool, str)
restore(caseDir) -> (state | None, errors)          # from .taa_checkpoint/
restoreExport(caseDir) -> (metadata | None, errors) # from the exported outputs
describe(caseDir) -> str        # static; "" when no checkpoint
describeExport(caseDir) -> str  # static; "" when the case is not complete
clear(caseDir) -> bool          # static
```

### WorkflowWidget

Steps 2 to 5 plus the zone picker UI.

```python
refineRequested = qt.Signal()
vmtkRequested = qt.Signal()
exportRequested = qt.Signal()
saveProgressRequested = qt.Signal()
notesChanged = qt.Signal(str)
```

### CenterlinePicker

Interactive 3D picking for SVS/STS zone landmark placement on the extracted centerline.

Features:

- VTK cell picker for precise centerline selection.
- Hover preview that snaps to the nearest centerline point before you click.
- Cross-section plane aligned to the centerline tangent, shown in the Yellow view.
- Cross-section diameter chords rendered at each confirmed landmark.
- Cumulative arc-length distance tracking along the centerline.
- Mouse-wheel navigation along the centerline in the Yellow view.
- Zone colour overlay between placed landmarks.
- Preview before confirmation, with undo.

```python
enable(centerlineNode)
disable()
toggleClickMode(centerlineNode)
getLandmarkDefs()
setSelectedZone(zoneIndex)
getNextUnplacedZone() -> int
getPlacedCount() -> int
confirmZonePoint(zoneIndex) -> str
syncLandmarksFromZoneNode()     # after a checkpoint restore
```

### ExportManager

Writes every output into the case folder and returns `True` on success. See [Outputs](#outputs).

---

## Installation

### Required Slicer extensions

- **SegmentEditor** — built-in segmentation tools.
- **SlicerVMTK** (ExtractCenterline) — centerline extraction. `DependencyInstaller` offers to install it on first startup.

### Python dependencies

- `requests` — used only by the auto-updater. `DependencyInstaller` installs it into Slicer's Python if it is missing.

### Steps

1. Copy the `TAAAnnotation/` folder into one of Slicer's scripted-module paths.
2. Restart Slicer.
3. Find the module under the **Segmentation** category.

---

## Configuration

`TAAAnnotationLib/config.py` holds the CT display defaults applied after each case loads:

```python
DEFAULT_CT_WINDOW_WIDTH = 1200
DEFAULT_CT_WINDOW_LEVEL = 350
```

---

## Auto-update

The module checks GitHub for a newer release on startup, in `AutoUpdater.py`.

- Source: `https://github.com/jaykshirsagar05/taa_annotation_script/releases/latest`
- Current version: `TAAAnnotation/VERSION`
- On update, it downloads the release ZIP, extracts `TAAAnnotation/` over the current install, and prompts for a Slicer restart.
- The check is non-blocking. Failures are logged and ignored, so an offline machine is unaffected.

To disable auto-update, remove the `checkAndUpdate()` call from `TAAAnnotation.py`.

---

## API reference

### TAAAnnotationLogic

```python
# State
reset(clearScene=True)
getTimestamp() -> str

# Loading
loadCase(caseDir) -> list[str]               # non-fatal errors; raises on a fatal one

# Display
applyConfiguredCtWindowLevel() -> bool

# Workflow
setupRefinement() -> bool                    # Step 2
setupVMTK() -> bool                          # Step 3
getAortaSurfacePolyData() -> vtkPolyData | None

# Zone landmarks
createZoneNode() -> node
addZonePoint(worldPos, zoneName=None) -> str | None
undoLastZonePoint()
deleteZonePoint(index)
renameZonePoint(index, newName)
jumpToZonePoint(index)

# State properties
currentId: str                               # case ID (folder name)
caseDir: str                                 # case directory
volNode: vtkMRMLScalarVolumeNode             # CT volume
segNode: vtkMRMLSegmentationNode             # editable segmentation
zoneNode: vtkMRMLMarkupsFiducialNode         # zone landmarks
centerlineNode: vtkMRMLModelNode
networkNode: vtkMRMLModelNode
endpointNode: vtkMRMLMarkupsFiducialNode
hasUnsavedWork: bool
workflowState: dict                          # {phase, lastSave, zoneCount}
```

---

## Extending the module

### Adding a workflow step

1. Add a button in `WorkflowWidget._setupUI()`.
2. Create a signal and connect it to a handler in `TAAAnnotationWidget.setup()`.
3. Add the logic method to `TAAAnnotationLogic`.
4. Update `WorkflowWidget.updateUIState()` for the enabling rule.
5. Update `WorkflowWidget.markDone()` for the completion styling.

### Adding an output file

1. Add the logical name and filename suffix to `LocalDataset.OUTPUT_SUFFIXES`.
2. Add the node to `nodesToExport` in `ExportManager.exportAll()`, or write the file in a dedicated step.
3. Add the name to `LocalDataset.REQUIRED_OUTPUTS` only when a case is not complete without it.

### Changing the input naming convention

Edit `LocalDataset.CT_TOKEN`, `LocalDataset.MASK_TOKEN`, and `LocalDataset.INPUT_EXTENSIONS`. `find_inputs()` is the only place that matches input filenames.

### Adding a checkpoint field

1. Add the node to `CHECKPOINT_FILENAMES` and to the `nodesToSave` list in `CheckpointManager.save()`.
2. Restore it in `CheckpointManager.restore()`.
3. Bump `CHECKPOINT_VERSION` when the change is not backward-compatible.

---

## Version history

| Version | Changes |
|---------|---------|
| **2.0.0** | Offline-only rewrite. Removed Orthanc PACS, the AdminDashboard client, login, worklist claiming, submission, and review. Added `LocalDataset` case discovery, `CaseBrowserWidget` dataset case list, and `CheckpointManager` local save and resume. Outputs are written flat into the case folder. Removed the DICOM and DICOM SEG loading paths and the dataset profile system; input is one CT NIfTI plus one mask NIfTI. |
| **1.3.1** | Centerline status handling in `WorkflowWidget` |
| **1.3.0** | Staged DICOM loading, extension auto-install, DICOM database initialization |
| **1.0.0** | CT window/level from `config.py`, parallel DICOM instance streaming, DICOM Native profile |
| **0.x** | CenterlinePicker scroll navigation, hover preview, cumulative distances, diameter chords; series-level Orthanc operations; dataset profile system; SVS/STS zone landmark UI; auto-update via GitHub releases |

---

## Contact

Developed by University of Ottawa Heart Institute (Canada)

## License

[Add your license information here]
