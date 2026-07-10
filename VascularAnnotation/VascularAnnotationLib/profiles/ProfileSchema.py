"""
ProfileSchema.py - VascularProfile dataclass and InputSpec for annotation profiles.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class InputSpec:
    """Specification for a single input file in a profile."""
    label: str
    role: str           # volume | mask | centerline
    required: bool
    file_patterns: list  # list of glob/template strings; {id} replaced with patient_id
    mime: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "InputSpec":
        return cls(
            label=d["label"],
            role=d["role"],
            required=d.get("required", True),
            file_patterns=d.get("file_patterns", []),
            mime=d.get("mime", ""),
        )


@dataclass
class VascularProfile:
    """
    Describes the data layout, workflow phases, and export configuration for
    one annotation use-case (e.g. aorta SVS/STS, carotid bifurcation, etc.).
    """
    id: str
    name: str
    description: str
    version: str
    anatomy_tag: str
    inputs: dict          # logical_name -> InputSpec
    mask_handling: dict
    phases: list          # ordered list of phase IDs
    landmark_set_id: str  # references a LandmarkSet JSON
    export_config: dict
    orthanc_attachments: dict
    profile_schema_version: str = "1.0"

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    @property
    def has_precalculated_centerline(self) -> bool:
        """True when a pre-computed centerline input is present and optional."""
        cl = self.inputs.get("centerline")
        # Centerline is pre-loaded when it exists in inputs and is NOT required
        # (required=False means it is opportunistically used when present)
        return cl is not None and not cl.required

    @property
    def skip_phases(self) -> set:
        """Set of all known phase IDs that are NOT in self.phases."""
        _all = {"load_data", "refine_mask", "extract_centerline", "place_landmarks", "export"}
        return _all - set(self.phases)

    def should_skip_phase(self, phase_id: str) -> bool:
        return phase_id not in self.phases

    def get_orthanc_attachment_id(self, logical_name: str) -> int:
        """Return the Orthanc attachment type ID for a logical name."""
        att_id = self.orthanc_attachments.get(logical_name)
        if att_id is None:
            raise KeyError(f"No Orthanc attachment ID for '{logical_name}' in profile '{self.id}'")
        return int(att_id)

    def get_export_filename(self, artifact_key: str, patient_id: str) -> str:
        """Return the export filename for an artifact key with patient_id substituted."""
        template = self.export_config.get("artifacts", {}).get(artifact_key, "")
        return template.replace("{patient_id}", patient_id)

    def get_required_inputs(self) -> dict:
        return {k: v for k, v in self.inputs.items() if v.required}

    def get_optional_inputs(self) -> dict:
        return {k: v for k, v in self.inputs.items() if not v.required}

    def __repr__(self) -> str:
        return f"VascularProfile(id={self.id!r}, name={self.name!r}, phases={self.phases})"
