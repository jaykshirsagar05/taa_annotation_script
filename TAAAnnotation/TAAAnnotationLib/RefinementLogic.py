"""
RefinementLogic.py — Mask refinement and VMTK centerline setup.

Extracted from TAAAnnotationLogic to separate the VMTK-heavy pipeline from
loading and zone management concerns.
"""

import slicer


class RefinementLogic:
    """Handles mask binarization, segment editor setup, and VMTK configuration."""

    def __init__(self, logic):
        self._logic = logic

    def setupRefinement(self):
        """Open the Segment Editor with the loaded segmentation pre-wired."""
        try:
            if not self._logic.segNode:
                slicer.util.errorDisplay("No segmentation loaded")
                return False

            if getattr(self._logic.activeProfile, 'binarize_mask_before_refine', False):
                self._binarizeSegmentationNode(self._logic.segNode)

            slicer.util.selectModule("SegmentEditor")

            segmentEditorNode = slicer.mrmlScene.GetSingletonNode(
                "SegmentEditor", "vtkMRMLSegmentEditorNode"
            )
            if segmentEditorNode:
                segmentEditorNode.SetAndObserveSegmentationNode(self._logic.segNode)
                segmentEditorNode.SetAndObserveSourceVolumeNode(self._logic.volNode)

            if self._logic.refNode:
                self._logic.refNode.GetDisplayNode().SetVisibility(False)
            self._logic.segNode.GetDisplayNode().SetVisibility(True)

            self._logic.workflowState["phase"] = 2
            return True

        except Exception as e:
            slicer.util.errorDisplay(f"Failed to setup refinement: {str(e)}")
            return False

    def _binarizeSegmentationNode(self, segNode):
        """Merge all segments into a single binary segment (label 0/1)."""
        try:
            tempLabel = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLLabelMapVolumeNode", "_temp_binarize"
            )
            slicer.modules.segmentations.logic().ExportAllSegmentsToLabelmapNode(
                segNode, tempLabel, slicer.vtkSegmentation.EXTENT_REFERENCE_GEOMETRY
            )
            array = slicer.util.arrayFromVolume(tempLabel)
            array[array > 0] = 1
            slicer.util.updateVolumeFromArray(tempLabel, array)
            segNode.GetSegmentation().RemoveAllSegments()
            slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                tempLabel, segNode
            )
            slicer.mrmlScene.RemoveNode(tempLabel)
            segNode.CreateClosedSurfaceRepresentation()
            print("[Binarize] Segmentation mask binarized to single binary segment")
        except Exception as e:
            print(f"[Binarize] WARNING — failed to binarize mask: {e}")

    def setupVMTK(self):
        """Create output nodes and wire them into the ExtractCenterline widget."""
        import vtk

        try:
            if not hasattr(slicer.modules, 'extractcenterline'):
                slicer.util.errorDisplay("VMTK Extension not installed")
                return False

            if not self._logic.segNode:
                slicer.util.errorDisplay("No refined segmentation available")
                return False

            # Export refined segmentation to temporary label volume and binarize
            tempRefinedLabel = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLLabelMapVolumeNode", "temp_refined_label"
            )
            slicer.modules.segmentations.logic().ExportAllSegmentsToLabelmapNode(
                self._logic.segNode, tempRefinedLabel,
                slicer.vtkSegmentation.EXTENT_REFERENCE_GEOMETRY
            )
            array = slicer.util.arrayFromVolume(tempRefinedLabel)
            array[array > 0] = 1
            slicer.util.updateVolumeFromArray(tempRefinedLabel, array)

            binarizedRefinedNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLSegmentationNode",
                f"{self._logic.currentId}_refined_binary"
            )
            slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                tempRefinedLabel, binarizedRefinedNode
            )
            binarizedRefinedNode.CreateClosedSurfaceRepresentation()
            slicer.mrmlScene.RemoveNode(tempRefinedLabel)

            slicer.util.selectModule("ExtractCenterline")
            slicer.app.processEvents()

            vmtkWidget = slicer.modules.extractcenterline.widgetRepresentation()
            if not vmtkWidget:
                raise RuntimeError("Failed to get VMTK widget")

            widgetSelf = vmtkWidget.self()
            parameterNode = widgetSelf._parameterNode
            if not parameterNode:
                parameterNode = widgetSelf.logic.getParameterNode()

            # Create output nodes
            self._logic.networkNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLModelNode", f"{self._logic.currentId}_Network"
            )
            self._logic.endpointNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsFiducialNode", f"{self._logic.currentId}_Endpoints"
            )
            self._logic.centerlineNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLModelNode", f"{self._logic.currentId}_Centerline"
            )

            # Build a surface model from the binarized refined mask
            inputSurfaceModel = None
            if binarizedRefinedNode:
                inputSurfaceModel = slicer.mrmlScene.AddNewNodeByClass(
                    "vtkMRMLModelNode",
                    f"{self._logic.currentId}_refined_binary_surface"
                )
                segmentation = binarizedRefinedNode.GetSegmentation()
                segmentId = segmentation.GetNthSegmentID(0)
                binarizedRefinedNode.CreateClosedSurfaceRepresentation()
                polyData = vtk.vtkPolyData()
                binarizedRefinedNode.GetClosedSurfaceRepresentation(segmentId, polyData)

                if polyData.GetNumberOfPoints() > 0:
                    inputSurfaceModel.SetAndObservePolyData(polyData)
                    inputSurfaceModel.CreateDefaultDisplayNodes()
                    displayNode = inputSurfaceModel.GetDisplayNode()
                    if displayNode:
                        displayNode.SetVisibility(True)
                        displayNode.SetOpacity(0.3)
                        displayNode.SetColor(0.8, 0.8, 0.0)

            if inputSurfaceModel:
                parameterNode.SetNodeReferenceID("InputSurface", inputSurfaceModel.GetID())
            parameterNode.SetNodeReferenceID(
                "OutputCenterlineModel", self._logic.centerlineNode.GetID())
            parameterNode.SetNodeReferenceID(
                "CenterlineModel", self._logic.centerlineNode.GetID())
            parameterNode.SetNodeReferenceID(
                "NetworkModel", self._logic.networkNode.GetID())
            parameterNode.SetNodeReferenceID(
                "EndPoints", self._logic.endpointNode.GetID())

            # Hide other segmentations so only the surface mesh is visible
            for node in [self._logic.segNode, self._logic.refNode,
                         self._logic.binarizedMergedNode]:
                if node:
                    node.GetDisplayNode().SetVisibility(False)
            if binarizedRefinedNode:
                binarizedRefinedNode.GetDisplayNode().SetVisibility(False)

            widgetSelf.updateGUIFromParameterNode()
            slicer.app.processEvents()

            self._logic.workflowState["phase"] = 3
            return True

        except Exception as e:
            slicer.util.errorDisplay(f"Failed to setup VMTK: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

    def getAortaSurfacePolyData(self):
        """Extract a vtkPolyData closed surface from the refined segmentation.

        Tries segNode first (refined mask), falls back to refNode.
        Returns vtkPolyData, or None if no segmentation is available.
        """
        import vtk
        for candidate in (self._logic.segNode, self._logic.refNode):
            if candidate is None:
                continue
            seg = candidate.GetSegmentation()
            if seg is None or seg.GetNumberOfSegments() == 0:
                continue
            candidate.CreateClosedSurfaceRepresentation()
            segmentId = seg.GetNthSegmentID(0)
            polyData = vtk.vtkPolyData()
            candidate.GetClosedSurfaceRepresentation(segmentId, polyData)
            if polyData.GetNumberOfPoints() > 0:
                return polyData
        print("[getAortaSurfacePolyData] No valid surface found in segNode or refNode")
        return None
