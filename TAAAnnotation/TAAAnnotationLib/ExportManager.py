"""
ExportManager.py — writes the finished annotation back into the scan directory.

Outputs land flat beside the CT and mask, each prefixed with the case ID, so
the whole folder can be zipped back and collected as-is.
"""

import json
import os
import slicer
from datetime import datetime

from TAAAnnotationLib import LocalDataset
from TAAAnnotationLib.AutoUpdater import getLocalVersion


class ExportManager:
    """Saves the annotation outputs into the case directory."""

    def __init__(self, logic):
        self.logic = logic

    def exportAll(self, notes=""):
        """Write every annotation output for the loaded case. Returns True on success."""
        import qt

        caseDir = self.logic.caseDir
        if not caseDir or not self.logic.currentId:
            slicer.util.errorDisplay("No case loaded")
            return False

        if not os.path.isdir(caseDir):
            slicer.util.errorDisplay(f"Case directory is missing: {caseDir}")
            return False

        if not self.logic.segNode:
            slicer.util.errorDisplay("No segmentation to export")
            return False

        zoneCount = (self.logic.zoneNode.GetNumberOfControlPoints()
                     if self.logic.zoneNode else 0)
        if zoneCount < 10:
            reply = qt.QMessageBox.question(
                None, 'Incomplete Zone Landmarks',
                f"Only {zoneCount}/10 zone landmarks placed.\n\nExport anyway?",
                qt.QMessageBox.Yes | qt.QMessageBox.No,
                qt.QMessageBox.No
            )
            if reply == qt.QMessageBox.No:
                return False

        nodesToExport = [
            ("refined_mask", self.logic.segNode,        "Refined Mask"),
            ("centerline",   self.logic.centerlineNode, "Centerline Model"),
            ("network",      self.logic.networkNode,    "Network Model"),
            ("endpoints",    self.logic.endpointNode,   "Endpoints"),
            ("zones",        self._zoneNodeWithPoints(), "Zone Landmarks"),
        ]

        progress = qt.QProgressDialog(
            "Exporting annotation…", "Cancel", 0, len(nodesToExport) + 2, None)
        progress.setWindowModality(qt.Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        slicer.app.processEvents()

        exported = []
        failed = []

        try:
            for step, (kind, node, label) in enumerate(nodesToExport, start=1):
                progress.setLabelText(f"Saving {label.lower()}…")
                progress.setValue(step)
                slicer.app.processEvents()

                if node is None:
                    continue

                path = LocalDataset.output_path(caseDir, kind)
                try:
                    if slicer.util.saveNode(node, path):
                        exported.append((kind, label, os.path.basename(path)))
                    else:
                        failed.append(label)
                except Exception as e:
                    print(f"[Export] Failed to save {kind}: {e}")
                    failed.append(label)

            progress.setLabelText("Saving notes…")
            progress.setValue(len(nodesToExport) + 1)
            slicer.app.processEvents()

            if notes:
                notesPath = LocalDataset.output_path(caseDir, "notes")
                with open(notesPath, "w", encoding="utf-8") as f:
                    f.write(notes + "\n")
                exported.append(("notes", "Notes", os.path.basename(notesPath)))

            progress.setLabelText("Saving metadata…")
            progress.setValue(len(nodesToExport) + 2)
            slicer.app.processEvents()

            self._writeMetadata(caseDir, exported, notes, zoneCount)
            progress.close()

        except Exception as e:
            progress.close()
            slicer.util.errorDisplay(f"Export failed: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

        fileList = "\n".join(f"  ✓ {name}" for _, _, name in exported)
        message = (f"✓ Export complete for {self.logic.currentId}\n\n"
                   f"Saved to:\n{caseDir}\n\n{fileList}")
        if failed:
            message += "\n\nCould not save:\n" + "\n".join(f"  ✗ {f}" for f in failed)

        qt.QMessageBox.information(
            None, "Export Complete",
            message + "\n\nClick OK to reset for the next case.")

        self.logic.hasUnsavedWork = False
        return True

    def _writeMetadata(self, caseDir, exported, notes, zoneCount):
        """Write the annotation metadata JSON alongside the other outputs."""
        inputs = LocalDataset.find_inputs(caseDir)
        metadata = {
            "case_id": self.logic.currentId,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "module_version": getLocalVersion(),
            "workflow": "local_offline",
            "inputs": {
                key: os.path.basename(path) for key, path in inputs.items()
            },
            "outputs": {kind: name for kind, _, name in exported},
            "zone_count": zoneCount,
            "zone_labels": self._zoneLabels(),
            "notes": notes,
        }
        with open(LocalDataset.output_path(caseDir, "metadata"), "w",
                  encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

    def _zoneNodeWithPoints(self):
        node = self.logic.zoneNode
        if node is None or node.GetNumberOfControlPoints() == 0:
            return None
        return node

    def _zoneLabels(self):
        node = self.logic.zoneNode
        if node is None:
            return []
        return [node.GetNthControlPointLabel(i)
                for i in range(node.GetNumberOfControlPoints())]
