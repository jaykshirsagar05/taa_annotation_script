"""
LoadDataPhase.py - Workflow phase: Load input data into the MRML scene.
"""
from .BasePhase import BasePhase

try:
    import slicer
    SLICER_AVAILABLE = True
except ImportError:
    SLICER_AVAILABLE = False


class LoadDataPhase(BasePhase):
    """Phase 1: Load data from a storage backend into the Slicer scene."""

    PHASE_ID   = "load_data"
    PHASE_NAME = "Load Data"

    def can_execute(self) -> bool:
        return True  # Always possible as the entry point

    def execute(self, folder_path: str = "", file_paths: dict = None,
                patient_id: str = "", **kwargs) -> bool:
        """
        Load study data.

        Called with file_paths already resolved (by OrthancBackend.fetch_study or
        LocalFolderBackend.fetch_study).

        Args:
            file_paths: {logical_name: local_path} dict.
            patient_id: Patient / study identifier.
            folder_path: Source folder (used for manual load path).
        """
        try:
            if file_paths and patient_id:
                # Profile-aware load via logic
                self.logic.currentId = patient_id
                if folder_path:
                    self.logic.rootDir = folder_path
                self.logic.activeProfile = self.profile
                errors = self.logic.loadDataWithProfile(self.profile, file_paths)
                if errors:
                    print(f"[LoadDataPhase] Partial load errors: {errors}")
                self.mark_done(f"Loaded: {patient_id}")
                return True
            elif folder_path:
                # Manual folder load using logic's auto-detect path
                success = self.logic.loadData(folder_path)
                if success:
                    self.mark_done(f"Loaded: {self.logic.currentId}")
                return success
            else:
                print("[LoadDataPhase] No file_paths or folder_path provided")
                return False
        except Exception as e:
            print(f"[LoadDataPhase] Error: {e}")
            import traceback
            traceback.print_exc()
            return False
