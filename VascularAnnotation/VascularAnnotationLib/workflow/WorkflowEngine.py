"""
WorkflowEngine.py - Drives the annotation workflow through ordered phases.
"""
from .phases.BasePhase import BasePhase
from .phases.LoadDataPhase import LoadDataPhase
from .phases.RefineMaskPhase import RefineMaskPhase
from .phases.ExtractCenterlinePhase import ExtractCenterlinePhase
from .phases.PlaceLandmarksPhase import PlaceLandmarksPhase
from .phases.ExportPhase import ExportPhase


_PHASE_CLASS_MAP = {
    "load_data":           LoadDataPhase,
    "refine_mask":         RefineMaskPhase,
    "extract_centerline":  ExtractCenterlinePhase,
    "place_landmarks":     PlaceLandmarksPhase,
    "export":              ExportPhase,
}


class WorkflowEngine:
    """
    Manages the ordered sequence of workflow phases defined in a VascularProfile.

    Phases are instantiated from profile.phases (a list of phase IDs).
    Phases not in the profile are skipped automatically.
    """

    def __init__(self, logic, profile):
        self.logic = logic
        self.profile = profile
        self.phases = self._build_phases()
        self.current_phase_index = 0

    # ------------------------------------------------------------------
    # Phase construction
    # ------------------------------------------------------------------

    def _build_phases(self) -> list:
        """Instantiate Phase objects in the order declared by the profile."""
        phases = []
        for phase_id in self.profile.phases:
            cls = _PHASE_CLASS_MAP.get(phase_id)
            if cls is None:
                print(f"[WorkflowEngine] WARNING: Unknown phase id '{phase_id}', skipping")
                continue
            phases.append(cls(self.logic, self.profile))
        return phases

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def get_current_phase(self) -> BasePhase:
        """Return the currently active phase object."""
        if 0 <= self.current_phase_index < len(self.phases):
            return self.phases[self.current_phase_index]
        return None

    def can_advance(self) -> bool:
        """True if the current phase is done and there is a next phase."""
        current = self.get_current_phase()
        if current is None:
            return False
        return current.is_done and (self.current_phase_index + 1 < len(self.phases))

    def advance(self) -> bool:
        """Move to the next phase. Returns True if successful."""
        if self.can_advance():
            self.current_phase_index += 1
            self.phases[self.current_phase_index].mark_active()
            return True
        return False

    def go_to_phase(self, phase_id: str) -> bool:
        """Jump to a specific phase by ID."""
        for i, phase in enumerate(self.phases):
            if phase.PHASE_ID == phase_id:
                self.current_phase_index = i
                phase.mark_active()
                return True
        return False

    def reset(self) -> None:
        """Reset all phases and go back to start."""
        for phase in self.phases:
            phase.reset()
        self.current_phase_index = 0

    # ------------------------------------------------------------------
    # State inspection
    # ------------------------------------------------------------------

    def get_phase_states(self) -> list:
        """
        Return a list of phase state dicts for rendering in the UI.

        Returns:
            [{"id": str, "name": str, "state": str, "status_message": str}, ...]
        """
        return [ph.to_dict() for ph in self.phases]

    def get_phase(self, phase_id: str) -> BasePhase:
        """Return the phase with the given ID, or None."""
        for ph in self.phases:
            if ph.PHASE_ID == phase_id:
                return ph
        return None

    def mark_phase_done(self, phase_id: str, message: str = "") -> None:
        ph = self.get_phase(phase_id)
        if ph:
            ph.mark_done(message)

    def is_complete(self) -> bool:
        """True if all phases are done or skipped."""
        return all(ph.is_done or ph.is_skipped for ph in self.phases)

    def __repr__(self) -> str:
        return (
            f"WorkflowEngine(profile={self.profile.id!r}, "
            f"phase={self.current_phase_index}/{len(self.phases)})"
        )
