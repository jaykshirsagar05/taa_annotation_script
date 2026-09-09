"""
DataLoader.py — File I/O layer for TAAAnnotationLogic.

Loads a case's CT volume and segmentation mask from a local directory into the
Slicer MRML scene. TAAAnnotationLogic delegates every loading call here.
"""

import os
import slicer

from TAAAnnotationLib import LocalDataset


class DataLoader:
    """Loads a case's NIfTI CT and segmentation mask into the MRML scene."""

    def __init__(self, logic):
        self._logic = logic

    # ------------------------------------------------------------------
    # High-level entry point
    # ------------------------------------------------------------------

    def loadCase(self, caseDir):
        """Load the CT and mask found in a scan directory.

        Returns a list of non-fatal error strings.

        Raises:
            RuntimeError: the directory is not a valid case, or the CT volume
                          could not be loaded (nothing else can proceed).
        """
        inputs = LocalDataset.find_inputs(caseDir)
        if "ct" not in inputs or "mask" not in inputs:
            raise RuntimeError(LocalDataset.describe_missing_inputs(caseDir))

        caseId = LocalDataset.case_id(caseDir)
        errors = []

        self._logic.reset()
        self._logic.caseDir = caseDir
        self._logic.currentId = caseId

        ct_path = self._fixFileExtension(inputs["ct"])
        print(f"[Loading] CT from: {ct_path}")
        slicer.util.showStatusMessage("Loading CT volume…")
        slicer.app.processEvents()

        volNode = slicer.util.loadVolume(ct_path)
        if not volNode:
            raise RuntimeError(f"Failed to load CT volume from {ct_path}")
        volNode.SetName(f"{caseId}_CT")
        self._logic.volNode = volNode

        self._setupViews()
        slicer.app.processEvents()

        try:
            slicer.util.showStatusMessage("Loading segmentation mask…")
            slicer.app.processEvents()
            self._logic.segNode = self._loadSegmentationMask(inputs["mask"])
            if not self._logic.segNode:
                raise RuntimeError("All loading strategies failed")
        except Exception as e:
            msg = f"Segmentation mask: {e}"
            print(f"[Loading] ERROR — {msg}")
            errors.append(msg)

        self._logic.workflowState["phase"] = 1
        self._logic.hasUnsavedWork = False

        print(f"[Loading] Complete — case {caseId}")
        if errors:
            print(f"[Loading] Errors: {errors}")
        return errors

    # ------------------------------------------------------------------
    # Segmentation loading
    # ------------------------------------------------------------------

    def _loadSegmentationMask(self, seg_path):
        """Load a mask file as a vtkMRMLSegmentationNode with a 3D surface."""
        seg_path = self._fixFileExtension(seg_path)
        file_size = os.path.getsize(seg_path)
        print(f"[Loading] Segmentation mask from: {seg_path} (size={file_size} bytes)")
        if file_size == 0:
            raise RuntimeError("Mask file is empty (0 bytes)")

        if seg_path.endswith(".nrrd"):
            segNode = self._loadSegmentationFromNrrd(seg_path)
            if not segNode:
                raise RuntimeError("Failed to load NRRD segmentation mask")
        else:
            labelNode = self._loadNiftiAsLabelMap(seg_path)
            if not labelNode:
                return None

            segNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLSegmentationNode",
                f"{self._logic.currentId}_Segmentation"
            )
            slicer.util.showStatusMessage(
                "Importing label map to segmentation — this may take a minute…")
            slicer.app.processEvents()
            slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                labelNode, segNode
            )
            slicer.mrmlScene.RemoveNode(labelNode)

        slicer.util.showStatusMessage("Creating 3D surface representation…")
        slicer.app.processEvents()
        segNode.CreateClosedSurfaceRepresentation()
        if segNode.GetDisplayNode():
            segNode.GetDisplayNode().SetVisibility(True)
        print("[Loading] Segmentation mask loaded successfully")
        return segNode

    def _loadSegmentationFromNrrd(self, filepath):
        """Load a .seg.nrrd or .nrrd file directly as a segmentation node."""
        try:
            print(f"[Loading]   Loading NRRD segmentation: {os.path.basename(filepath)}")
            segNode = slicer.util.loadSegmentation(filepath)
            if segNode:
                segNode.SetName(f"{self._logic.currentId}_Segmentation")
                print(f"[Loading]   loadSegmentation succeeded "
                      f"({segNode.GetSegmentation().GetNumberOfSegments()} segments)")
                return segNode
        except Exception as e1:
            print(f"[Loading]   loadSegmentation failed: {e1}")

        try:
            print("[Loading]   Fallback: loading NRRD as label volume...")
            labelNode = slicer.util.loadLabelVolume(filepath)
            if labelNode:
                segNode = slicer.mrmlScene.AddNewNodeByClass(
                    "vtkMRMLSegmentationNode",
                    f"{self._logic.currentId}_Segmentation"
                )
                slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                    labelNode, segNode
                )
                slicer.mrmlScene.RemoveNode(labelNode)
                print("[Loading]   NRRD label-volume fallback succeeded")
                return segNode
        except Exception as e2:
            print(f"[Loading]   NRRD label-volume fallback failed: {e2}")

        return None

    # ------------------------------------------------------------------
    # View helpers
    # ------------------------------------------------------------------

    def _setupViews(self):
        """Configure 4-up view layout and set background volume."""
        if self._logic.volNode:
            (slicer.app.layoutManager()
             .sliceWidget('Red').sliceLogic()
             .GetSliceCompositeNode()
             .SetBackgroundVolumeID(self._logic.volNode.GetID()))
        slicer.app.layoutManager().setLayout(
            slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)

    # ------------------------------------------------------------------
    # Static helpers — no Slicer/logic dependency
    # ------------------------------------------------------------------

    @staticmethod
    def _fixFileExtension(filepath):
        """
        Ensure the file extension matches the actual content.

        Handles:
        - .nii.gz that is actually NRRD  → rename to .nrrd / .seg.nrrd
        - .nii.gz that is uncompressed NIfTI → rename to .nii
        - Already-correct files → returned unchanged
        """
        if not filepath.endswith('.nii.gz'):
            return filepath
        try:
            with open(filepath, 'rb') as f:
                header = f.read(512)
            if len(header) < 4:
                return filepath

            if header[0] == 0x1f and header[1] == 0x8b:
                return filepath

            if header[:4] == b'NRRD':
                is_seg = b'Segment' in header
                new_path = filepath.replace('.nii.gz',
                                            '.seg.nrrd' if is_seg else '.nrrd')
                os.rename(filepath, new_path)
                print(f"[Loading] Renamed {os.path.basename(filepath)} → "
                      f"{os.path.basename(new_path)} (NRRD detected)")
                return new_path

            import struct
            hdr_size = struct.unpack('<i', header[:4])[0]
            if hdr_size in (348, 540):
                new_path = filepath[:-3]  # strip .gz
                os.rename(filepath, new_path)
                print(f"[Loading] Renamed {os.path.basename(filepath)} → "
                      f"{os.path.basename(new_path)} (raw NIfTI)")
                return new_path

            return filepath
        except Exception as e:
            print(f"[Loading] _fixFileExtension warning: {e}")
            return filepath

    @staticmethod
    def _loadNiftiAsLabelMap(filepath):
        """
        Robustly load a NIfTI mask file as a vtkMRMLLabelMapVolumeNode.

        Tries, in order:
          1. slicer.util.loadLabelVolume
          2. slicer.util.loadVolume → convert to label map
          3. SimpleITK direct read  → push into label map node
        """
        try:
            node = slicer.util.loadLabelVolume(filepath)
            if node:
                print("[Loading]   Strategy 1 (loadLabelVolume) succeeded")
                return node
        except Exception as e1:
            print(f"[Loading]   Strategy 1 (loadLabelVolume) failed: {e1}")

        try:
            tmpVol = slicer.util.loadVolume(filepath)
            if tmpVol:
                labelNode = slicer.mrmlScene.AddNewNodeByClass(
                    "vtkMRMLLabelMapVolumeNode", "tmp_label_conv"
                )
                volLogic = slicer.modules.volumes.logic()
                volLogic.CreateLabelVolumeFromVolume(
                    slicer.mrmlScene, labelNode, tmpVol
                )
                slicer.mrmlScene.RemoveNode(tmpVol)
                print("[Loading]   Strategy 2 (loadVolume + convert) succeeded")
                return labelNode
        except Exception as e2:
            print(f"[Loading]   Strategy 2 (loadVolume + convert) failed: {e2}")

        try:
            import SimpleITK as sitk
            import sitkUtils

            print("[Loading]   Strategy 3: reading with SimpleITK...")
            sitkImage = sitk.ReadImage(filepath)

            pixel_type = sitkImage.GetPixelID()
            print(f"[Loading]   SimpleITK pixel type: {pixel_type} "
                  f"({sitkImage.GetPixelIDTypeAsString()})")
            if pixel_type in (sitk.sitkFloat32, sitk.sitkFloat64):
                sitkImage = sitk.Cast(sitkImage, sitk.sitkInt16)

            tempName = "tmp_sitk_label"
            sitkUtils.PushVolumeToSlicer(sitkImage, name=tempName,
                                         className='vtkMRMLLabelMapVolumeNode')
            labelNode = slicer.util.getNode(tempName)
            if labelNode:
                print("[Loading]   Strategy 3 (SimpleITK) succeeded")
                return labelNode
        except Exception as e3:
            print(f"[Loading]   Strategy 3 (SimpleITK) failed: {e3}")

        print("[Loading]   All loading strategies exhausted — returning None")
        return None
