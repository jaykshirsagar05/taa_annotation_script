# TAA Annotation Module for 3D Slicer

A specialized 3D Slicer extension for Thoracic Aortic Aneurysm (TAA) annotation workflows with integrated Orthanc PACS server support. Developed by the University of Ottawa Heart Institute.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Module Structure](#module-structure)
- [Workflow Components](#workflow-components)
- [Orthanc Integration](#orthanc-integration)
- [Role-Based Access](#role-based-access)
- [Dataset Profiles](#dataset-profiles)
- [Data Flow](#data-flow)
- [Key Features](#key-features)
- [Installation & Dependencies](#installation--dependencies)
- [Configuration](#configuration)
- [Auto-Update](#auto-update)
- [API Reference](#api-reference)
- [Extending the Module](#extending-the-module)
- [Version History](#version-history)

---

## Overview

The TAA Annotation Module provides a guided 5-phase workflow for medical image annotation:

1. **Load Data** - Import CT scans and pre-computed segmentation masks (NIfTI or native DICOM)
2. **Refine Mask** - Manual correction of aorta segmentation using Segment Editor
3. **Extract Centerline** - VMTK-based centerline extraction (skipped for pre-computed centerline profiles)
4. **Zone Landmarks** - Interactive placement of SVS/STS zonal landmarks on the centerline
5. **Export** - Package refined annotations for storage/review

The module supports two data loading paths:
- **Manual loading** from local filesystem
- **Orthanc PACS integration** with role-based worklist management

Current version: **1.0.1**

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        TAAAnnotation.py                              │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  TAAAnnotationWidget (UI Controller)                            ││
│  │  - Orchestrates all UI components                               ││
│  │  - Handles signals/callbacks between components                  ││
│  │  - Manages Orthanc study/series loading and submission          ││
│  └─────────────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  TAAAnnotationLogic (Business Logic)                            ││
│  │  - Data loading (loadData, loadProcedureDataFromPaths)          ││
│  │  - DICOM SEG loading (_loadDicomSeg)                            ││
│  │  - CT window/level configuration                                 ││
│  │  - Workflow state management                                     ││
│  │  - Segmentation/VMTK setup                                       ││
│  │  - Zone point management                                         ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       TAAAnnotationLib/                             │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │  WorkflowWidget  │  │  OrthancClient   │  │ CenterlinePicker │  │
│  │  (Step-by-step   │  │  (REST API +     │  │ (VTK picking +   │  │
│  │   annotation UI) │  │   async streams) │  │  scroll nav)     │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘  │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ OrthancIntegrat- │  │ OrthancWorklist  │  │  AutosaveManager │  │
│  │   ionWidget      │  │     Widget       │  │                  │  │
│  │ (Login + Actions)│  │ (Table + Filter) │  │                  │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘  │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │  ExportManager   │  │  DatasetProfile  │  │   AutoUpdater    │  │
│  │ (File bundling)  │  │  (Profile defs)  │  │ (GitHub updates) │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘  │
│  ┌──────────────────┐                                               │
│  │    config.py     │                                               │
│  │ (Server settings)│                                               │
│  └──────────────────┘                                               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Module Structure

```
TAAAnnotation/
├── TAAAnnotation.py           # Main module: Widget, Logic, Test classes
├── VERSION                    # Current version string (e.g. 1.0.1)
└── TAAAnnotationLib/
    ├── __init__.py            # Package exports
    ├── AutosaveManager.py     # Session persistence & crash recovery
    ├── AutoUpdater.py         # GitHub-based auto-update
    ├── CenterlinePicker.py    # Interactive 3D centerline zone picking
    ├── config.py              # Server URLs, credentials, CT display defaults
    ├── DatasetProfile.py      # Dataset profile definitions & auto-detection
    ├── ExportManager.py       # Data export bundling
    ├── OrthancClient.py       # Orthanc REST API client (series-level)
    ├── OrthancIntegrationWidget.py  # Combined login/worklist/actions UI
    ├── OrthancWorklistWidget.py     # Login panel & worklist table
    └── WorkflowWidget.py      # 5-phase annotation workflow UI
```

---

## Workflow Components

### WorkflowWidget

The main annotation workflow UI component providing a step-by-step interface.

**Phases:**

| Phase | Button | Function | Enabled When |
|-------|--------|----------|--------------|
| 1 | Load Data & Initialize | Load CT + masks | Always |
| 2 | Refine Mask | Opens Segment Editor | Phase ≥ 1 |
| 3 | Extract VMTK Centerline | Launches VMTK module | Phase ≥ 2 (skipped for pre-computed centerline profiles) |
| 4 | Zonal Landmarks | Interactive SVS/STS zone picking | Phase ≥ 3 |
| 5 | Export & Reset | Bundle outputs | Phase ≥ 3 |

**UI Elements:**
- Phase buttons with visual state (default/active/done)
- Recovery banner for crash recovery
- Centerline selector combo box
- **SVS/STS Zonal Landmarks** zone picker:
  - Zone dropdown with anatomical placement instructions per zone
  - Progress table displaying landmark name, status, and coordinates
  - Start/Confirm/Undo controls
  - Context menu: jump to point, delete
- Notes text area
- Status label
- Quick Save button

**Signals:**
```python
loadDataRequested = qt.Signal()      # Manual load clicked
refineRequested = qt.Signal()        # Refine phase clicked
vmtkRequested = qt.Signal()          # VMTK phase clicked
exportRequested = qt.Signal()        # Export clicked
quickSaveRequested = qt.Signal()     # Quick save clicked
notesChanged = qt.Signal(str)        # Notes text changed
```

---

### CenterlinePicker

Interactive 3D picking for SVS/STS zone landmark placement on extracted centerlines.

**Features:**
- VTK cell picker for precise centerline selection
- Hover preview — point snaps to nearest centerline location before click
- Preview point with cyan color before confirmation
- Preview cross-section plane aligned to centerline tangent
- Cross-sectional slice alignment (Yellow slice perpendicular to centerline tangent)
- Cross-section diameter chords rendered at each confirmed landmark
- Cumulative distance tracking along centerline path
- **Scroll navigation** — mouse-wheel on the Yellow view scrolls along the centerline
- Zone-specific anatomical landmark definitions (`getLandmarkDefs()`)
- Named zone targeting (`setSelectedZone()`)
- Real-time zone count tracking
- Undo support

**Key Methods:**
```python
enable(centerlineNode)           # Start picking mode
disable()                        # Stop picking mode
toggleClickMode()                # Toggle on/off
getLandmarkDefs()                # Returns ordered list of SVS/STS zone definitions
setSelectedZone(zoneName)        # Pre-select a zone for the next placement
getNextUnplacedZone()            # Returns the name of the next unplaced zone
getPlacedCount()                 # Returns count of confirmed landmarks
confirmZonePoint()               # Confirm the current hover/preview point
```

**Workflow:**
1. User clicks "Start Zone Picker"
2. Centerline highlighted in green
3. Move mouse over centerline → hover preview snaps to nearest point
4. Click on centerline → preview point locked
5. Click "Confirm Zone" → landmark added with zone name
6. Repeat until all SVS/STS zones placed
7. Auto-disables when all zones complete

---

### AutosaveManager

Session persistence and crash recovery system.

**Features:**
- Auto-saves every 30 seconds
- Stores state in `{slicer.app.temporaryPath}/taa_wizard_state.json`
- Recovery banner shown if previous session found (within 24 hours)
- Quick save to project directory
- Action history logging for Orthanc operations

**State Saved:**
```json
{
  "timestamp": "ISO datetime",
  "currentId": "patient ID",
  "rootDir": "project folder",
  "phase": 3,
  "zoneCount": 5,
  "volNodeId": "MRML node ID",
  "segNodeId": "MRML node ID"
}
```

---

### ExportManager

Data export and bundling.

**Output Bundle:** `{rootDir}/{patientId}_GT_Bundle/`

**Exported Files:**
| File | Format | Description |
|------|--------|-------------|
| `{id}_refined_mask.seg.nrrd` | NRRD | Refined segmentation |
| `{id}_Centerline.vtk` | VTK | Extracted centerline model |
| `{id}_Network.vtk` | VTK | Vascular network model |
| `{id}_Endpoints.fcsv` | Fiducial CSV | Centerline endpoints |
| `{id}_Zones.fcsv` | Fiducial CSV | Zone landmarks |
| `{id}_notes.txt` | Text | Annotator notes |
| `export_metadata.json` | JSON | Export metadata |

---

## Orthanc Integration

### OrthancClient

REST API client for Orthanc PACS server communication. Operates at the **series level** (CT series as the primary unit). Supports parallel async instance streaming to avoid buffering large ZIP archives in RAM.

**Connection:**
```python
client = OrthancClient(server_url="http://localhost:8042")
success, message = client.login(username, password)
```

**Attachment Type IDs:**
| ID | Constant | Description |
|----|----------|-------------|
| 1024 | ATTACHMENT_METADATA | annotation_metadata.json |
| 1025 | ATTACHMENT_CT_NIFTI | ct_scan.nii.gz |
| 1026 | ATTACHMENT_UNIFIED_MASK | unified_mask_smoothed.nii.gz |
| 1027 | ATTACHMENT_MERGED_MASK | merged_mask.nii.gz |
| 1028 | ATTACHMENT_REFINED_MASK | refined_mask.seg.nrrd |
| 1029 | ATTACHMENT_CENTERLINE | centerline.vtk |
| 1030 | ATTACHMENT_ZONES | zones.fcsv |
| 1031 | ATTACHMENT_ENDPOINTS | endpoints.fcsv |
| 1032 | ATTACHMENT_NOTES | notes.txt |

**Key Methods:**
```python
# Authentication
login(username, password) → (bool, str)
logout()
is_authenticated() → bool

# Study / Series Queries
get_all_studies() → List[Dict]
get_study_info(study_id) → Dict
get_worklist(role) → List[Dict]

# Metadata Management
get_annotation_metadata(study_id) → Dict
set_annotation_metadata(study_id, metadata) → bool
claim_study(study_id, role) → (bool, str)
release_study(study_id) → (bool, str)

# Attachment Operations (series-level with study fallback)
download_nifti_from_series(series_id, attachment_type, output_path) → str
upload_nifti(study_id, file_path, attachment_type) → bool

# Profile-Aware Download (parallel, series-level)
download_files_for_series(series_id, patient_id, profile, temp_dir) → Dict[str, str]
get_series_attachments_info(series_id, parent_study_id) → Dict[str, bool]

# DICOM Native Streaming
get_dicom_seg_series_id(study_id) → Optional[str]
download_dicom_seg_file(seg_series_id, output_path) → Optional[str]

# Workflow Actions
submit_annotation_on_series(series_id, files, notes) → (bool, str)
approve_annotation(study_id, comments) → (bool, str)
reject_annotation(study_id, reason) → (bool, str)
```

**Parallel Download Architecture:**

All profile downloads use `ThreadPoolExecutor`. For standard profiles, attachments are fetched concurrently (up to 4 workers). For the DICOM_NATIVE profile, CT instance streaming and DICOM SEG download run in parallel (2 workers), then optional attachments are fetched sequentially.

CT DICOM instances are streamed one-by-one via `/instances/{id}/file` (1 MB chunks) — no ZIP archive is buffered in RAM.

---

### Annotation Status Lifecycle

```
                    ┌──────────────────┐
                    │     PENDING      │ ◄── Initial state
                    └────────┬─────────┘
                             │ claim (annotator)
                             ▼
                    ┌──────────────────┐
                    │   IN_PROGRESS    │ ◄── Annotator working
                    └────────┬─────────┘
                             │ submit_annotation
                             ▼
                    ┌──────────────────┐
                    │    ANNOTATED     │ ◄── Ready for review
                    └────────┬─────────┘
                             │ claim (reviewer)
                             ▼
                    ┌──────────────────┐
                    │    IN_REVIEW     │ ◄── Reviewer checking
                    └────────┬─────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
              ▼ approve                     ▼ reject
    ┌──────────────────┐          ┌──────────────────┐
    │   GROUND_TRUTH   │          │    REJECTED      │
    └──────────────────┘          └────────┬─────────┘
              │                            │ annotator reclaims
              │ FINAL STATE                └─────► IN_PROGRESS
              ▼
```

**Status Enum:**
```python
class AnnotationStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    ANNOTATED = "annotated"
    IN_REVIEW = "in_review"
    REVIEWED = "reviewed"
    REJECTED = "rejected"
    GROUND_TRUTH = "ground_truth"
```

---

### OrthancIntegrationWidget

Combined UI widget for Orthanc integration.

**Components:**
- Stacked widget (Login page / Worklist page)
- Action buttons (Submit / Approve / Reject)
- Review comments text area

**Signals:**
```python
studyLoaded = qt.Signal(str, dict)        # (series_id, series_info)
annotationSubmitted = qt.Signal(str)       # series_id
annotationApproved = qt.Signal(str)        # series_id
annotationRejected = qt.Signal(str, str)   # (series_id, reason)
loggedOut = qt.Signal()
```

---

### OrthancWorklistWidget

Worklist table showing CT series with filtering capabilities.

**Annotator Filters:**
- My Worklist
- All Pending
- My In Progress
- Rejected

**Reviewer Filters:**
- My Worklist
- Awaiting Review
- My In Review
- Ground Truth

**Table Columns:**
| Column | Description |
|--------|-------------|
| Patient ID | DICOM PatientID |
| Patient Name | DICOM PatientName |
| Study Date | DICOM StudyDate |
| Status | Annotation lifecycle status |
| Annotator | Claimed annotator username |
| Reviewer | Claimed reviewer username |

---

## Role-Based Access

### Annotator Role

**Permissions:**
- View pending and rejected studies
- Claim pending studies for annotation
- View own in-progress studies
- Submit completed annotations
- Release claimed studies

**Worklist Shows:**
- `PENDING` studies (available to claim)
- `IN_PROGRESS` studies (own only)
- `REJECTED` studies (for rework)

**Actions:**
- Submit Annotation to Orthanc

---

### Reviewer Role

**Permissions:**
- View annotated studies awaiting review
- Claim studies for review
- Approve annotations as ground truth
- Reject annotations with feedback

**Worklist Shows:**
- `ANNOTATED` studies (ready for review)
- `IN_REVIEW` studies (own only)
- `GROUND_TRUTH` studies (completed)

**Actions:**
- Approve as Ground Truth
- Reject (requires comment)

---

## Dataset Profiles

The module uses a **profile-based** data loading system that auto-detects the dataset type from available Orthanc series/attachments or local folder contents.

| Profile | Input Data | Workflow | Skip |
|---------|-----------|----------|------|
| **Dual Mask** | CT NIfTI + unified mask + merged mask | Load → Refine → VMTK → Zones → Export | — |
| **Mask + Centerline** | CT NIfTI + seg mask + pre-computed centerline (.vtk/.vtp) | Load → Refine → Zones → Export | VMTK |
| **CT + Seg Mask** | CT (NIfTI or DICOM) + unified mask NIfTI | Load → Refine → VMTK → Zones → Export | — |
| **DICOM Native** | Native DICOM CT series + DICOM SEG series | Load → Refine → VMTK → Zones → Export | — |

**Auto-detection priority (Orthanc):**
1. DICOM Native — when a DICOM CT series and a DICOM SEG series are present
2. Mask + Centerline — when CT + unified mask + centerline attachment exist
3. Dual Mask — when CT + both NIfTI masks exist
4. CT + Seg Mask — when CT + unified mask exist

**DICOM Native profile details:**
- CT is streamed instance-by-instance from the Orthanc series (no ZIP buffering)
- Segmentation is loaded directly from the DICOM SEG object via the Slicer `DICOMSegmentation` plugin
- Two-strategy fallback: DICOM database import (primary) → `slicer.util.loadSegmentation` (fallback)
- The CT DICOM directory is co-imported into a temporary DICOM database so the SEG plugin can resolve the referenced CT series geometry

**Adding a new profile:** Define in `PROFILES` dict in `DatasetProfile.py`, update `detect_profile_from_attachments()` and `detect_profile_from_folder()`.

---

## Data Flow

### Manual Loading

```
1. User clicks "Load Data & Initialize"
2. Folder picker dialog opens
3. Logic searches for: ct_scan_{id}.nii.gz
4. Loads:
   - CT volume → volNode (with configured window/level applied)
   - {id}_unified_mask_smoothed.nii.gz → segNode (editable)
   - {id}_merged.nii.gz → refNode (reference)
   - Binarized merged mask → binarizedMergedNode (if profile requires)
5. Sets up 4-up view layout
6. Phase advances to 1
```

### Orthanc Loading

```
1. User logs into Orthanc (direct mode or via AdminDashboard)
2. Selects CT series from worklist
3. Profile auto-detected from available attachments/series
4. OrthancClient downloads files in parallel:
   Standard profiles:
     - Attachment 1025 → ct_scan.nii.gz          (or DICOM series)
     - Attachment 1026 → unified_mask_smoothed.nii.gz
     - Attachment 1027 → merged_mask.nii.gz       (Dual Mask)
     - Attachment 1029 → centerline.vtk           (Mask+CL)
   DICOM Native:
     - CT instances streamed per-instance → {id}_ct_dicom/
     - DICOM SEG instance → {id}_seg.dcm
5. Files saved to temp directory
6. Logic.loadProcedureDataFromPaths() called
7. CT window/level from config applied automatically
8. For reviewers: existing annotations also downloaded
```

### Orthanc Submission

```
1. User clicks "Submit Annotation to Orthanc"
2. Zone count validation (warning if zones incomplete)
3. Export to temp directory:
   - refined_mask.seg.nrrd
   - Centerline.vtk
   - Zones.fcsv
4. OrthancClient uploads each as series-level attachment
5. Notes uploaded as text attachment
6. Metadata updated: status → ANNOTATED
7. History entry added
```

---

## Key Features

### Input Data Requirements

For local loading, folder must contain:
```
folder/
├── ct_scan_{patientId}.nii.gz                        # CT volume
├── {patientId}_unified_mask_smoothed.nii.gz          # Initial segmentation
└── {patientId}_merged.nii.gz                         # Reference merged mask
```

### SVS/STS Zone Landmarks

Named anatomical zone points placed interactively on the centerline following the SVS/STS reporting standards. The zone picker guides the annotator through each zone in order with anatomical instructions displayed per zone.

- Uses VMTK-extracted (or pre-computed) centerline
- Cross-sectional view alignment with tangent plane
- Hover preview snaps to nearest centerline point
- Scroll wheel navigates along centerline in Yellow view
- Cross-section diameter chords rendered at each placed landmark
- Cumulative centerline distance tracked per zone
- Preview before confirmation, undo support

### VMTK Integration

Automatic setup for Extract Centerline module:
- Creates surface model from binarized refined mask
- Pre-configures output nodes (Network, Endpoints, Centerline)
- Sets input references in VMTK parameter node

### CT Window/Level

Default CT display window/level is applied automatically after data load. Values are read from `config.py`:
- `DEFAULT_CT_WINDOW_WIDTH` (default: 1200)
- `DEFAULT_CT_WINDOW_LEVEL` (default: 350 HU)

---

## Installation & Dependencies

### Required Extensions

- **SegmentEditor** - Built-in segmentation tools
- **ExtractCenterline** (VMTK) - Centerline extraction
- **DICOMSegmentation** - For DICOM SEG loading (DICOM Native profile)

### Python Dependencies

- `requests` - HTTP client for Orthanc API and auto-update
- Standard library: `json`, `tempfile`, `os`, `datetime`, `enum`, `concurrent.futures`

### Installation

1. Copy `TAAAnnotation/` folder to Slicer's module paths
2. Restart Slicer
3. Find module under **Segmentation** category

---

## Configuration

All server and display settings are in `TAAAnnotationLib/config.py`. Edit this file to match your deployment.

```python
# Orthanc PACS server
ORTHANC_URL = "http://localhost:8042"

# AdminDashboard base URL
ADMIN_DASHBOARD_URL = "http://localhost:7778"

# Orthanc service account credentials
ORTHANC_USERNAME = "orthanc"
ORTHANC_PASSWORD = "orthanc"

# CT display window defaults (Hounsfield units)
DEFAULT_CT_WINDOW_WIDTH = 1200
DEFAULT_CT_WINDOW_LEVEL = 350
```

### Autosave

- Interval: 30 seconds
- Location: `{slicer.app.temporaryPath}/taa_wizard_state.json`
- Recovery window: 24 hours

---

## Auto-Update

The module checks GitHub for new releases on startup via `AutoUpdater.py`.

- Source: `https://github.com/jaykshirsagar05/taa_annotation_script/releases/latest`
- Current version stored in `TAAAnnotation/VERSION`
- On update: downloads release ZIP, extracts `TAAAnnotation/` over current install, prompts user to restart Slicer
- Non-blocking: check runs via deferred call, failures are logged and ignored silently

To disable auto-update, remove the `checkAndUpdate()` call from `TAAAnnotation.py`.

---

## API Reference

### TAAAnnotationLogic

```python
# State Management
reset()                                      # Clear all state
getTimestamp() → str                         # Current time HH:MM:SS

# Data Loading
loadData(folderPath) → bool
loadProcedureDataFromPaths(ct_path, seg_path, merged_path, profile)

# Display
applyConfiguredCtWindowLevel()               # Apply config.py window/level to CT

# Workflow Phases
setupRefinement() → bool                     # Phase 2: Opens Segment Editor
setupVMTK() → bool                           # Phase 3: Configures VMTK

# Zone Management
createZoneNode() → node
addZonePoint(worldPos, pointId) → label
undoLastZonePoint()
deleteZonePoint(index)
renameZonePoint(index, newName)
jumpToZonePoint(index)

# State Properties
currentId: str                               # Patient ID
rootDir: str                                 # Project folder
volNode: vtkMRMLVolumeNode                   # CT volume
segNode: vtkMRMLSegmentationNode             # Editable segmentation
refNode: vtkMRMLSegmentationNode             # Reference segmentation
zoneNode: vtkMRMLMarkupsFiducialNode         # Zone landmarks
hasUnsavedWork: bool
workflowState: dict                          # {phase, lastSave, zoneCount}
```

---

## Extending the Module

### Adding New Workflow Phase

1. Add button in `WorkflowWidget._setupUI()`
2. Create signal and connect to handler
3. Add logic method in `TAAAnnotationLogic`
4. Update `WorkflowWidget.updateUIState()` for enabling logic
5. Update `WorkflowWidget.markDone()` for completion styling

### Adding New Orthanc Attachment Type

1. Define constant in `OrthancClient`:
   ```python
   ATTACHMENT_NEW_TYPE = 1033  # Must be 1024-65535
   ```
2. Add to `_get_extension_for_type()`
3. Add to `get_series_attachments_info()` if needed
4. Update `submit_annotation_on_series()` file mapping

### Adding New Dataset Profile

1. Add profile type constant to `DatasetProfile` class
2. Add entry to `PROFILES` dict in `DatasetProfile.py`
3. Update `detect_profile_from_attachments()` detection priority
4. Update `detect_profile_from_folder()` for local loading
5. Handle the new profile in `OrthancClient.download_files_for_series()` if it needs special download logic

### Adding New User Role

1. Add role handling in `OrthancClient.get_worklist()`
2. Update `OrthancClient.claim_study()` for role validation
3. Add filter options in `OrthancWorklistWidget.setupUI()`
4. Configure action buttons in `OrthancIntegrationWidget._configureActionsForRole()`

### Custom Export Formats

Extend `ExportManager.exportAll()`:
```python
# Add after existing exports
progress.setLabelText("Saving custom format...")
progress.setValue(8)
# Your export logic here
```

---

## Version History

| Version | Changes |
|---------|---------|
| **1.0.1** | Version bump |
| **1.0.0** | CT window/level auto-configuration from `config.py`; async parallel DICOM instance streaming replaces ZIP archive download; DICOM Native profile for CT+DICOM SEG workflows |
| **0.x** | CenterlinePicker: scroll navigation on Yellow view, hover preview, cumulative distances, cross-section diameter chords; `config.py` for centralized server settings; series-level Orthanc operations; DatasetProfile system with DUAL_MASK, MASK_AND_CENTERLINE, CT_AND_SEG_MASK profiles; SVS/STS zone landmark UI with progress table and anatomical instructions; auto-update via GitHub releases |
| **2.1_modular** | Modular architecture, Orthanc PACS integration, role-based workflow |

---

## Contact

Developed by University of Ottawa Heart Institute (Canada)

---

## License

[Add your license information here]
