"""
StorageBackend.py - Abstract base class for storage backends.

Concrete implementations: LocalFolderBackend, OrthancBackend, DICOMwebBackend.
"""
from abc import ABC, abstractmethod
from typing import Tuple


class StorageBackend(ABC):
    """
    Abstract interface for a data source that can list studies,
    fetch data, and submit annotations.

    Capabilities are declared per-class via the CAPABILITIES class variable.
    """

    CAPABILITIES: set = set()  # subclasses declare: {"worklist", "pacs", "submit", "review"}

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def list_worklist(self, filter_name: str = "all") -> list:
        """
        Return a list of available studies.

        Args:
            filter_name: Optional filter string (backend-specific).

        Returns:
            List of dicts with at minimum: {id, patient_id, status}
        """
        ...

    @abstractmethod
    def fetch_study(self, study_id: str, profile, temp_dir: str) -> dict:
        """
        Download/copy study data to temp_dir, resolve file paths by profile.

        Args:
            study_id: Study identifier (Orthanc ID, folder name, etc.)
            profile:  VascularProfile instance describing required files.
            temp_dir: Local directory to download/copy files into.

        Returns:
            dict with at minimum:
                {
                    "patient_id": str,
                    "file_paths": {logical_name: local_path},
                    "_profile": profile,
                    "_temp_dir": temp_dir,
                }
        """
        ...

    @abstractmethod
    def submit_artifacts(
        self, study_id: str, artifacts: dict, notes: str, metadata: dict
    ) -> Tuple[bool, str]:
        """
        Upload/copy annotation artifacts back to the backend.

        Args:
            study_id:  Study identifier.
            artifacts: dict {logical_name: local_file_path}.
            notes:     Free-text notes string.
            metadata:  Metadata dict to store alongside artifacts.

        Returns:
            (success: bool, message: str)
        """
        ...

    # ------------------------------------------------------------------
    # Capability query
    # ------------------------------------------------------------------

    def can(self, capability: str) -> bool:
        """Return True if this backend supports *capability*."""
        return capability in self.CAPABILITIES

    # ------------------------------------------------------------------
    # Optional methods (raise NotImplementedError if not supported)
    # ------------------------------------------------------------------

    def claim_study(self, study_id: str, role: str) -> Tuple[bool, str]:
        raise NotImplementedError(f"{type(self).__name__} does not support claim_study")

    def release_study(self, study_id: str) -> Tuple[bool, str]:
        raise NotImplementedError(f"{type(self).__name__} does not support release_study")

    def approve_annotation(self, study_id: str, comments: str) -> Tuple[bool, str]:
        raise NotImplementedError(f"{type(self).__name__} does not support approve_annotation")

    def reject_annotation(self, study_id: str, reason: str) -> Tuple[bool, str]:
        raise NotImplementedError(f"{type(self).__name__} does not support reject_annotation")

    def get_worklist_filters(self, role: str) -> list:
        """Return available filter names for the given role."""
        return ["all"]

    def is_connected(self) -> bool:
        """Return True if the backend is reachable."""
        return True

    def get_display_name(self) -> str:
        """Human-readable name for UI display."""
        return type(self).__name__
