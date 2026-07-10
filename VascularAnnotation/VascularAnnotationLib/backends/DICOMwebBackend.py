"""
DICOMwebBackend.py - Placeholder DICOMweb WADO-RS / STOW-RS storage backend.

Full implementation requires a DICOMweb-capable server (e.g. Orthanc with
DICOMweb plugin, DCM4CHEE, Google Cloud Healthcare API).

This stub raises NotImplementedError for all abstract methods so that the
plugin can load and display "DICOMweb" as an option without crashing.
"""
from typing import Tuple

from .StorageBackend import StorageBackend


class DICOMwebBackend(StorageBackend):
    """
    Placeholder DICOMweb backend. Not yet implemented.

    To implement:
      1. Replace NotImplementedError raises with actual WADO-RS / STOW-RS calls.
      2. Update CAPABILITIES as features are added.
    """

    CAPABILITIES: set = {"worklist"}

    def __init__(self, base_url: str = ""):
        self.base_url = base_url

    def list_worklist(self, filter_name: str = "all") -> list:
        raise NotImplementedError(
            "DICOMwebBackend.list_worklist is not yet implemented. "
            "Contribute at https://github.com/your-org/vascular-annotation"
        )

    def fetch_study(self, study_id: str, profile, temp_dir: str) -> dict:
        raise NotImplementedError("DICOMwebBackend.fetch_study is not yet implemented.")

    def submit_artifacts(
        self, study_id: str, artifacts: dict, notes: str, metadata: dict
    ) -> Tuple[bool, str]:
        raise NotImplementedError("DICOMwebBackend.submit_artifacts is not yet implemented.")

    def is_connected(self) -> bool:
        return False

    def get_display_name(self) -> str:
        return f"DICOMweb ({self.base_url or 'not configured'}) [not implemented]"
