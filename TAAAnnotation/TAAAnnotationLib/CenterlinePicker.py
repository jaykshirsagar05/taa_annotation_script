import vtk
import slicer
import qt
import numpy as np

class CenterlinePicker:
    """Handles centerline clicking and zone point placement"""
    
    def __init__(self, logic, updateCallback):
        self.logic = logic
        self.updateCallback = updateCallback
        self.isActive = False
        self.centerlineNode = None
        self.previewNode = None
        self.previewPlaneNode = None
        self.currentPreviewPos = None
        self.currentPreviewId = -1
        self.clickObserverTag = None
        self.originalPicker = None
        self.originalCenterlineColor = None
        self.cellPicker = None

    def toggleClickMode(self, centerlineNode):
        """Toggle click mode on/off"""
        if self.isActive:
            self.disable()
            return True
        else:
            return self.enable(centerlineNode)

    def enable(self, centerlineNode):
        """Enable centerline click mode"""
        if not centerlineNode:
            slicer.util.warningDisplay("Please select a centerline model first.")
            return False
        
        if not centerlineNode.GetPolyData() or centerlineNode.GetPolyData().GetNumberOfPoints() == 0:
            slicer.util.warningDisplay("Selected model has no points.")
            return False
        
        self.centerlineNode = centerlineNode
        
        # Create zone node if needed
        self.logic.createZoneNode()
        
        # Create preview node
        self.previewNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLMarkupsFiducialNode", "Zone_Preview_Temp"
        )
        self.previewNode.GetDisplayNode().SetSelectedColor(0, 1, 1)
        self.previewNode.GetDisplayNode().SetGlyphScale(4.0)
        self.previewNode.GetDisplayNode().SetTextScale(0)
        
        # Highlight centerline
        displayNode = self.centerlineNode.GetDisplayNode()
        if displayNode:
            displayNode.SetVisibility(True)
            self.originalCenterlineColor = displayNode.GetColor()
            displayNode.SetColor(0, 1, 0)
            displayNode.SetLineWidth(3)
        
        # Setup picking
        self._setupPointPicking()
        
        self.isActive = True
        return True

    def disable(self):
        """Disable click mode"""
        # Restore centerline color
        if self.centerlineNode and self.originalCenterlineColor:
            displayNode = self.centerlineNode.GetDisplayNode()
            if displayNode:
                displayNode.SetColor(self.originalCenterlineColor)
                displayNode.SetLineWidth(1)
        
        # Remove observer
        self._removePointPicking()
        
        # Remove preview nodes
        if self.previewNode:
            slicer.mrmlScene.RemoveNode(self.previewNode)
            self.previewNode = None
        
        if self.previewPlaneNode:
            slicer.mrmlScene.RemoveNode(self.previewPlaneNode)
            self.previewPlaneNode = None
        
        self.isActive = False
        self.currentPreviewPos = None
        self.currentPreviewId = -1

    def _setupPointPicking(self):
        """Setup VTK point picking"""
        threeDWidget = slicer.app.layoutManager().threeDWidget(0)
        if not threeDWidget:
            return
        
        threeDView = threeDWidget.threeDView()
        renderWindow = threeDView.renderWindow()
        interactor = renderWindow.GetInteractor()
        
        self.cellPicker = vtk.vtkCellPicker()
        self.cellPicker.SetTolerance(0.005)
        
        self.originalPicker = interactor.GetPicker()
        interactor.SetPicker(self.cellPicker)
        
        self.clickObserverTag = interactor.AddObserver(
            vtk.vtkCommand.LeftButtonPressEvent,
            self._onCenterlineClicked,
            1.0
        )
        
        threeDView.setCursor(qt.Qt.CrossCursor)


    def _removePointPicking(self):
        """Remove point picking"""
        threeDWidget = slicer.app.layoutManager().threeDWidget(0)
        if not threeDWidget:
            return
        
        threeDView = threeDWidget.threeDView()
        renderWindow = threeDView.renderWindow()
        interactor = renderWindow.GetInteractor()
        
        if self.clickObserverTag:
            interactor.RemoveObserver(self.clickObserverTag)
            self.clickObserverTag = None
        
        if self.originalPicker:
            interactor.SetPicker(self.originalPicker)
        
        threeDView.setCursor(qt.Qt.ArrowCursor)

    def _onCenterlineClicked(self, caller, event):
        """Handle centerline click"""
        if not self.isActive:
            return
        
        interactor = caller
        clickPos = interactor.GetEventPosition()
        
        threeDWidget = slicer.app.layoutManager().threeDWidget(0)
        threeDView = threeDWidget.threeDView()
        renderer = threeDView.renderWindow().GetRenderers().GetFirstRenderer()
        
        self.cellPicker.Pick(clickPos[0], clickPos[1], 0, renderer)
        
        if self.cellPicker.GetCellId() >= 0:
            worldPos = self.cellPicker.GetPickPosition()
            
            try:
                nearestPointId = self._findNearestCenterlinePoint(worldPos)
                if nearestPointId >= 0:
                    exactPos = self.centerlineNode.GetPolyData().GetPoints().GetPoint(nearestPointId)
                    self._updatePreviewState(exactPos, nearestPointId)
                    # NEW: Call the callback to update UI (enable Confirm button)
                    if self.updateCallback:
                        self.updateCallback()
            except Exception as e:
                import traceback
                traceback.print_exc()

    def _findNearestCenterlinePoint(self, worldPos):
        """Find nearest point on centerline"""
        if not self.centerlineNode or not self.centerlineNode.GetPolyData():
            return -1
        
        locator = vtk.vtkPointLocator()
        locator.SetDataSet(self.centerlineNode.GetPolyData())
        locator.BuildLocator()
        
        return locator.FindClosestPoint(worldPos)

    def _updatePreviewState(self, pos, pointId):
        """Update preview point and cross-section"""
        self.currentPreviewPos = pos
        self.currentPreviewId = pointId
        
        # Update preview markup
        if self.previewNode:
            self.previewNode.RemoveAllControlPoints()
            self.previewNode.AddControlPoint(pos[0], pos[1], pos[2])
            self.previewNode.SetNthControlPointLabel(0, "Preview")
        
        # Calculate tangent
        tangent = self._getTangentAtPoint(self.centerlineNode.GetPolyData(), pointId)
        
        # Update Yellow slice
        yellowSlice = slicer.app.layoutManager().sliceWidget('Yellow')
        yellowLogic = yellowSlice.sliceLogic()
        n = np.array(tangent)
        n = n / np.linalg.norm(n)
        
        a = np.array([0, 0, 1]) if abs(n[2]) < 0.9 else np.array([0, 1, 0])
        t1 = np.cross(n, a)
        t1 = t1 / np.linalg.norm(t1)
        
        yellowLogic.GetSliceNode().SetSliceToRASByNTP(
            n[0], n[1], n[2],
            t1[0], t1[1], t1[2],
            pos[0], pos[1], pos[2], 0
        )
        yellowSlice.sliceLogic().GetSliceNode().SetSliceVisible(False)
        
        # Create preview plane
        self._createOrUpdatePreviewPlane(pos, n, t1)

    def _getTangentAtPoint(self, polydata, pointId):
        """Calculate tangent at centerline point"""
        pointData = polydata.GetPointData()
        tangents = pointData.GetArray("Tangents") or pointData.GetArray("FrenetTangent")
        if tangents:
            return tangents.GetTuple3(pointId)
        
        # Fallback: geometric calculation
        nPoints = polydata.GetNumberOfPoints()
        idxPrev = max(0, pointId - 1)
        idxNext = min(nPoints - 1, pointId + 1)
        
        pPrev = list(polydata.GetPoint(idxPrev))
        pNext = list(polydata.GetPoint(idxNext))
        
        import math
        vec = [pNext[i] - pPrev[i] for i in range(3)]
        norm = math.sqrt(sum(x*x for x in vec))
        
        if norm > 1e-6:
            return [x/norm for x in vec]
        return [0, 0, 1]

    def _createOrUpdatePreviewPlane(self, center, normal, xAxis):
        """Create visual preview plane"""
        if self.previewPlaneNode:
            slicer.mrmlScene.RemoveNode(self.previewPlaneNode)
            self.previewPlaneNode = None
        
        disk = vtk.vtkDiskSource()
        disk.SetInnerRadius(0)
        disk.SetOuterRadius(30)
        disk.SetRadialResolution(30)
        disk.SetCircumferentialResolution(30)
        
        yAxis = np.cross(normal, xAxis)
        yAxis = yAxis / np.linalg.norm(yAxis)
        
        matrix = vtk.vtkMatrix4x4()
        for i in range(3):
            matrix.SetElement(i, 0, xAxis[i])
            matrix.SetElement(i, 1, yAxis[i])
            matrix.SetElement(i, 2, normal[i])
            matrix.SetElement(i, 3, center[i])
        
        transform = vtk.vtkTransform()
        transform.SetMatrix(matrix)
        
        transformFilter = vtk.vtkTransformPolyDataFilter()
        transformFilter.SetInputConnection(disk.GetOutputPort())
        transformFilter.SetTransform(transform)
        transformFilter.Update()
        
        self.previewPlaneNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode", "Preview_Plane_Temp")
        self.previewPlaneNode.SetAndObservePolyData(transformFilter.GetOutput())
        self.previewPlaneNode.CreateDefaultDisplayNodes()
        
        display = self.previewPlaneNode.GetDisplayNode()
        if display:
            display.SetColor(1, 0, 0)
            display.SetOpacity(0.5)
            display.SetBackfaceCulling(False)
            display.SetVisibility(True)
        
        slicer.app.processEvents()
        threeDWidget = slicer.app.layoutManager().threeDWidget(0)
        if threeDWidget:
            threeDWidget.threeDView().forceRender()

    def confirmZonePoint(self, zoneName):
        """Confirm and add current preview point with specified zone name"""
        if not self.currentPreviewPos:
            return
        
        result = self.logic.addZonePoint(self.currentPreviewPos, zoneName)
        
        if result is None:
            slicer.util.warningDisplay("Maximum 10 zones reached.")
            return
        
        # Clean up preview
        if self.previewNode:
            self.previewNode.RemoveAllControlPoints()
        
        if self.previewPlaneNode:
            slicer.mrmlScene.RemoveNode(self.previewPlaneNode)
            self.previewPlaneNode = None
        
        self.currentPreviewPos = None
        self.currentPreviewId = -1
        
        if self.updateCallback:
            self.updateCallback()