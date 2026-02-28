"""
DatasetProfile.py - Flexible dataset profiles for different annotation workflows.

Supports auto-detection of dataset type based on available Orthanc attachments
or local folder contents, enabling the module to work with different data
configurations without hardcoded file requirements.

Profiles:
    DUAL_MASK:           CT + unified mask + merged mask  (full VMTK workflow)
    MASK_AND_CENTERLINE: CT + seg mask + pre-computed centerline  (skip VMTK)

Adding a new profile:
    1. Define a new constant (e.g., MY_PROFILE = "my_profile")
    2. Add a DatasetProfile entry to PROFILES with required/optional attachments
    3. Update detect_profile_from_attachments() priority logic
    4. Update detect_profile_from_folder() file-matching logic
"""

import os
import glob


# -------------------------------------------------------------------------
# Attachment type IDs — mirrored from OrthancClient to avoid circular imports
# -------------------------------------------------------------------------
ATT_METADATA = 1024
ATT_CT_NIFTI = 1025
ATT_UNIFIED_MASK = 1026
ATT_MERGED_MASK = 1027
ATT_REFINED_MASK = 1028
ATT_CENTERLINE = 1029
ATT_ZONES = 1030
ATT_ENDPOINTS = 1031
ATT_NOTES = 1032


class DatasetProfile:
    """
    Describes what data a study contains and what workflow phases apply.

    Attributes:
        profile_type:   Unique string key (matches a PROFILES dict key).
        name:           Short human-readable name shown in the UI.
        description:    Longer description for tooltips / info panels.
        required_attachments:  dict {logical_name: attachment_id}
                               All of these must be present to download.
        optional_attachments:  dict {logical_name: attachment_id}
                               Downloaded when available, not mandatory.
        skip_phases:    set of phase numbers skipped for this profile.
        has_precalculated_centerline:  True when the study ships a centerline
                                        and VMTK extraction is unnecessary.
    """

    # Profile type constants
    DUAL_MASK = "dual_mask"
    MASK_AND_CENTERLINE = "mask_and_centerline"

    def __init__(self, profile_type, name, description,
                 required_attachments, optional_attachments=None,
                 skip_phases=None, has_precalculated_centerline=False):
        self.profile_type = profile_type
        self.name = name
        self.description = description
        self.required_attachments = required_attachments
        self.optional_attachments = optional_attachments or {}
        self.skip_phases = skip_phases or set()
        self.has_precalculated_centerline = has_precalculated_centerline

    def should_skip_phase(self, phase_number):
        """Check if a workflow phase should be skipped for this profile."""
        return phase_number in self.skip_phases

    def __repr__(self):
        return f"DatasetProfile({self.profile_type}: {self.name})"


# -------------------------------------------------------------------------
# Pre-defined profiles
# -------------------------------------------------------------------------

PROFILES = {
    DatasetProfile.DUAL_MASK: DatasetProfile(
        profile_type=DatasetProfile.DUAL_MASK,
        name="Dual Mask",
        description=(
            "CT scan with unified and merged segmentation masks. "
            "Full workflow: refine mask, extract centerline via VMTK, "
            "place landmarks."
        ),
        required_attachments={
            "ct":       ATT_CT_NIFTI,
            "seg_mask": ATT_UNIFIED_MASK,
            "ref_mask": ATT_MERGED_MASK,
        },
        skip_phases=set(),
        has_precalculated_centerline=False,
    ),

    DatasetProfile.MASK_AND_CENTERLINE: DatasetProfile(
        profile_type=DatasetProfile.MASK_AND_CENTERLINE,
        name="Mask + Centerline",
        description=(
            "CT scan with segmentation mask and pre-computed centerline. "
            "Workflow: refine mask, place landmarks on existing centerline "
            "(VMTK extraction is skipped)."
        ),
        required_attachments={
            "ct":         ATT_CT_NIFTI,
            "seg_mask":   ATT_UNIFIED_MASK,
            "centerline": ATT_CENTERLINE,
        },
        optional_attachments={
            "ref_mask": ATT_MERGED_MASK,
        },
        skip_phases={3},           # Skip VMTK extraction
        has_precalculated_centerline=True,
    ),
}


# -------------------------------------------------------------------------
# File-name helpers for downloads
# -------------------------------------------------------------------------

