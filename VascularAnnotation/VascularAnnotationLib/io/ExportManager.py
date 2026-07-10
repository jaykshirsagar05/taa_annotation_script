"""
ExportManager.py - Template-driven annotation export for VascularAnnotation.

Uses VascularProfile.get_export_filename() for all artifact naming so that
file names are determined by the active profile, not hard-coded.
"""
import hashlib
import json
import os
from datetime import datetime
from typing import Optional

try:
    import slicer
    import qt
    SLICER_AVAILABLE = True
except ImportError:
    SLICER_AVAILABLE = False


_PLUGIN_VERSION = "1.0.0"


class ExportManager:
    """Profile-driven export of all annotation artifacts."""

    def __init__(self, logic, profile):
        """
        Args:
            logic:   VascularAnnotationLogic instance.
            profile: VascularProfile instance (determines filenames & artifacts).
        """
        self.logic = logic
        self.profile = profile

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def exportAll(self, notes: str = "") -> bool:
        """
        Export all annotation artifacts to a bundle directory.

        The bundle directory is:
            {logic.rootDir}/{patient_id}{export_config.bundle_suffix}/

        Returns:
            True on success, False on failure or user cancellation.
        """
        if not SLICER_AVAILABLE:
            raise RuntimeError("ExportManager requires 3D Slicer")

        if not self.logic.rootDir or not self.logic.currentId:
            slicer.util.errorDisplay("No data loaded")
            return False

        if not self.logic.segNode:
            slicer.util.errorDisplay("No segmentation to export")
            return False

        zone_count = (
            self.logic.zoneNode.GetNumberOfControlPoints()
            if self.logic.zoneNode else 0
        )
        max_lm = self.profile.get_export_filename("landmarks", "").count("{") + 1  # rough check
        if zone_count == 0:
            reply = qt.QMessageBox.question(
                None,
                "No Landmarks",
                "No landmarks placed. Continue export anyway?",
                qt.QMessageBox.Yes | qt.QMessageBox.No,
                qt.QMessageBox.No,
            )
            if reply == qt.QMessageBox.No:
                return False

        pid = self.logic.currentId
        bundle_suffix = self.profile.export_config.get("bundle_suffix", "_GT_Bundle")
        save_dir = os.path.join(self.logic.rootDir, f"{pid}{bundle_suffix}")
        os.makedirs(save_dir, exist_ok=True)

        progress = qt.QProgressDialog("Exporting data...", "Cancel", 0, 7, None)
        progress.setWindowModality(qt.Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        slicer.app.processEvents()

        exported_files = []
        checksums = {}

        try:
            # 1. Refined mask
            progress.setLabelText("Saving refined mask...")
            progress.setValue(1)
            slicer.app.processEvents()
            seg_filename = self.profile.get_export_filename("refined_mask", pid)
            if seg_filename:
                seg_path = os.path.join(save_dir, seg_filename)
                slicer.util.saveNode(self.logic.segNode, seg_path)
                exported_files.append(f"✓ Refined Mask ({seg_filename})")
                checksums[seg_filename] = _file_sha256(seg_path)

            # 2. Centerline
            progress.setLabelText("Saving centerline...")
            progress.setValue(2)
            slicer.app.processEvents()
            if self.logic.centerlineNode:
                cl_filename = self.profile.get_export_filename("centerline", pid)
                if cl_filename:
                    cl_path = os.path.join(save_dir, cl_filename)
                    slicer.util.saveNode(self.logic.centerlineNode, cl_path)
                    exported_files.append(f"✓ Centerline ({cl_filename})")
                    checksums[cl_filename] = _file_sha256(cl_path)

            # 3. Network
            progress.setLabelText("Saving network...")
            progress.setValue(3)
            slicer.app.processEvents()
            if self.logic.networkNode:
                net_filename = self.profile.get_export_filename("network", pid)
                if net_filename:
                    net_path = os.path.join(save_dir, net_filename)
                    slicer.util.saveNode(self.logic.networkNode, net_path)
                    exported_files.append(f"✓ Network ({net_filename})")
                    checksums[net_filename] = _file_sha256(net_path)

            # 4. Endpoints
            progress.setLabelText("Saving endpoints...")
            progress.setValue(4)
            slicer.app.processEvents()
            if self.logic.endpointNode:
                ep_filename = self.profile.get_export_filename("endpoints", pid)
                if ep_filename:
                    ep_path = os.path.join(save_dir, ep_filename)
                    slicer.util.saveNode(self.logic.endpointNode, ep_path)
                    exported_files.append(f"✓ Endpoints ({ep_filename})")
                    checksums[ep_filename] = _file_sha256(ep_path)

            # 5. Landmarks (zones)
            progress.setLabelText("Saving landmarks...")
            progress.setValue(5)
            slicer.app.processEvents()
            if self.logic.zoneNode and zone_count > 0:
                lm_filename = self.profile.get_export_filename("landmarks", pid)
                if lm_filename:
                    lm_path = os.path.join(save_dir, lm_filename)
                    slicer.util.saveNode(self.logic.zoneNode, lm_path)
                    exported_files.append(f"✓ Landmarks ({zone_count} pts, {lm_filename})")
                    checksums[lm_filename] = _file_sha256(lm_path)

            # 6. Notes
            progress.setLabelText("Saving notes...")
            progress.setValue(6)
            slicer.app.processEvents()
            if notes:
                notes_filename = self.profile.get_export_filename("notes", pid)
                if notes_filename:
                    notes_path = os.path.join(save_dir, notes_filename)
                    with open(notes_path, "w", encoding="utf-8") as f:
                        f.write(notes + "\n")
                    exported_files.append(f"✓ Notes ({notes_filename})")

            # 7. Metadata + checksums
            progress.setLabelText("Saving metadata...")
            progress.setValue(7)
            slicer.app.processEvents()
            meta = self._build_metadata(zone_count, exported_files, notes, checksums)
            meta_path = os.path.join(save_dir, "export_metadata.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)

            progress.close()
            msg = f"✓ Export Complete!\n\nSaved to:\n{save_dir}\n\nFiles:\n"
            msg += "\n".join(exported_files)
            qt.QMessageBox.information(None, "Export Complete", msg + "\n\nClick OK to reset.")
            self.logic.hasUnsavedWork = False
            return True

        except Exception as e:
            progress.close()
            slicer.util.errorDisplay(f"Export failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def _build_metadata(
        self,
        zone_count: int,
        exported_files: list,
        notes: str,
        checksums: dict,
    ) -> dict:
        slicer_version = "unknown"
        if SLICER_AVAILABLE:
            try:
                slicer_version = slicer.app.applicationVersion
            except Exception:
                pass
        return {
            "patient_id": self.logic.currentId,
            "profile_id": self.profile.id,
            "profile_version": self.profile.version,
            "plugin_version": _PLUGIN_VERSION,
            "slicer_version": slicer_version,
            "export_date": datetime.now().isoformat(),
            "exported_files": exported_files,
            "zone_count": zone_count,
            "has_notes": bool(notes),
            "checksums": checksums,
        }


def _file_sha256(path: str) -> str:
    """Compute SHA-256 hex digest of a file (for reproducibility manifest)."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""
