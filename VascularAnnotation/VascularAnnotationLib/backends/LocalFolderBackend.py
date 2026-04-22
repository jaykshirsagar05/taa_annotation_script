"""
LocalFolderBackend.py - Local filesystem storage backend.

Scans a watched directory for study sub-folders containing NIfTI files.
Submits artifacts by copying them into {watched_dir}/submitted/{patient_id}/.
"""
import os
import shutil
import glob as _glob
from typing import Tuple

from .StorageBackend import StorageBackend
from ..persistence.SettingsStore import settings as _settings


class LocalFolderBackend(StorageBackend):
    """Storage backend backed by a local folder."""

    CAPABILITIES = {"worklist", "submit"}

    def __init__(self, watched_folder: str = ""):
        self._watched_folder = watched_folder or _settings.local_watched_folder

    # ------------------------------------------------------------------
    # Watched folder
    # ------------------------------------------------------------------

    def set_watched_folder(self, path: str) -> None:
        self._watched_folder = path
        _settings.local_watched_folder = path

    def get_watched_folder(self) -> str:
        return self._watched_folder

    # ------------------------------------------------------------------
    # StorageBackend interface
    # ------------------------------------------------------------------

    def list_worklist(self, filter_name: str = "all") -> list:
        """
        Scan watched directory for sub-folders containing at least one NIfTI file.

        Returns:
            List of dicts: {id, patient_id, status, path}
        """
        if not self._watched_folder or not os.path.isdir(self._watched_folder):
            return []

        result = []
        submitted_dir = os.path.join(self._watched_folder, "submitted")

        for entry in sorted(os.listdir(self._watched_folder)):
            entry_path = os.path.join(self._watched_folder, entry)
            if not os.path.isdir(entry_path):
                continue
            if entry == "submitted":
                continue

            # Check if it contains any NIfTI file
            nifti_files = (
                _glob.glob(os.path.join(entry_path, "*.nii.gz"))
                + _glob.glob(os.path.join(entry_path, "*.nii"))
            )
            if not nifti_files:
                continue

            # Determine status
            status = "pending"
            if os.path.isdir(os.path.join(submitted_dir, entry)):
                status = "submitted"

            result.append(
                {
                    "id": entry,
                    "patient_id": entry,
                    "status": status,
                    "path": entry_path,
                }
            )
        return result

    def fetch_study(self, study_id: str, profile, temp_dir: str) -> dict:
        """
        Copy files from the study sub-folder into temp_dir, then match
        by profile file_patterns.

        Args:
            study_id: Sub-folder name inside watched_folder.
            profile:  VascularProfile.
            temp_dir: Destination directory.

        Returns:
            dict with patient_id, file_paths, _profile, _temp_dir.
        """
        study_dir = os.path.join(self._watched_folder, study_id)
        if not os.path.isdir(study_dir):
            raise FileNotFoundError(f"Study folder not found: {study_dir}")

        os.makedirs(temp_dir, exist_ok=True)

        # Copy all files
        for fname in os.listdir(study_dir):
            src = os.path.join(study_dir, fname)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(temp_dir, fname))

        # Match files to profile inputs using file_patterns
        file_paths = {}
        for input_name, input_spec in profile.inputs.items():
            matched = _match_patterns(input_spec.file_patterns, temp_dir)
            if matched:
                file_paths[input_name] = matched
            elif input_spec.required:
                print(
                    f"[LocalFolderBackend] WARNING: Required input '{input_name}' "
                    f"not found in '{study_dir}'"
                )

        return {
            "patient_id": study_id,
            "file_paths": file_paths,
            "_profile": profile,
            "_temp_dir": temp_dir,
        }

    def submit_artifacts(
        self, study_id: str, artifacts: dict, notes: str, metadata: dict
    ) -> Tuple[bool, str]:
        """
        Copy artifacts into {watched_dir}/submitted/{patient_id}/.
        """
        if not self._watched_folder:
            return False, "No watched folder configured"

        dest_dir = os.path.join(self._watched_folder, "submitted", study_id)
        os.makedirs(dest_dir, exist_ok=True)

        copied = []
        failed = []
        for key, src_path in artifacts.items():
            if src_path and os.path.isfile(src_path):
                dest = os.path.join(dest_dir, os.path.basename(src_path))
                try:
                    shutil.copy2(src_path, dest)
                    copied.append(key)
                except Exception as e:
                    failed.append(f"{key}: {e}")

        if notes:
            notes_path = os.path.join(dest_dir, "notes.txt")
            with open(notes_path, "w", encoding="utf-8") as f:
                f.write(notes)

        if metadata:
            import json
            meta_path = os.path.join(dest_dir, "export_metadata.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)

        if failed:
            return False, f"Submitted {len(copied)} files; failures: {'; '.join(failed)}"
        return True, f"Submitted {len(copied)} files to '{dest_dir}'"

    def is_connected(self) -> bool:
        return bool(self._watched_folder) and os.path.isdir(self._watched_folder)

    def get_display_name(self) -> str:
        return f"Local Folder ({self._watched_folder or 'not set'})"


def _match_patterns(patterns: list, folder: str):
    """Return the first file in folder matching any pattern (glob with {id}=*)."""
    for pattern in patterns:
        glob_pattern = pattern.replace("{id}", "*")
        matched = _glob.glob(os.path.join(folder, glob_pattern))
        if matched:
            return matched[0]
    return None
