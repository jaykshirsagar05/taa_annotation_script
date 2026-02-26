# CLAUDE.md — TAA Annotation Module

## Project Overview

A **3D Slicer extension** for Thoracic Aortic Aneurysm (TAA) annotation workflows with Orthanc PACS integration. Developed by the University of Ottawa Heart Institute. Provides a simplified 3-phase workflow: Load Data → Place Zonal Landmarks → Export/Submit.

Pre-computed centerlines (.vtp) are loaded from Orthanc alongside CT scans and segmentation masks — no in-module mask refinement or centerline extraction is required.

## Directory Structure

```
taa_annotation_script/
├── TAAAnnotation/                        # Main Slicer module
│   ├── TAAAnnotation.py                  # Module entry point (Widget + Logic)
│   └── TAAAnnotationLib/                 # Library components
│       ├── OrthancClient.py              # Orthanc REST API client (with Admin Dashboard fallback)
│       ├── OrthancIntegrationWidget.py   # Orthanc login/worklist UI
│       ├── OrthancWorklistWidget.py      # Worklist table & login panel
│       ├── WorkflowWidget.py             # 3-phase workflow UI
│       ├── AutosaveManager.py            # Session persistence & crash recovery
│       ├── CenterlinePicker.py           # Interactive 3D zone picking
│       └── ExportManager.py              # Data export bundling
├── download_data_orthanc.py              # Utility: download annotations
└── upload_data_orthanc.py                # Utility: bulk upload studies
```

## Tech Stack

- **Language:** Python 3
- **Platform:** 3D Slicer (medical imaging)
- **GUI:** Qt (via Slicer)
- **3D Rendering:** VTK
- **PACS:** Orthanc server (works with or without AdminDashboard)
- **Key libraries:** `requests`, `numpy`, `pydicom`

## How to Run

1. Install **3D Slicer** (4.11+ with Python 3)
2. Install `requests` via Slicer's Python: `pip install requests`
3. Copy the `TAAAnnotation/` folder into Slicer's scripted-modules path
4. Restart Slicer → find module under **Segmentation > TAA Annotation**

### Utility Scripts

```bash
# Upload studies to Orthanc
python upload_data_orthanc.py

# Download completed annotations
python download_data_orthanc.py
```

## Architecture

Three-tier MVC-style separation:

- **Widget layer** — Qt-based UI (`TAAAnnotationWidget`, `WorkflowWidget`, `OrthancIntegrationWidget`)
- **Logic layer** — MRML scene management, file I/O, workflow state (`TAAAnnotationLogic`)
- **Service layer** — REST API, persistence, export (`OrthancClient`, `AutosaveManager`, `ExportManager`, `CenterlinePicker`)

## Workflow

### Simplified 3-Phase Annotation

```
1. Load Data & Initialize
   ├── CT scan (.nii.gz)
   ├── Segmentation mask (.nii.gz)
   └── Pre-computed centerline (.vtp)

2. Place Zonal Landmarks (10 points on centerline)
   ├── Select centerline model in combo box
   ├── Start Zone Picker → click on centerline → Confirm Zone
   └── Repeat until 10 zones placed

3. Export / Submit to Orthanc
```

### Admin Dashboard Independence

The module works in two modes:

- **With AdminDashboard** — Authenticates via AdminDashboard API, gets JWT token and role assignment, uses role-based worklist filtering
- **Without AdminDashboard (fallback)** — When AdminDashboard is unreachable, falls back to direct Orthanc mode with default credentials (`admin`/`admin`) and admin role. Worklist is fetched directly from Orthanc REST API.

Fallback is automatic — the login method tries AdminDashboard first, then falls back on `ConnectionError`/`Timeout`.

## Code Conventions

- **Classes:** PascalCase (`TAAAnnotationLogic`, `OrthancClient`)
- **Methods:** camelCase, event handlers prefixed with `on` (`onLoadData`, `onSubmitToOrthanc`)
- **Constants:** UPPERCASE (`ATTACHMENT_METADATA = 1024`)
- **Variables:** camelCase (`currentId`, `volNode`)
- **Error display:** `slicer.util.errorDisplay()` for user-facing errors
- **Debug logging:** print statements with `[prefix]` format
- **Qt signals/slots** for UI-to-logic communication

## Key Configuration

| Setting | Default |
|---------|---------|
| Orthanc URL | `http://localhost:8042` |
| AdminDashboard URL | `http://localhost:8000` |
| Default credentials | `admin` / `admin` |
| Default role (fallback) | `admin` |
| Autosave interval | 30 seconds |
| Recovery window | 24 hours |
| State file | `{slicer.app.temporaryPath}/taa_wizard_state.json` |

### Orthanc Attachment Type IDs

| ID | Content |
|----|---------|
| 1024 | annotation_metadata.json |
| 1025 | ct_scan.nii.gz |
| 1026 | unified_mask_smoothed.nii.gz |
| 1027 | merged_mask.nii.gz |
| 1028 | refined_mask.seg.nrrd |
| 1029 | centerline.vtp |
| 1030 | zones.fcsv |
| 1031 | endpoints.fcsv |
| 1032 | notes.txt |

## Annotation Status Lifecycle

```
PENDING → IN_PROGRESS → ANNOTATED → IN_REVIEW → GROUND_TRUTH
              ↓                           ↓
          (release)                   REJECTED → IN_PROGRESS
```

## Expected Data Format (Manual Loading)

```
project_folder/
├── ct_scan_{patientId}.nii.gz
├── {patientId}_unified_mask_smoothed.nii.gz
├── {patientId}_merged_mask.nii.gz          (optional)
└── {patientId}_Centerline.vtp              (optional, auto-detected)
```

## Orthanc Study Loading

When a study is loaded from Orthanc, the following attachments are downloaded:

| Attachment | Required | Description |
|------------|----------|-------------|
| 1025 (CT) | Yes | CT scan NIfTI |
| 1026 (unified mask) | Yes | Segmentation mask for editing |
| 1027 (merged mask) | No | Reference segmentation |
| 1029 (centerline) | No | Pre-computed centerline (.vtp) |

The centerline is auto-selected in the UI combo box after loading.

## Export / Submit Output

When submitting to Orthanc, the following are uploaded:

| File | Attachment ID |
|------|---------------|
| refined_mask.seg.nrrd | 1028 |
| Centerline.vtp | 1029 |
| Endpoints.fcsv | 1031 |
| Zones.fcsv | 1030 |
| notes.txt | 1032 |
