"""
ExtractCenterlinePhase.py - Workflow phase: Setup VMTK centerline extraction.
"""
from .BasePhase import BasePhase

try:
    import slicer
    SLICER_AVAILABLE = True
except ImportError:
    SLICER_AVAILABLE = False


class ExtractCenterlinePhase(BasePhase):
    """Phase 3: Open ExtractCenterline (VMTK) module for centerline extraction."""

    PHASE_ID   = "extract_centerline"
    PHASE_NAME = "Extract Centerline"

    def can_execute(self) -> bool:
        if not SLICER_AVAILABLE:
            return False
        # Can skip if centerline is pre-loaded
        if (self.profile.has_precalculated_centerline
                and getattr(self.logic, "centerlineNode", None) is not None):
            return True
        return getattr(self.logic, "segNode", None) is not None

    def execute(self, **kwargs) -> bool:
        """Setup VMTK, or mark skipped if centerline is pre-loaded."""
        # If a centerline was pre-loaded, skip this phase gracefully
        if (self.profile.has_precalculated_centerline
                and getattr(self.logic, "centerlineNode", None) is not None):
            self.mark_skipped("Centerline pre-loaded")
            return True
        try:
            success = self.logic.setupVMTK()
            if success:
                self.mark_done("VMTK ready")
            return success
        except Exception as e:
            print(f"[ExtractCenterlinePhase] Error: {e}")
            return False
