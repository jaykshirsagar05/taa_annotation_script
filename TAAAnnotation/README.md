# TAA Annotation Module for 3D Slicer

A specialized 3D Slicer extension for Thoracic Aortic Aneurysm (TAA) annotation workflows with integrated Orthanc PACS server support. Developed by the University of Ottawa Heart Institute.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Module Structure](#module-structure)
- [Workflow Components](#workflow-components)
- [Orthanc Integration](#orthanc-integration)
- [Role-Based Access](#role-based-access)
- [Data Flow](#data-flow)
- [Key Features](#key-features)
- [Installation & Dependencies](#installation--dependencies)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Extending the Module](#extending-the-module)

---

## Overview

The TAA Annotation Module provides a guided 5-phase workflow for medical image annotation:

1. **Load Data** - Import CT scans and pre-computed segmentation masks
2. **Refine Mask** - Manual correction of aorta segmentation using Segment Editor
3. **Extract Centerline** - VMTK-based centerline extraction
4. **Zone Landmarks** - Interactive placement of 10 anatomical zone markers on the centerline
5. **Export** - Package refined annotations for storage/review

The module supports two data loading paths:
- **Manual loading** from local filesystem
- **Orthanc PACS integration** with role-based worklist management

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        TAAAnnotation.py                              │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  TAAAnnotationWidget (UI Controller)                            ││
│  │  - Orchestrates all UI components                               ││
│  │  - Handles signals/callbacks between components                  ││
│  │  - Manages Orthanc study loading and submission                 ││
│  └─────────────────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  TAAAnnotationLogic (Business Logic)                            ││
│  │  - Data loading (loadData, loadProcedureDataFromPaths)          ││
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
│  │  (Step-by-step   │  │  (REST API)      │  │ (VTK picking)    │  │
│  │   annotation UI) │  │                  │  │                  │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘  │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ OrthancIntegrat- │  │ OrthancWorklist  │  │  AutosaveManager │  │
│  │   ionWidget      │  │     Widget       │  │                  │  │
│  │ (Login + Actions)│  │ (Table + Filter) │  │                  │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘  │
│  ┌──────────────────┐                                               │
│  │  ExportManager   │                                               │
│  │ (File bundling)  │                                               │
│  └──────────────────┘                                               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Module Structure

```
TAAAnnotation/
├── TAAAnnotation.py           # Main module: Widget, Logic, Test classes
└── TAAAnnotationLib/
    ├── __init__.py            # Package exports
    ├── AutosaveManager.py     # Session persistence & crash recovery
    ├── CenterlinePicker.py    # Interactive 3D centerline point selection
    ├── ExportManager.py       # Data export bundling
    ├── OrthancClient.py       # Orthanc REST API client
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
| 3 | Extract VMTK Centerline | Launches VMTK module | Phase ≥ 2 |
| 4 | Zonal Landmarks | Interactive zone picking | Phase ≥ 3 |
| 5 | Export & Reset | Bundle outputs | Phase ≥ 3 |

**UI Elements:**
- Phase buttons with visual state (default/active/done)
- Recovery banner for crash recovery
- Centerline selector combo box
- Zone picker controls (Start/Confirm/Undo)
- Zone list with jump-to-point functionality
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

Interactive 3D picking for zone landmark placement on extracted centerlines.

**Features:**
- VTK cell picker for precise centerline selection
- Preview point with cyan color before confirmation
- Cross-sectional slice alignment (Yellow slice perpendicular to centerline tangent)
- Real-time zone count tracking
- Undo support

**Key Methods:**
```python
enable(centerlineNode)     # Start picking mode
disable()                  # Stop picking mode  
toggleClickMode()          # Toggle on/off
```

**Workflow:**
1. User clicks "Start Zone Picker"
2. Centerline highlighted in green
3. Click on centerline → preview point appears
4. Click "Confirm Zone" → point added to zone node
5. Repeat until 10 zones placed
6. Auto-disables when all zones complete

---

### AutosaveManager

Session persistence and crash recovery system.

**Features:**
- Auto-saves every 30 seconds
- Stores state in `{slicer.app.temporaryPath}/taa_wizard_state.json`
- Recovery banner shown if previous session found (within 24 hours)
- Quick save to project directory

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
| `{id}_Zones.fcsv` | Fiducial CSV | Zone landmarks (10 points) |
| `{id}_notes.txt` | Text | Annotator notes |
| `export_metadata.json` | JSON | Export metadata |

---

## Orthanc Integration

### OrthancClient

REST API client for Orthanc PACS server communication.

**Connection:**
```python
client = OrthancClient(server_url="http://localhost:8042")
success, message = client.login(username, password)
```

**Attachment Type IDs:**
| ID | Type | Description |
|----|------|-------------|
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

# Study Queries
get_all_studies() → List[Dict]
get_study_info(study_id) → Dict
get_worklist(role) → List[Dict]

# Metadata Management
get_annotation_metadata(study_id) → Dict
set_annotation_metadata(study_id, metadata) → bool
claim_study(study_id, role) → (bool, str)
release_study(study_id) → (bool, str)

# File Operations
upload_nifti(study_id, file_path, attachment_type) → bool
download_nifti(study_id, attachment_type, output_path) → str

# Workflow Actions
submit_annotation(study_id, files, notes) → (bool, str)
approve_annotation(study_id, comments) → (bool, str)
reject_annotation(study_id, reason) → (bool, str)
```

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
studyLoaded = qt.Signal(str, dict)        # (study_id, study_info)
annotationSubmitted = qt.Signal(str)       # study_id
annotationApproved = qt.Signal(str)        # study_id
annotationRejected = qt.Signal(str, str)   # (study_id, reason)
loggedOut = qt.Signal()
```

---

### OrthancWorklistWidget

Worklist table with filtering capabilities.

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
- 📤 Submit Annotation to Orthanc

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
- ✅ Approve as Ground Truth
- ❌ Reject (requires comment)

---

## Data Flow

### Manual Loading

```
1. User clicks "Load Data & Initialize"
2. Folder picker dialog opens
3. Logic searches for: ct_scan_{id}.nii.gz
4. Loads:
   - CT volume → volNode
   - {id}_unified_mask_smoothed.nii.gz → segNode (editable)
   - {id}_merged.nii.gz → refNode (reference)
   - Binarized merged mask → binarizedMergedNode
5. Sets up 4-up view layout
6. Phase advances to 1
```

### Orthanc Loading

```
1. User logs into Orthanc
2. Selects study from worklist
3. OrthancClient downloads:
   - Attachment 1025 → ct_scan.nii.gz
   - Attachment 1026 → unified_mask_smoothed.nii.gz
   - Attachment 1027 → merged_mask.nii.gz
4. Files saved to temp directory
5. Logic.loadProcedureDataFromPaths() called
6. For reviewers: existing annotations also downloaded
```

### Orthanc Submission

```
1. User clicks "Submit Annotation to Orthanc"
2. Zone count validation (warning if < 10)
3. Export to temp directory:
   - refined_mask.seg.nrrd
   - Centerline.vtk
   - Zones.fcsv
4. OrthancClient uploads each as attachment
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
├── ct_scan_{patientId}.nii.gz        # CT volume
├── {patientId}_unified_mask_smoothed.nii.gz  # Initial segmentation
└── {patientId}_merged.nii.gz         # Reference merged mask
```

### Zone Landmarks

10 anatomical zone points placed interactively on the centerline:
- Zone 0-9 start points
- Uses VMTK-extracted centerline
- Cross-sectional view alignment
- Preview before confirmation

### VMTK Integration

Automatic setup for Extract Centerline module:
- Creates surface model from binarized refined mask
- Pre-configures output nodes (Network, Endpoints, Centerline)
- Sets input references in VMTK parameter node

---

## Installation & Dependencies

### Required Extensions

- **SegmentEditor** - Built-in segmentation tools
- **ExtractCenterline** (VMTK) - Centerline extraction

### Python Dependencies

- `requests` - HTTP client for Orthanc API
- Standard library: `json`, `tempfile`, `os`, `datetime`, `enum`

### Installation

1. Copy TAAAnnotation folder to Slicer's module paths
2. Restart Slicer
3. Find module under **Segmentation** category

---

## Configuration

### Orthanc Server

Default: `http://localhost:8042`

Configurable in login widget.

### Autosave

- Interval: 30 seconds
- Location: `{slicer.app.temporaryPath}/taa_wizard_state.json`
- Recovery window: 24 hours

---

## API Reference

### TAAAnnotationLogic

```python
# State Management
reset()                     # Clear all state
getTimestamp() → str        # Current time HH:MM:SS

# Data Loading
loadData(folderPath) → bool
loadProcedureDataFromPaths(ct_path, unified_path, merged_path)

# Workflow Phases
setupRefinement() → bool    # Phase 2: Opens Segment Editor
setupVMTK() → bool          # Phase 3: Configures VMTK

# Zone Management
createZoneNode() → node
addZonePoint(worldPos, pointId) → label
undoLastZonePoint()
deleteZonePoint(index)
renameZonePoint(index, newName)
jumpToZonePoint(index)

# State Properties
currentId: str              # Patient ID
rootDir: str                # Project folder
volNode: vtkMRMLVolumeNode  # CT volume
segNode: vtkMRMLSegmentationNode  # Editable segmentation
refNode: vtkMRMLSegmentationNode  # Reference segmentation
zoneNode: vtkMRMLMarkupsFiducialNode  # Zone landmarks
hasUnsavedWork: bool
workflowState: dict         # {phase, lastSave, zoneCount}
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
3. Add to `get_study_attachments()` if needed
4. Update `submit_annotation()` file mapping

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

- **2.1_modular** - Current version with modular architecture
- Refactored into separate library components
- Added Orthanc PACS integration
- Role-based workflow support

---

## Contact

Developed by University of Ottawa Heart Institute (Canada)

---

## License

[Add your license information here]
```
