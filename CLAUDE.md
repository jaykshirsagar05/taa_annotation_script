# CLAUDE.md — TAA Annotation Module

## Project Overview

A **3D Slicer extension** for Thoracic Aortic Aneurysm (TAA) annotation workflows with Orthanc PACS integration. Developed by the University of Ottawa Heart Institute. Provides a guided 5-phase workflow: Load Data → Refine Mask → Extract Centerline → Place Zonal Landmarks → Export.

## Directory Structure

```
taa_annotation_script/
├── TAAAnnotation/                        # Main Slicer module
│   ├── TAAAnnotation.py                  # Module entry point (Widget + Logic)
│   └── TAAAnnotationLib/                 # Library components
│       ├── OrthancClient.py              # Orthanc REST API client
│       ├── OrthancIntegrationWidget.py   # Orthanc login/worklist UI
│       ├── OrthancWorklistWidget.py      # Worklist table & login panel
│       ├── WorkflowWidget.py             # 5-phase workflow UI
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
- **Centerline Extraction:** VMTK (ExtractCenterline extension)
- **PACS:** Orthanc server + AdminDashboard API
- **Key libraries:** `requests`, `numpy`, `pydicom`

## How to Run

1. Install **3D Slicer** (4.11+ with Python 3)
2. Install **ExtractCenterline/VMTK** from Slicer Extensions Manager
3. Install `requests` via Slicer's Python: `pip install requests`
4. Copy the `TAAAnnotation/` folder into Slicer's scripted-modules path
5. Restart Slicer → find module under **Segmentation > TAA Annotation**

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
| 1029 | centerline.vtk |
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
└── {patientId}_merged_mask.nii.gz
```

## Export Output Structure

```
{patientId}_GT_Bundle/
├── {id}_refined_mask.seg.nrrd
├── {id}_Centerline.vtk
├── {id}_Network.vtk
├── {id}_Endpoints.fcsv
├── {id}_Zones.fcsv
├── {id}_notes.txt
└── export_metadata.json
```
