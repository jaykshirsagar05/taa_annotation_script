"""
AutosaveManager.py - Session autosave and crash recovery.

Saves workflow state to a JSON file at regular intervals.
Detects a prior unsaved session on startup and offers recovery.
"""
import json
import os
from datetime import datetime, timedelta

try:
    import slicer
    import qt
    SLICER_AVAILABLE = True
except ImportError:
    SLICER_AVAILABLE = False


_RECOVERY_WINDOW_HOURS = 24
_AUTOSAVE_INTERVAL_MS = 30_000   # 30 seconds


def _state_file_path() -> str:
    if SLICER_AVAILABLE:
        try:
            return os.path.join(
                slicer.app.temporaryPath, "vascular_annotation_state.json"
            )
        except Exception:
            pass
    return os.path.expanduser("~/.vascular_annotation_state.json")


class AutosaveManager:
    """Autosaves workflow state and supports crash recovery."""

    def __init__(self, logic):
        self.logic = logic
        self._timer = None
        self._state_path = _state_file_path()
        self._start_timer()

    # ------------------------------------------------------------------
    # Timer management
    # ------------------------------------------------------------------

    def _start_timer(self):
        if not SLICER_AVAILABLE:
            return
        try:
            self._timer = qt.QTimer()
            self._timer.setInterval(_AUTOSAVE_INTERVAL_MS)
            self._timer.timeout.connect(self._autosave)
            self._timer.start()
        except Exception as e:
            print(f"[AutosaveManager] Could not start timer: {e}")

    def stop(self):
        """Stop the autosave timer."""
        if self._timer:
            try:
                self._timer.stop()
            except Exception:
                pass
            self._timer = None

    # ------------------------------------------------------------------
    # Save / load
    # ------------------------------------------------------------------

    def _autosave(self):
        """Periodic autosave callback."""
        if not self.logic.currentId:
            return
        try:
            self._write_state()
        except Exception as e:
            print(f"[AutosaveManager] Autosave failed: {e}")

    def quickSave(self):
        """Triggered by user 'Quick Save' button."""
        try:
            self._write_state()
            print(f"[AutosaveManager] Quick save at {datetime.now().strftime('%H:%M:%S')}")
        except Exception as e:
            print(f"[AutosaveManager] Quick save failed: {e}")

    def _write_state(self):
        state = {
            "timestamp": datetime.now().isoformat(),
            "patient_id": getattr(self.logic, "currentId", ""),
            "root_dir": getattr(self.logic, "rootDir", ""),
            "workflow_state": getattr(self.logic, "workflowState", {}),
            "zone_count": 0,
            "profile_id": None,
        }
        if getattr(self.logic, "zoneNode", None) and SLICER_AVAILABLE:
            try:
                state["zone_count"] = self.logic.zoneNode.GetNumberOfControlPoints()
            except Exception:
                pass
        profile = getattr(self.logic, "activeProfile", None)
        if profile:
            state["profile_id"] = getattr(profile, "id", None)

        os.makedirs(os.path.dirname(os.path.abspath(self._state_path)), exist_ok=True)
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def _read_state(self) -> dict:
        if not os.path.isfile(self._state_path):
            return {}
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[AutosaveManager] Could not read state: {e}")
            return {}

    # ------------------------------------------------------------------
    # Crash recovery
    # ------------------------------------------------------------------

    def attemptCrashRecovery(self, show_banner_callback):
        """
        Called on startup. If a recent autosave file is found, invokes
        show_banner_callback(patient_id, timestamp) so the UI can show
        a recovery banner.
        """
        state = self._read_state()
        if not state or not state.get("patient_id"):
            return

        try:
            saved_at = datetime.fromisoformat(state["timestamp"])
        except Exception:
            return

        age = datetime.now() - saved_at
        if age > timedelta(hours=_RECOVERY_WINDOW_HOURS):
            return

        try:
            show_banner_callback(state.get("patient_id", ""), state.get("timestamp", ""))
        except Exception as e:
            print(f"[AutosaveManager] Error showing recovery banner: {e}")

    def recoverSession(self) -> bool:
        """Attempt to restore workflow state from the autosave file."""
        state = self._read_state()
        if not state:
            return False
        try:
            self.logic.currentId = state.get("patient_id", "")
            self.logic.rootDir = state.get("root_dir", "")
            saved_ws = state.get("workflow_state", {})
            if isinstance(saved_ws, dict):
                self.logic.workflowState.update(saved_ws)
            print(f"[AutosaveManager] Recovered session for '{self.logic.currentId}'")
            return True
        except Exception as e:
            print(f"[AutosaveManager] Recovery failed: {e}")
            return False

    def cleanup(self):
        """Remove the autosave state file (call after successful export)."""
        self.stop()
        if os.path.isfile(self._state_path):
            try:
                os.remove(self._state_path)
            except Exception as e:
                print(f"[AutosaveManager] Could not remove state file: {e}")
