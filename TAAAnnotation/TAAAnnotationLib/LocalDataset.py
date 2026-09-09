"""
LocalDataset.py — case discovery and output naming for the offline workflow.

A case is a directory holding two NIfTI inputs: one whose filename contains
``_ct`` and one whose filename contains ``_mask``. Every annotation output is
written back into that same directory, prefixed with the case ID.

Pure Python: no slicer/qt imports, so it is unit-testable outside Slicer.
"""

import json
import os


CT_TOKEN = "_ct"
MASK_TOKEN = "_mask"

# .nrrd is accepted because DataLoader._fixFileExtension renames an input whose
# content does not match its .nii.gz extension; the renamed file must stay
# discoverable on the next load.
INPUT_EXTENSIONS = (".nii.gz", ".nii", ".nrrd")

CHECKPOINT_DIRNAME = ".taa_checkpoint"
CHECKPOINT_STATE_FILENAME = "state.json"

# logical name -> filename suffix appended to the case ID
OUTPUT_SUFFIXES = {
    "refined_mask": "_refined_mask.seg.nrrd",
    "centerline":   "_centerline.vtk",
    "network":      "_network.vtk",
    "endpoints":    "_endpoints.fcsv",
    "zones":        "_zones.fcsv",
    "notes":        "_notes.txt",
    "metadata":     "_annotation.json",
}

# Outputs that must all exist before a case counts as complete.
REQUIRED_OUTPUTS = ("refined_mask", "centerline", "zones", "metadata")

STATUS_NOT_STARTED = "not_started"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETE = "complete"

STATUS_LABELS = {
    STATUS_NOT_STARTED: "○ Not started",
    STATUS_IN_PROGRESS: "⏳ In progress",
    STATUS_COMPLETE:    "✔ Complete",
}


def case_id(case_dir):
    """Return the case ID for a scan directory: its folder name."""
    return os.path.basename(os.path.normpath(os.path.abspath(case_dir)))


def find_inputs(case_dir):
    """Return {"ct": path, "mask": path} for a scan directory; keys absent when unmatched.

    A file matches the mask before the CT, so a name carrying both tokens is
    never mistaken for the CT. Annotation outputs are skipped, so re-opening a
    finished case does not match its refined mask as the input mask.
    """
    found = {}
    if not os.path.isdir(case_dir):
        return found

    try:
        names = sorted(os.listdir(case_dir))
    except OSError:
        return found

    outputs = output_filenames(case_dir)

    for name in names:
        if name in outputs:
            continue
        lowered = name.lower()
        if not lowered.endswith(INPUT_EXTENSIONS):
            continue
        if MASK_TOKEN in lowered:
            found.setdefault("mask", os.path.join(case_dir, name))
        elif CT_TOKEN in lowered:
            found.setdefault("ct", os.path.join(case_dir, name))

    return found


def is_case_dir(path):
    """True when the directory holds both a CT and a mask NIfTI."""
    inputs = find_inputs(path)
    return "ct" in inputs and "mask" in inputs


def output_path(case_dir, kind):
    """Return the absolute path an annotation output is written to."""
    suffix = OUTPUT_SUFFIXES[kind]
    return os.path.join(case_dir, f"{case_id(case_dir)}{suffix}")


def output_filenames(case_dir):
    """Return the set of filenames this case's annotation outputs occupy."""
    return {os.path.basename(output_path(case_dir, kind))
            for kind in OUTPUT_SUFFIXES}


def checkpoint_dir(case_dir):
    """Return the checkpoint directory for a case (may not exist)."""
    return os.path.join(case_dir, CHECKPOINT_DIRNAME)


def checkpoint_state_path(case_dir):
    """Return the path of the checkpoint's state.json (may not exist)."""
    return os.path.join(checkpoint_dir(case_dir), CHECKPOINT_STATE_FILENAME)


def read_checkpoint_state(case_dir):
    """Return the parsed checkpoint state dict, or None when absent or unreadable."""
    path = checkpoint_state_path(case_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError) as e:
        print(f"[LocalDataset] Unreadable checkpoint at {path}: {e}")
        return None
    return state if isinstance(state, dict) else None


def read_export_metadata(case_dir):
    """Return the parsed export metadata dict, or None when absent or unreadable."""
    path = output_path(case_dir, "metadata")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    except (OSError, ValueError) as e:
        print(f"[LocalDataset] Unreadable export metadata at {path}: {e}")
        return None
    return metadata if isinstance(metadata, dict) else None


def is_complete(case_dir):
    """True when every required annotation output exists in the case directory."""
    return all(os.path.exists(output_path(case_dir, kind))
               for kind in REQUIRED_OUTPUTS)


def count_fcsv_points(path):
    """Return the number of control points in a Slicer .fcsv file, 0 when unreadable."""
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for line in f
                       if line.strip() and not line.startswith("#"))
    except OSError:
        return 0


def case_status(case_dir):
    """Return (status, zone_count) for a scan directory."""
    if is_complete(case_dir):
        return STATUS_COMPLETE, count_fcsv_points(output_path(case_dir, "zones"))

    state = read_checkpoint_state(case_dir)
    if state is not None:
        return STATUS_IN_PROGRESS, int(state.get("zone_count", 0))

    return STATUS_NOT_STARTED, 0


def list_cases(root_dir):
    """Return one entry per case under a dataset root, sorted by case ID.

    When ``root_dir`` is itself a case directory it is returned as the single
    entry, so picking a scan folder by mistake still works.

    Each entry: {case_id, path, ct, mask, status, zone_count}.
    """
    if not os.path.isdir(root_dir):
        return []

    if is_case_dir(root_dir):
        candidates = [root_dir]
    else:
        candidates = sorted(
            os.path.join(root_dir, name)
            for name in os.listdir(root_dir)
            if os.path.isdir(os.path.join(root_dir, name))
            and name != CHECKPOINT_DIRNAME
        )

    cases = []
    for path in candidates:
        inputs = find_inputs(path)
        if "ct" not in inputs or "mask" not in inputs:
            continue
        status, zone_count = case_status(path)
        cases.append({
            "case_id": case_id(path),
            "path": path,
            "ct": inputs["ct"],
            "mask": inputs["mask"],
            "status": status,
            "zone_count": zone_count,
        })

    cases.sort(key=lambda c: c["case_id"])
    return cases


def describe_missing_inputs(case_dir):
    """Return a user-facing message explaining why a directory is not a valid case."""
    inputs = find_inputs(case_dir)
    missing = [name for name in ("ct", "mask") if name not in inputs]
    if not missing:
        return ""

    try:
        outputs = output_filenames(case_dir)
        listing = sorted(n for n in os.listdir(case_dir)
                         if n not in outputs
                         and n.lower().endswith(INPUT_EXTENSIONS))
    except OSError:
        listing = []

    tokens = {"ct": CT_TOKEN, "mask": MASK_TOKEN}
    wanted = "\n".join(f"  - a NIfTI file whose name contains '{tokens[m]}'"
                       for m in missing)
    seen = ("\n".join(f"  - {n}" for n in listing)
            if listing else "  (no .nii / .nii.gz files)")

    return (f"'{os.path.basename(case_dir)}' is missing:\n{wanted}\n\n"
            f"NIfTI files found:\n{seen}")
