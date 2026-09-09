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

            self._binarizeSegmentationNode(self._logic.segNode)

            slicer.util.selectModule("SegmentEditor")

            segmentEditorNode = slicer.mrmlScene.GetSingletonNode(
                "SegmentEditor", "vtkMRMLSegmentEditorNode"
            )
            if segmentEditorNode:
                segmentEditorNode.SetAndObserveSegmentationNode(self._logic.segNode)
                segmentEditorNode.SetAndObserveSourceVolumeNode(self._logic.volNode)

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

    def buildPickingSurface(self):
        """Build the translucent aorta surface used for 3D centerline picking.

        Also hides the opaque segmentations. CenterlinePicker's vtkCellPicker
        hits whatever is frontmost in the 3D view and then snaps the hit
        position to the nearest centerline point, so the wall must be
        translucent and nothing opaque may sit in front of it — otherwise the
        centerline is buried inside a solid surface and cannot be hovered,
        clicked, or seen.

        Returns the surface model node, or None when no surface could be built.
        """
        import vtk

        if not self._logic.segNode:
            return None

        existing = slicer.mrmlScene.GetFirstNodeByName(self._pickingSurfaceName())
        if existing is not None:
            slicer.mrmlScene.RemoveNode(existing)

        tempLabel = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLabelMapVolumeNode", "temp_refined_label"
        )
        slicer.modules.segmentations.logic().ExportAllSegmentsToLabelmapNode(
            self._logic.segNode, tempLabel,
            slicer.vtkSegmentation.EXTENT_REFERENCE_GEOMETRY
        )
        array = slicer.util.arrayFromVolume(tempLabel)
        array[array > 0] = 1
        slicer.util.updateVolumeFromArray(tempLabel, array)

        binarizedNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSegmentationNode",
            f"{self._logic.currentId}_refined_binary"
        )
        slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
            tempLabel, binarizedNode
        )
        binarizedNode.CreateClosedSurfaceRepresentation()
        slicer.mrmlScene.RemoveNode(tempLabel)

        segmentId = binarizedNode.GetSegmentation().GetNthSegmentID(0)
        polyData = vtk.vtkPolyData()
        binarizedNode.GetClosedSurfaceRepresentation(segmentId, polyData)
        if polyData.GetNumberOfPoints() == 0:
            print("[VMTK] Refined segmentation produced an empty surface")
            slicer.mrmlScene.RemoveNode(binarizedNode)
            return None

        surfaceModel = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLModelNode", self._pickingSurfaceName()
        )
        surfaceModel.SetAndObservePolyData(polyData)
        surfaceModel.CreateDefaultDisplayNodes()
        displayNode = surfaceModel.GetDisplayNode()
        if displayNode:
            displayNode.SetVisibility(True)
            displayNode.SetOpacity(0.3)
            displayNode.SetColor(0.8, 0.8, 0.0)

        for node in (self._logic.segNode, binarizedNode):
            if node and node.GetDisplayNode():
                node.GetDisplayNode().SetVisibility(False)

        print("[VMTK] Picking surface ready — segmentation hidden")
        return surfaceModel

    def _pickingSurfaceName(self):
        return f"{self._logic.currentId}_refined_binary_surface"

    def setupVMTK(self):
        """Create output nodes and wire them into the ExtractCenterline widget."""
        try:
            if not hasattr(slicer.modules, 'extractcenterline'):
                slicer.util.errorDisplay("VMTK Extension not installed")
                return False

            if not self._logic.segNode:
                slicer.util.errorDisplay("No refined segmentation available")
                return False

            inputSurfaceModel = self.buildPickingSurface()

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
        """Return a vtkPolyData closed surface of the refined segmentation, or None."""
        import vtk
        node = self._logic.segNode
        if node is None:
            return None
        seg = node.GetSegmentation()
        if seg is None or seg.GetNumberOfSegments() == 0:
            return None
        node.CreateClosedSurfaceRepresentation()
        polyData = vtk.vtkPolyData()
        node.GetClosedSurfaceRepresentation(seg.GetNthSegmentID(0), polyData)
        if polyData.GetNumberOfPoints() > 0:
            return polyData
        print("[getAortaSurfacePolyData] No valid surface found in segNode")
        return None
