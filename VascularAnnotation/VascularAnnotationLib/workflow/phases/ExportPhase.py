"""
ExportPhase.py - Workflow phase: Export all annotation artifacts.
"""
from .BasePhase import BasePhase

try:
    import slicer
    SLICER_AVAILABLE = True
except ImportError:
    SLICER_AVAILABLE = False


class ExportPhase(BasePhase):
    """Phase 5: Bundle and export (or submit) all annotation artifacts."""

    PHASE_ID   = "export"
    PHASE_NAME = "Export"

    def can_execute(self) -> bool:
        return getattr(self.logic, "segNode", None) is not None

    def execute(self, export_manager=None, notes: str = "", **kwargs) -> bool:
        """
        Run the export.

        Args:
            export_manager: ExportManager instance.
            notes: Free-text notes string.
        """
        if export_manager is None:
            from ...io.ExportManager import ExportManager
            export_manager = ExportManager(self.logic, self.profile)
        try:
            success = export_manager.exportAll(notes=notes)
            if success:
                self.mark_done("Export complete")
            return success
        except Exception as e:
            print(f"[ExportPhase] Error: {e}")
            if SLICER_AVAILABLE:
                slicer.util.errorDisplay(f"Export failed: {e}")
            return False
