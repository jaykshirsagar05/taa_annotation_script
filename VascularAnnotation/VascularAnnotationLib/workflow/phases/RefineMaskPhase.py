"""
RefineMaskPhase.py - Workflow phase: Open Segment Editor for mask refinement.
"""
from .BasePhase import BasePhase

try:
    import slicer
    SLICER_AVAILABLE = True
except ImportError:
    SLICER_AVAILABLE = False


class RefineMaskPhase(BasePhase):
    """Phase 2: Open Segment Editor to allow the user to refine the segmentation mask."""

    PHASE_ID   = "refine_mask"
    PHASE_NAME = "Refine Mask"

    def can_execute(self) -> bool:
        return getattr(self.logic, "segNode", None) is not None

    def execute(self, **kwargs) -> bool:
        """Open Segment Editor with the loaded segmentation and volume."""
        try:
            success = self.logic.setupRefinement()
            if success:
                self.mark_done("Segment Editor opened")
            return success
        except Exception as e:
            print(f"[RefineMaskPhase] Error: {e}")
            return False
