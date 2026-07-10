"""
PlaceLandmarksPhase.py - Workflow phase: Interactive landmark placement on centerline.
"""
from .BasePhase import BasePhase

try:
    import slicer
    SLICER_AVAILABLE = True
except ImportError:
    SLICER_AVAILABLE = False


class PlaceLandmarksPhase(BasePhase):
    """Phase 4: Interactive placement of landmarks on the extracted centerline."""

    PHASE_ID   = "place_landmarks"
    PHASE_NAME = "Place Landmarks"

    def can_execute(self) -> bool:
        return getattr(self.logic, "centerlineNode", None) is not None

    def execute(self, landmark_picker=None, **kwargs) -> bool:
        """
        Enable the LandmarkPicker interactor.

        Args:
            landmark_picker: LandmarkPicker instance.  If None, phase
                             only creates the zone node and marks itself active.
        """
        try:
            self.logic.createZoneNode()
            self.mark_active()
            if landmark_picker:
                cl_node = self.logic.centerlineNode
                success = landmark_picker.enable(cl_node)
                if success:
                    self.mark_active()
                return success
            return True
        except Exception as e:
            print(f"[PlaceLandmarksPhase] Error: {e}")
            return False

    def complete(self, placed_count: int = 0) -> None:
        """Called when the user confirms landmark placement is done."""
        self.mark_done(f"{placed_count} landmarks placed")
