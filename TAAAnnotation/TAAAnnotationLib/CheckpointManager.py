"""
CheckpointManager.py — local save/resume of an in-progress annotation.

A checkpoint is a ``.taa_checkpoint/`` directory inside the scan folder, so
progress travels with the data when the folder is copied or re-zipped. It
holds the work-in-progress nodes plus a state.json describing the phase
reached. Nothing leaves the machine.
"""

import json
import os
import shutil
import slicer
from datetime import datetime

from TAAAnnotationLib import LocalDataset


CHECKPOINT_VERSION = 1

# Exported outputs that map back onto a scene node when a finished case is
# reopened. "notes" and "metadata" are read separately, not loaded as nodes.
RESTORABLE_OUTPUTS = ("refined_mask", "centerline", "network",
                      "endpoints", "zones")

# logical name -> filename inside the checkpoint directory
CHECKPOINT_FILENAMES = {
    "refined_mask": "refined_mask.seg.nrrd",
    "centerline":   "centerline.vtk",
    "network":      "network.vtk",
    "endpoints":    "endpoints.fcsv",
    "zones":        "zones.fcsv",
}


class CheckpointManager:
    """Writes and restores an annotation checkpoint inside the scan directory."""

    def __init__(self, logic):
        self.logic = logic

    # --- Save ---

    def save(self, notes=""):
        """Write a checkpoint for the loaded case. Returns (success, message)."""
        caseDir = self.logic.caseDir
        if not caseDir or not os.path.isdir(caseDir):
            return False, "No case loaded — nothing to save."

        if not self.logic.segNode:
            return False, "No segmentation loaded — nothing to save."

        ckptDir = LocalDataset.checkpoint_dir(caseDir)
        try:
            os.makedirs(ckptDir, exist_ok=True)
        except OSError as e:
            return False, f"Could not create checkpoint directory: {e}"

        saved = {}
        errors = []

        nodesToSave = [
            ("refined_mask", self.logic.segNode),
            ("centerline",   self.logic.centerlineNode),
            ("network",      self.logic.networkNode),
            ("endpoints",    self.logic.endpointNode),
            ("zones",        self._zoneNodeWithPoints()),
        ]

        for kind, node in nodesToSave:
            if node is None:
                continue
            path = os.path.join(ckptDir, CHECKPOINT_FILENAMES[kind])
            try:
                if slicer.util.saveNode(node, path):
                    saved[kind] = CHECKPOINT_FILENAMES[kind]
                else:
                    errors.append(kind)
            except Exception as e:
                print(f"[Checkpoint] Failed to save {kind}: {e}")
                errors.append(kind)

        state = {
            "version": CHECKPOINT_VERSION,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "case_id": self.logic.currentId,
            "phase": self.logic.workflowState.get("phase", 0),
            "zone_count": self._zoneCount(),
            "notes": notes,
            "files": saved,
        }

        try:
            with open(LocalDataset.checkpoint_state_path(caseDir), "w",
                      encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except OSError as e:
            return False, f"Could not write checkpoint state: {e}"

        self.logic.hasUnsavedWork = False
        self.logic.workflowState["lastSave"] = state["saved_at"]
        print(f"[Checkpoint] Saved {sorted(saved)} to {ckptDir}")

        if errors:
            return True, (f"Checkpoint saved with warnings — "
                          f"could not save: {', '.join(errors)}")
        return True, f"Checkpoint saved ({len(saved)} file(s))"

    # --- Restore ---

    def restore(self, caseDir):
        """Load a checkpoint's nodes over the freshly loaded case.

        The CT must already be in the scene. Returns (state, errors); state is
        None when there is no readable checkpoint.
        """
        state = LocalDataset.read_checkpoint_state(caseDir)
        if state is None:
            return None, ["No checkpoint state found."]

        ckptDir = LocalDataset.checkpoint_dir(caseDir)
        paths = {kind: os.path.join(ckptDir, filename)
                 for kind, filename in state.get("files", {}).items()}

        errors = self._restoreNodes(paths)
        self.logic.workflowState["phase"] = int(state.get("phase", 1))
        self.logic.workflowState["zoneCount"] = self._zoneCount()
        self.logic.hasUnsavedWork = False

        print(f"[Checkpoint] Restored {sorted(paths)} from {ckptDir}")
        return state, errors

    def restoreExport(self, caseDir):
        """Load a completed case's exported outputs back into the scene.

        Lets an annotator reopen finished work for review. Returns
        (metadata, errors); metadata is None when the JSON is missing or
        unreadable, which does not stop the nodes themselves from loading.
        """
        # Only some outputs are written for every case (there is no network
        # model when VMTK produced none), so ask for what is actually there.
        paths = {}
        for kind in RESTORABLE_OUTPUTS:
            path = LocalDataset.output_path(caseDir, kind)
            if os.path.exists(path):
                paths[kind] = path

        errors = self._restoreNodes(paths)
        # A completed case is reopened at the zone-landmark phase: the
        # centerline exists, so landmarks can be reviewed and re-exported.
        self.logic.workflowState["phase"] = 3
        self.logic.workflowState["zoneCount"] = self._zoneCount()
        self.logic.hasUnsavedWork = False

        print(f"[Checkpoint] Reopened exported outputs from {caseDir}")
        return LocalDataset.read_export_metadata(caseDir), errors

    def _restoreNodes(self, paths):
        """Load annotation nodes from a {kind: path} mapping. Returns error strings.

        Every path given is expected to exist — a missing one is reported, not
        skipped, so a corrupt checkpoint is not mistaken for a partial one.
        Callers omit the kinds they do not have.
        """
        errors = []

        segPath = paths.get("refined_mask")
        if segPath:
            try:
                node = slicer.util.loadSegmentation(segPath)
                if node:
                    self._replaceSegmentation(node)
                else:
                    errors.append("Refined mask could not be loaded.")
            except Exception as e:
                errors.append(f"Refined mask: {e}")

        for kind, attr, name, color in (
            ("centerline", "centerlineNode", "Centerline", (1.0, 1.0, 0.0)),
            ("network",    "networkNode",    "Network",    None),
        ):
            path = paths.get(kind)
            if not path:
                continue
            try:
                node = slicer.util.loadModel(path)
                if node:
                    node.SetName(f"{self.logic.currentId}_{name}")
                    display = node.GetDisplayNode()
                    if display and color:
                        display.SetColor(*color)
                        display.SetLineWidth(3)
                    setattr(self.logic, attr, node)
                else:
                    errors.append(f"{name} could not be loaded.")
            except Exception as e:
                errors.append(f"{name}: {e}")

        for kind, attr, name in (
            ("endpoints", "endpointNode", "Endpoints"),
            ("zones",     "zoneNode",     "Zones"),
        ):
            path = paths.get(kind)
            if not path:
                continue
            try:
                node = slicer.util.loadMarkups(path)
                if node:
                    node.SetName(f"{self.logic.currentId}_{name}")
                    setattr(self.logic, attr, node)
                else:
                    errors.append(f"{name} could not be loaded.")
            except Exception as e:
                errors.append(f"{name}: {e}")

        return errors

    # --- Housekeeping ---

    @staticmethod
    def describe(caseDir):
        """Return a one-line summary of a case's checkpoint, or "" when absent."""
        state = LocalDataset.read_checkpoint_state(caseDir)
        if state is None:
            return ""
        return (f"Saved {state.get('saved_at', 'unknown time')} — "
                f"phase {state.get('phase', '?')}, "
                f"{state.get('zone_count', 0)}/10 landmarks")

    @staticmethod
    def describeExport(caseDir):
        """Return a one-line summary of a case's exported annotation, or "" when absent."""
        if not LocalDataset.is_complete(caseDir):
            return ""
        metadata = LocalDataset.read_export_metadata(caseDir) or {}
        exported = metadata.get("exported_at", "an earlier session")
        zones = metadata.get(
            "zone_count",
            LocalDataset.count_fcsv_points(
                LocalDataset.output_path(caseDir, "zones")),
        )
        return f"Exported {exported} — {zones}/10 landmarks"

    @staticmethod
    def clear(caseDir):
        """Delete a case's checkpoint directory. Returns True when it is gone."""
        ckptDir = LocalDataset.checkpoint_dir(caseDir)
        if not os.path.isdir(ckptDir):
            return True
        try:
            shutil.rmtree(ckptDir)
            print(f"[Checkpoint] Cleared {ckptDir}")
            return True
        except OSError as e:
            print(f"[Checkpoint] Could not clear {ckptDir}: {e}")
            return False

    # --- Internal helpers ---

    def _replaceSegmentation(self, node):
        """Swap in a restored segmentation for the one loaded from the input mask."""
        previous = self.logic.segNode
        if previous is not None and previous is not node:
            slicer.mrmlScene.RemoveNode(previous)

        node.SetName(f"{self.logic.currentId}_Segmentation")
        node.CreateClosedSurfaceRepresentation()
        display = node.GetDisplayNode()
        if display:
            display.SetVisibility(True)
        self.logic.segNode = node

    def _zoneNodeWithPoints(self):
        """Return the zone node only when it holds at least one control point."""
        node = self.logic.zoneNode
        if node is None or node.GetNumberOfControlPoints() == 0:
            return None
        return node

    def _zoneCount(self):
        if self.logic.zoneNode is None:
            return 0
        return self.logic.zoneNode.GetNumberOfControlPoints()
