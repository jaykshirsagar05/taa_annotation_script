"""
ProfileLoader.py - Load, validate, and save VascularProfile JSON files.

NOTE: jsonschema is NOT used (unavailable in Slicer Python).
Validation is performed manually by checking required keys and types.
"""
import json
import os
from typing import Optional

from .ProfileSchema import VascularProfile, InputSpec


_REQUIRED_TOP_KEYS = [
    "profile_schema_version", "id", "name", "description", "version",
    "anatomy_tag", "inputs", "mask_handling", "phases", "landmark_set",
    "export", "orthanc_attachments",
]

_REQUIRED_INPUT_KEYS = ["label", "role", "required", "file_patterns"]

_KNOWN_ROLES = {"volume", "mask", "centerline", "mesh"}

_KNOWN_PHASE_IDS = {"load_data", "refine_mask", "extract_centerline", "place_landmarks", "export"}


class ProfileLoader:
    """Loads and validates VascularProfile JSON files."""

    @staticmethod
    def load(path: str) -> "VascularProfile":
        """Load a profile JSON file and return a VascularProfile instance.

        Raises:
            FileNotFoundError: if path does not exist.
            ValueError: if the profile fails validation.
        """
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Profile not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        errors = ProfileLoader.validate(data)
        if errors:
            raise ValueError(
                f"Profile '{path}' has {len(errors)} validation error(s):\n"
                + "\n".join(f"  - {e}" for e in errors)
            )
        return ProfileLoader.from_dict(data)

    @staticmethod
    def save(profile_dict: dict, path: str) -> None:
        """Save a profile dict as JSON."""
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profile_dict, f, indent=2, ensure_ascii=False)

    @staticmethod
    def validate(d: dict) -> list:
        """
        Validate a profile dict.  Returns list of error strings (empty = valid).
        Does NOT raise — callers decide what to do with errors.
        """
        errors = []
        if not isinstance(d, dict):
            return ["Profile must be a JSON object"]

        # Required top-level keys
        for key in _REQUIRED_TOP_KEYS:
            if key not in d:
                errors.append(f"Missing required key: '{key}'")

        if errors:
            return errors  # Can't continue without required keys

        # id must be a non-empty string
        if not isinstance(d["id"], str) or not d["id"].strip():
            errors.append("'id' must be a non-empty string")

        # phases must be a non-empty list
        if not isinstance(d["phases"], list) or len(d["phases"]) == 0:
            errors.append("'phases' must be a non-empty list")
        else:
            for ph in d["phases"]:
                if ph not in _KNOWN_PHASE_IDS:
                    errors.append(
                        f"Unknown phase '{ph}'. Known phases: {sorted(_KNOWN_PHASE_IDS)}"
                    )

        # inputs must be a dict
        if not isinstance(d["inputs"], dict):
            errors.append("'inputs' must be a JSON object")
        else:
            for input_name, input_def in d["inputs"].items():
                if not isinstance(input_def, dict):
                    errors.append(f"Input '{input_name}' must be a JSON object")
                    continue
                for req_key in _REQUIRED_INPUT_KEYS:
                    if req_key not in input_def:
                        errors.append(
                            f"Input '{input_name}' missing required key '{req_key}'"
                        )
                if "role" in input_def and input_def["role"] not in _KNOWN_ROLES:
                    errors.append(
                        f"Input '{input_name}' has unknown role '{input_def['role']}'. "
                        f"Known: {sorted(_KNOWN_ROLES)}"
                    )
                if "file_patterns" in input_def:
                    if not isinstance(input_def["file_patterns"], list):
                        errors.append(
                            f"Input '{input_name}'.file_patterns must be a list"
                        )

        # export must have 'artifacts' dict
        if isinstance(d.get("export"), dict):
            if "artifacts" not in d["export"]:
                errors.append("'export' object must contain 'artifacts'")
        else:
            errors.append("'export' must be a JSON object")

        # orthanc_attachments must be a dict with integer values
        if not isinstance(d.get("orthanc_attachments"), dict):
            errors.append("'orthanc_attachments' must be a JSON object")
        else:
            for k, v in d["orthanc_attachments"].items():
                if not isinstance(v, int):
                    errors.append(
                        f"'orthanc_attachments.{k}' must be an integer, got {type(v).__name__}"
                    )

        return errors

    @staticmethod
    def from_dict(d: dict) -> "VascularProfile":
        """Convert a raw dict (already validated) to a VascularProfile."""
        inputs = {
            name: InputSpec.from_dict(spec)
            for name, spec in d.get("inputs", {}).items()
        }
        return VascularProfile(
            id=d["id"],
            name=d["name"],
            description=d.get("description", ""),
            version=d.get("version", "1.0.0"),
            anatomy_tag=d.get("anatomy_tag", ""),
            inputs=inputs,
            mask_handling=d.get("mask_handling", {}),
            phases=d["phases"],
            landmark_set_id=d.get("landmark_set", "generic_n"),
            export_config=d.get("export", {}),
            orthanc_attachments=d.get("orthanc_attachments", {}),
            profile_schema_version=d.get("profile_schema_version", "1.0"),
        )

    @staticmethod
    def to_dict(profile: "VascularProfile") -> dict:
        """Convert a VascularProfile to a serialisable dict."""
        inputs_dict = {}
        for name, spec in profile.inputs.items():
            inputs_dict[name] = {
                "label": spec.label,
                "role": spec.role,
                "required": spec.required,
                "file_patterns": spec.file_patterns,
                "mime": spec.mime,
            }
        return {
            "profile_schema_version": profile.profile_schema_version,
            "id": profile.id,
            "name": profile.name,
            "description": profile.description,
            "version": profile.version,
            "anatomy_tag": profile.anatomy_tag,
            "inputs": inputs_dict,
            "mask_handling": profile.mask_handling,
            "phases": profile.phases,
            "landmark_set": profile.landmark_set_id,
            "export": profile.export_config,
            "orthanc_attachments": profile.orthanc_attachments,
        }