ATTACHMENT_DOWNLOAD_NAMES = {
    # logical_name → (extension, filename_template)
    # {id} is replaced with the patient ID at download time
    "ct":         (".nii.gz",   "ct_scan_{id}.nii.gz"),
    "seg_mask":   (".nii.gz",   "{id}_unified_mask_smoothed.nii.gz"),
    "ref_mask":   (".nii.gz",   "{id}_merged_mask.nii.gz"),
    "centerline": (".vtk",      "{id}_Centerline.vtk"),
}


def download_filename(logical_name, patient_id):
    """Return the local filename to use when downloading an attachment."""
    entry = ATTACHMENT_DOWNLOAD_NAMES.get(logical_name)
    if entry:
        return entry[1].replace("{id}", patient_id)
    return f"{patient_id}_{logical_name}"


# -------------------------------------------------------------------------
# Orthanc auto-detection
# -------------------------------------------------------------------------

def detect_profile_from_attachments(available_attachments):
    """
    Detect dataset profile from available Orthanc attachments.

    Args:
        available_attachments: dict of {attachment_name: bool} as returned by
                               OrthancClient.get_study_attachments()

    Returns:
        DatasetProfile instance, or None if no profile matches.
    """
    has_ct        = available_attachments.get("ct_nifti", False)
    has_unified   = available_attachments.get("unified_mask", False)
    has_merged    = available_attachments.get("merged_mask", False)
    has_centerline = available_attachments.get("centerline", False)

    # Mask + Centerline takes priority when a centerline is present
    if has_ct and has_unified and has_centerline:
        return PROFILES[DatasetProfile.MASK_AND_CENTERLINE]

    # Dual Mask (original workflow)
    if has_ct and has_unified and has_merged:
        return PROFILES[DatasetProfile.DUAL_MASK]

    # Partial match — fall back to Dual Mask (download will report what's missing)
    if has_ct and has_unified:
        return PROFILES[DatasetProfile.DUAL_MASK]

    return None


# -------------------------------------------------------------------------
# Local-folder auto-detection
# -------------------------------------------------------------------------

def detect_profile_from_folder(folder_path, patient_id):
    """
    Detect dataset profile from files in a local folder.

    Args:
        folder_path: Path to the data folder.
        patient_id:  Detected patient ID (from CT filename).

    Returns:
        Tuple of (DatasetProfile, dict_of_found_file_paths) or (None, {}).
    """
    found = {}

    # --- CT ---
    ct_path = os.path.join(folder_path, f"ct_scan_{patient_id}.nii.gz")
    if os.path.exists(ct_path):
        found["ct"] = ct_path

    # --- Segmentation mask (unified) ---
    seg_path = os.path.join(folder_path,
                            f"{patient_id}_unified_mask_smoothed.nii.gz")
    if os.path.exists(seg_path):
        found["seg_mask"] = seg_path

    # --- Reference mask (merged) ---
    ref_path = os.path.join(folder_path, f"{patient_id}_merged_mask.nii.gz")
    if os.path.exists(ref_path):
        found["ref_mask"] = ref_path

    # --- Pre-computed centerline (.vtk or .vtp) ---
    centerline_name_candidates = [
        f"{patient_id}_centerline.vtk",
        f"{patient_id}_centerline.vtp",
        f"{patient_id}_Centerline.vtk",
        f"{patient_id}_Centerline.vtp",
        f"centerline_{patient_id}.vtk",
        f"centerline_{patient_id}.vtp",
        f"{patient_id}.vtp",
    ]
    for name in centerline_name_candidates:
        cl_path = os.path.join(folder_path, name)
        if os.path.exists(cl_path):
            found["centerline"] = cl_path
            break

    # Fallback: any lone .vtp or any *centerline*.vtk in the folder
    if "centerline" not in found:
        vtp_files = glob.glob(os.path.join(folder_path, "*.vtp"))
        vtk_cl    = (glob.glob(os.path.join(folder_path, "*centerline*.vtk"))
                     + glob.glob(os.path.join(folder_path, "*Centerline*.vtk")))
        candidates = vtp_files + vtk_cl
        if len(candidates) == 1:
            found["centerline"] = candidates[0]

    # --- Determine profile ---
    has_ct  = "ct"        in found
    has_seg = "seg_mask"  in found
    has_ref = "ref_mask"  in found
    has_cl  = "centerline" in found

    if has_ct and has_seg and has_cl:
        return PROFILES[DatasetProfile.MASK_AND_CENTERLINE], found
    if has_ct and has_seg and has_ref:
        return PROFILES[DatasetProfile.DUAL_MASK], found
    if has_ct and has_seg:
        # Only one mask, no centerline — fall back to dual-mask (will warn)
        return PROFILES[DatasetProfile.DUAL_MASK], found

    return None, found
