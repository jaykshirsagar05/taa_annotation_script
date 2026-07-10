"""
BasePhase.py - Abstract base class for workflow phases.
"""
from abc import ABC, abstractmethod


class BasePhase(ABC):
    """Abstract base class for a workflow phase."""

    PHASE_ID: str = ""    # subclasses override with their phase ID string
    PHASE_NAME: str = ""  # human-readable label

    # Phase states
    STATE_PENDING = "pending"
    STATE_ACTIVE  = "active"
    STATE_DONE    = "done"
    STATE_SKIPPED = "skipped"

    def __init__(self, logic, profile):
        self.logic = logic
        self.profile = profile
        self.state = self.STATE_PENDING
        self._status_message = ""

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def execute(self, **kwargs) -> bool:
        """
        Run the phase.

        Returns:
            True on success, False on failure.
        """
        ...

    @abstractmethod
    def can_execute(self) -> bool:
        """Return True if preconditions for this phase are met."""
        ...

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def mark_done(self, message: str = "") -> None:
        self.state = self.STATE_DONE
        self._status_message = message

    def mark_skipped(self, message: str = "") -> None:
        self.state = self.STATE_SKIPPED
        self._status_message = message

    def mark_active(self) -> None:
        self.state = self.STATE_ACTIVE

    def reset(self) -> None:
        self.state = self.STATE_PENDING
        self._status_message = ""

    @property
    def status_message(self) -> str:
        return self._status_message

    @property
    def is_done(self) -> bool:
        return self.state == self.STATE_DONE

    @property
    def is_skipped(self) -> bool:
        return self.state == self.STATE_SKIPPED

    @property
    def is_active(self) -> bool:
        return self.state == self.STATE_ACTIVE

    def to_dict(self) -> dict:
        return {
            "id": self.PHASE_ID,
            "name": self.PHASE_NAME,
            "state": self.state,
            "status_message": self._status_message,
        }

    def __repr__(self) -> str:
        return f"{type(self).__name__}(state={self.state})"
