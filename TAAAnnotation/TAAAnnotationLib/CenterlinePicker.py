"""
CenterlinePicker.py - Interactive SVS/STS zone landmark placement on aortic centerlines.

Provides:
    - SVS_STS_LANDMARKS: Canonical landmark definitions (labels, descriptions, colors).
    - CenterlinePicker: Click-to-pick interaction with live hover preview,
      cross-section plane visualisation, and per-zone centerline colouring.

Reference:
    Fillinger MF et al. "Reporting standards for thoracic endovascular
    aortic repair (TEVAR)", J Vasc Surg, 2010.
"""

import vtk
import slicer
import qt
import numpy as np


# ---------------------------------------------------------------------------
#  Qt wheel-event filter used during centerline scroll navigation
# ---------------------------------------------------------------------------

class _YellowWheelFilter(qt.QObject):
    """Installed on the Yellow slice-view widget when a zone preview is active.

    Intercepts Qt-level QWheelEvent before it reaches VTK so the scroll wheel
    steps along the centerline instead of moving the slice stack.
    Returning True from eventFilter() fully consumes the event.
    """

    def __init__(self, picker):
        super().__init__()
        self._picker = picker

    def eventFilter(self, obj, event):
        if event.type() == qt.QEvent.Wheel:
            try:
                delta = event.angleDelta().y()
            except AttributeError:
                delta = event.delta()   # Qt4-style fallback
            self._picker._scrollCenterline(+1 if delta > 0 else -1)
            return True   # consume — Yellow slice must not also scroll
        return False


# ---------------------------------------------------------------------------
#  SVS / STS Aortic Zone Landmark Definitions
# ---------------------------------------------------------------------------
# Each entry marks a *boundary* between consecutive zones along the aorta.
# The annotator places exactly these 10 landmarks on the extracted centerline.
# ---------------------------------------------------------------------------

SVS_STS_LANDMARKS = [
    {
        "id": 0,
        "label": "STJ",
        "name": "Sinotubular Junction",
        "description": (
            "Junction of the aortic root and ascending aorta, "
            "just above the sinuses of Valsalva."
        ),
        "color": [1.0, 0.20, 0.20],
        "zone_after": "Zone 0 \u2014 Ascending Aorta",
    },
    {
        "id": 1,
        "label": "BCA",
        "name": "Brachiocephalic Artery",
        "description": (
            "Origin of the brachiocephalic (innominate) artery "
            "from the aortic arch."
        ),
        "color": [1.0, 0.55, 0.0],
        "zone_after": "Zone 1 \u2014 Proximal Arch",
    },
    {
        "id": 2,
        "label": "LCCA",
        "name": "Left Common Carotid",
        "description": (
            "Origin of the left common carotid artery from the "
            "aortic arch."
        ),
        "color": [0.90, 0.85, 0.0],
        "zone_after": "Zone 2 \u2014 Distal Arch",
    },
    {
        "id": 3,
        "label": "LSA",
        "name": "Left Subclavian Artery",
        "description": (
            "Origin of the left subclavian artery from the "
            "aortic arch."
        ),
        "color": [0.20, 0.80, 0.20],
        "zone_after": "Zone 3 \u2014 Prox. Descending",
    },
    {
        "id": 4,
        "label": "PDescA",
        "name": "Proximal Descending",
        "description": (
            "Approximately 2\u2009cm distal to the left subclavian "
            "artery origin."
        ),
        "color": [0.0, 0.80, 0.80],
        "zone_after": "Zone 4 \u2014 Mid Descending",
    },
    {
        "id": 5,
        "label": "MDescA",
        "name": "Mid Descending",
        "description": (
            "Mid-level of the descending thoracic aorta "
            "(approximately at the pulmonary artery bifurcation)."
        ),
        "color": [0.20, 0.40, 1.0],
        "zone_after": "Zone 5 \u2014 Distal Descending",
    },
    {
        "id": 6,
        "label": "CT",
        "name": "Celiac Trunk",
        "description": (
            "Origin of the celiac trunk at the diaphragmatic level."
        ),
        "color": [0.55, 0.0, 1.0],
        "zone_after": "Zone 6 \u2014 Suprarenal Abdominal",
    },
    {
        "id": 7,
        "label": "SMA",
        "name": "Superior Mesenteric Artery",
        "description": "Origin of the superior mesenteric artery.",
        "color": [0.90, 0.0, 0.90],
        "zone_after": "Zone 7 \u2014 Paravisceral",
    },
    {
        "id": 8,
        "label": "RA",
        "name": "Renal Arteries",
        "description": (
            "Level of the most inferior (lowest) renal artery origin."
        ),
        "color": [0.80, 0.40, 0.15],
        "zone_after": "Zone 8 \u2014 Infrarenal",
    },
    {
        "id": 9,
        "label": "ABif",
        "name": "Aortic Bifurcation",
        "description": (
            "Bifurcation of the aorta into the common iliac arteries."
        ),
        "color": [0.55, 0.55, 0.55],
        "zone_after": None,
    },
]


class CenterlinePicker:
    """Interactive SVS/STS zone landmark placement on a centerline model.

    Workflow
    --------
    1.  ``enable()`` activates the 3D-view interactor.
    2.  Mouse-hover shows a live preview sphere (throttled via QTimer).
    3.  Left-click locks the preview and shows a cross-section disk +
        updates the Yellow slice orientation.
    4.  ``confirmZonePoint()`` commits the point to the MRML zone node.
    5.  ``updateCenterlineZoneColors()`` paints a tube overlay between
        placed landmarks.
    """

    MAX_ZONES = 10

    # ------------------------------------------------------------------
    #  Lifecycle
    # ------------------------------------------------------------------

    def __init__(self, logic, updateCallback):
        self.logic = logic
        self.updateCallback = updateCallback

        # Interaction state
        self.isActive = False
        self.centerlineNode = None
        self.previewNode = None
        self.previewPlaneNode = None
        self.currentPreviewPos = None
        self.currentPreviewId = -1

        # VTK interactor bookkeeping
        self.clickObserverTag = None
        self.moveObserverTag = None
        self.originalPicker = None
        self.originalCenterlineColor = None
        self.originalCenterlineOpacity = None
        self.cellPicker = None

        # Zone tracking
        self.selectedZoneIndex = 0
        self.landmarkPointIds = {}   # zoneIndex -> centerline pointId

        # Zone colour overlay
        self.zoneColorNode = None

        # Hover throttle (80 ms)
        self._hoverTimer = qt.QTimer()
        self._hoverTimer.setInterval(80)
        self._hoverTimer.setSingleShot(True)
        self._hoverTimer.timeout.connect(self._performHoverPick)
        self._lastMousePos = None

        # Slice-view adjustment support
        self._previewObserverTag = None
        self._isSnapping = False
        self._snapTimer = qt.QTimer()
        self._snapTimer.setInterval(150)
        self._snapTimer.setSingleShot(True)
        self._snapTimer.timeout.connect(self._performSnapToLine)
        self._originalSliceIntersectionVisibility = None
        self._originalSliceIntersectionThickness = None

        # Feature 1: Cumulative arc-length distances along the centerline
        self._cumulativeDistances = None   # np.ndarray or None

        # Scroll navigation — walk along the centerline after a click
        self._centerlinePath = None        # ordered list of point IDs (main trunk)
        self._currentPathIndex = -1        # current position in _centerlinePath
        self._wheelFilter = None           # _YellowWheelFilter Qt event-filter instance

        # Feature 2: Diameter chord nodes (max = red, min = cyan)
        self._maxDiamNode = None           # vtkMRMLMarkupsLineNode
        self._minDiamNode = None           # vtkMRMLMarkupsLineNode

    # ------------------------------------------------------------------
    #  Public helpers
    # ------------------------------------------------------------------

    @staticmethod
    def getLandmarkDefs():
        """Return the SVS/STS landmark definitions list."""
        return SVS_STS_LANDMARKS

    def setSelectedZone(self, zoneIndex):
        """Set which landmark is being placed next."""
        self.selectedZoneIndex = max(0, min(zoneIndex, self.MAX_ZONES - 1))

    def getNextUnplacedZone(self):
        """Return the index of the first unplaced landmark, or -1."""
        for i in range(self.MAX_ZONES):
            if i not in self.landmarkPointIds:
                return i
        return -1

    def getPlacedCount(self):
        """Return how many landmarks have been placed."""
        return len(self.landmarkPointIds)

    # ------------------------------------------------------------------
    #  Enable / Disable
    # ------------------------------------------------------------------

    def toggleClickMode(self, centerlineNode):
        """Toggle click mode on/off.  Returns True on success."""
        if self.isActive:
            self.disable()
            return True
        else:
            return self.enable(centerlineNode)

    def enable(self, centerlineNode):
        """Enable centerline click mode."""
        if not centerlineNode:
            slicer.util.warningDisplay("Please select a centerline model first.")
            return False

        pd = centerlineNode.GetPolyData()
        if not pd or pd.GetNumberOfPoints() == 0:
            slicer.util.warningDisplay("Selected model has no points.")
            return False

        # Clean up if previously active
        if self.isActive:
            self.disable()

        self.centerlineNode = centerlineNode
        self._cumulativeDistances = None   # invalidate cache for new centerline
        self._centerlinePath = None
        self._currentPathIndex = -1

        # Ensure zone node exists
        self.logic.createZoneNode()

        # Rebuild landmark map from any existing zone points
        self._rebuildLandmarkMap()

        # Create preview fiducial (cyan, with label)
        self.previewNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLMarkupsFiducialNode", "Zone_Preview_Temp"
        )
        disp = self.previewNode.GetDisplayNode()
        disp.SetSelectedColor(0, 1, 1)
        disp.SetGlyphScale(4.5)
        disp.SetTextScale(3.5)
        self.previewNode.SetLocked(True)

        # Highlight centerline
        displayNode = self.centerlineNode.GetDisplayNode()
        if displayNode:
            displayNode.SetVisibility(True)
            self.originalCenterlineColor = displayNode.GetColor()
            self.originalCenterlineOpacity = displayNode.GetOpacity()
            displayNode.SetColor(0.4, 1.0, 0.4)
            displayNode.SetLineWidth(3)
            displayNode.SetOpacity(0.6)

            # Show centerline in all 2D slice views (Axial/Coronal/Sagittal)
            self._originalSliceIntersectionVisibility = (
                displayNode.GetSliceIntersectionVisibility()
            )
            self._originalSliceIntersectionThickness = (
                displayNode.GetSliceIntersectionThickness()
            )
            displayNode.SetSliceIntersectionVisibility(True)
            displayNode.SetSliceIntersectionThickness(3)

        # Install VTK observers
        if not self._setupPointPicking():
            return False

        self.isActive = True
        return True

    def disable(self):
        """Disable click mode and clean up transient nodes."""
        self._hoverTimer.stop()
        self._snapTimer.stop()
        self._stopPreviewObservation()
        self._stopYellowScrollObservation()
        self._currentPathIndex = -1

        # Restore centerline appearance
        if self.centerlineNode:
            displayNode = self.centerlineNode.GetDisplayNode()
            if displayNode:
                if self.originalCenterlineColor:
                    displayNode.SetColor(self.originalCenterlineColor)
                if self.originalCenterlineOpacity is not None:
                    displayNode.SetOpacity(self.originalCenterlineOpacity)
                displayNode.SetLineWidth(1)

                # Restore 2D slice intersection visibility
                if self._originalSliceIntersectionVisibility is not None:
                    displayNode.SetSliceIntersectionVisibility(
                        self._originalSliceIntersectionVisibility
                    )
                if self._originalSliceIntersectionThickness is not None:
                    displayNode.SetSliceIntersectionThickness(
                        self._originalSliceIntersectionThickness
                    )
                self._originalSliceIntersectionVisibility = None
                self._originalSliceIntersectionThickness = None

        # Hide Yellow slice plane in 3D and clean up chord nodes
        ys = slicer.app.layoutManager().sliceWidget("Yellow")
        if ys:
            ys.sliceLogic().GetSliceNode().SetSliceVisible(False)
        self._removeDiameterChords()

        self._removePointPicking()

        # Remove preview nodes
        if self.previewNode:
            slicer.mrmlScene.RemoveNode(self.previewNode)
            self.previewNode = None
        if self.previewPlaneNode:
            slicer.mrmlScene.RemoveNode(self.previewPlaneNode)
            self.previewPlaneNode = None

        self.isActive = False
        self.centerlineNode = None
        self._cumulativeDistances = None
        self._centerlinePath = None
        self.currentPreviewPos = None
        self.currentPreviewId = -1

    def cleanup(self):
        """Full teardown - call on module reset / scene close."""
        self.disable()
        self._removeZoneColorNode()
        self.landmarkPointIds = {}

    # ------------------------------------------------------------------
    #  Landmark map
    # ------------------------------------------------------------------

    def _rebuildLandmarkMap(self):
        """Rebuild ``landmarkPointIds`` from existing control-point labels."""
        self.landmarkPointIds = {}
        if not self.logic.zoneNode:
            return
        n = self.logic.zoneNode.GetNumberOfControlPoints()
        for i in range(n):
            label = self.logic.zoneNode.GetNthControlPointLabel(i)
            for lm in SVS_STS_LANDMARKS:
                if label == lm["label"] or label.startswith(f"Zone_{lm['id']}_"):
                    # Point-ID unknown for pre-existing / loaded zones -> -1
                    self.landmarkPointIds[lm["id"]] = -1
                    break

    def onLandmarkRemoved(self, label):
        """Notify the picker that a landmark was removed externally."""
        for lm in SVS_STS_LANDMARKS:
            if lm["label"] == label:
                self.landmarkPointIds.pop(lm["id"], None)
                break
        self.updateCenterlineZoneColors()

    # ------------------------------------------------------------------
    #  VTK Interactor setup
    # ------------------------------------------------------------------

    def _setupPointPicking(self):
        threeDWidget = slicer.app.layoutManager().threeDWidget(0)
        if not threeDWidget:
            slicer.util.warningDisplay("No 3D view available.")
            return False

        threeDView = threeDWidget.threeDView()
        if not threeDView:
            return False

        renderWindow = threeDView.renderWindow()
        interactor = renderWindow.GetInteractor()

        self.cellPicker = vtk.vtkCellPicker()
        self.cellPicker.SetTolerance(0.005)

        self.originalPicker = interactor.GetPicker()
        interactor.SetPicker(self.cellPicker)

        # Click -> lock preview
        self.clickObserverTag = interactor.AddObserver(
            vtk.vtkCommand.LeftButtonPressEvent,
            self._onCenterlineClicked,
            1.0,
        )

        # Mouse-move -> hover preview (non-blocking, low priority)
        self.moveObserverTag = interactor.AddObserver(
            vtk.vtkCommand.MouseMoveEvent,
            self._onMouseMove,
            0.0,
        )

        threeDView.setCursor(qt.Qt.CrossCursor)
        return True

    def _removePointPicking(self):
        threeDWidget = slicer.app.layoutManager().threeDWidget(0)
        if not threeDWidget:
            return

        threeDView = threeDWidget.threeDView()
        renderWindow = threeDView.renderWindow()
        interactor = renderWindow.GetInteractor()

        if self.clickObserverTag:
            interactor.RemoveObserver(self.clickObserverTag)
            self.clickObserverTag = None
        if self.moveObserverTag:
            interactor.RemoveObserver(self.moveObserverTag)
            self.moveObserverTag = None
        if self.originalPicker:
            interactor.SetPicker(self.originalPicker)

        threeDView.setCursor(qt.Qt.ArrowCursor)

    # ------------------------------------------------------------------
    #  Hover preview (throttled to ~80 ms)
    # ------------------------------------------------------------------

    def _onMouseMove(self, caller, event):
        if not self.isActive:
            return
        interactor = caller
        # Don't overwrite a locked click-preview with hover data
        if self.currentPreviewPos is not None:
            return
        # Skip while modifier keys are held (pan / rotate)
        if interactor.GetShiftKey() or interactor.GetControlKey():
            return
        self._lastMousePos = interactor.GetEventPosition()
        if not self._hoverTimer.isActive():
            self._hoverTimer.start()

    def _performHoverPick(self):
        if not self.isActive or self._lastMousePos is None:
            return
        try:
            threeDWidget = slicer.app.layoutManager().threeDWidget(0)
            if not threeDWidget:
                return
            renderer = (
                threeDWidget.threeDView()
                .renderWindow()
                .GetRenderers()
                .GetFirstRenderer()
            )
            if not self.cellPicker:
                return

            self.cellPicker.Pick(
                self._lastMousePos[0], self._lastMousePos[1], 0, renderer
            )

            if self.cellPicker.GetCellId() >= 0:
                worldPos = self.cellPicker.GetPickPosition()
                nearestPointId = self._findNearestCenterlinePoint(worldPos)
                if nearestPointId >= 0:
                    exactPos = self.centerlineNode.GetPolyData().GetPoints().GetPoint(
                        nearestPointId
                    )
                    self._updateHoverPreview(exactPos)
            else:
                self._clearHoverPreview()
        except Exception:
            pass

    def _updateHoverPreview(self, pos):
        """Lightweight preview sphere that follows the cursor along the centerline."""
        if not self.previewNode:
            return
        lm = (
            SVS_STS_LANDMARKS[self.selectedZoneIndex]
            if self.selectedZoneIndex < len(SVS_STS_LANDMARKS)
            else None
        )
        label = lm["label"] if lm else "?"

        # Feature 1: append arc-length distance from previous landmark
        self._ensureCumulativeDistances()
        hoverPid = self._findNearestCenterlinePoint(pos)
        dist, prevLabel = self._getDistanceFromPreviousLandmark(hoverPid)
        if dist is not None:
            label = f"{label} (+{dist:.1f} mm from {prevLabel})"

        if self.previewNode.GetNumberOfControlPoints() == 0:
            self.previewNode.AddControlPoint(pos[0], pos[1], pos[2])
        else:
            self.previewNode.SetNthControlPointPosition(0, pos[0], pos[1], pos[2])
        self.previewNode.SetNthControlPointLabel(0, label)
        if lm:
            self.previewNode.GetDisplayNode().SetSelectedColor(*lm["color"])

    def _clearHoverPreview(self):
        if self.previewNode and self.previewNode.GetNumberOfControlPoints() > 0:
            self.previewNode.RemoveAllControlPoints()

    # ------------------------------------------------------------------
    #  Click -> lock preview + cross-section
    # ------------------------------------------------------------------

    def _onCenterlineClicked(self, caller, event):
        if not self.isActive:
            return
        try:
            interactor = caller
            clickPos = interactor.GetEventPosition()

            threeDWidget = slicer.app.layoutManager().threeDWidget(0)
            if not threeDWidget:
                return
            renderer = (
                threeDWidget.threeDView()
                .renderWindow()
                .GetRenderers()
                .GetFirstRenderer()
            )
            if not self.cellPicker:
                return

            self.cellPicker.Pick(clickPos[0], clickPos[1], 0, renderer)

            if self.cellPicker.GetCellId() >= 0:
                worldPos = self.cellPicker.GetPickPosition()
                nearestPointId = self._findNearestCenterlinePoint(worldPos)
                if nearestPointId >= 0:
                    exactPos = self.centerlineNode.GetPolyData().GetPoints().GetPoint(
                        nearestPointId
                    )
                    self._updatePreviewState(exactPos, nearestPointId)
                    if self.updateCallback:
                        self.updateCallback()
        except Exception:
            import traceback
            traceback.print_exc()

    # ------------------------------------------------------------------
    #  Geometry helpers
    # ------------------------------------------------------------------

    def _findNearestCenterlinePoint(self, worldPos):
        if not self.centerlineNode or not self.centerlineNode.GetPolyData():
            return -1
        locator = vtk.vtkPointLocator()
        locator.SetDataSet(self.centerlineNode.GetPolyData())
        locator.BuildLocator()
        return locator.FindClosestPoint(worldPos)

    def _getTangentAtPoint(self, polydata, pointId):
        """Return the unit tangent vector at *pointId*."""
        pointData = polydata.GetPointData()
        tangents = pointData.GetArray("Tangents") or pointData.GetArray("FrenetTangent")
        if tangents:
            return tangents.GetTuple3(pointId)

        nPoints = polydata.GetNumberOfPoints()
        idxPrev = max(0, pointId - 1)
        idxNext = min(nPoints - 1, pointId + 1)

        pPrev = list(polydata.GetPoint(idxPrev))
        pNext = list(polydata.GetPoint(idxNext))

        import math
        vec = [pNext[i] - pPrev[i] for i in range(3)]
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 1e-6:
            return [x / norm for x in vec]
        return [0, 0, 1]

    def _buildCenterlinePath(self, polydata):
        """Walk the centerline graph; return the ordered main-trunk point-ID list.

        Works for both vtkPolyLine cells (one per branch) and individual
        vtkLine cells (one per segment), as produced by different VMTK /
        Slicer ExtractCenterline configurations.  Returns the longest path
        found (= main trunk).
        """
        nPoints = polydata.GetNumberOfPoints()
        if polydata.GetNumberOfCells() == 0 or nPoints == 0:
            return []

        # Build undirected adjacency list from all cells.
        adj = [[] for _ in range(nPoints)]
        for cellIdx in range(polydata.GetNumberOfCells()):
            cell = polydata.GetCell(cellIdx)
            nPts = cell.GetNumberOfPoints()
            for j in range(nPts - 1):
                a = cell.GetPointId(j)
                b = cell.GetPointId(j + 1)
                if b not in adj[a]:
                    adj[a].append(b)
                if a not in adj[b]:
                    adj[b].append(a)

        # Endpoints have degree 1 (trunk start / branch tip).
        endpoints = [i for i in range(nPoints) if len(adj[i]) == 1]
        if not endpoints:
            endpoints = [0]  # fallback for closed loops

        from collections import deque

        def reachable_count(node, excluded):
            count = 0
            queue = deque([node])
            seen = set(excluded) | {node}
            while queue:
                n = queue.popleft()
                count += 1
                for nb in adj[n]:
                    if nb not in seen:
                        seen.add(nb)
                        queue.append(nb)
            return count

        def guided_walk(start):
            path = [start]
            visited = {start}
            current = start
            while True:
                nxt = [n for n in adj[current] if n not in visited]
                if not nxt:
                    break
                if len(nxt) == 1:
                    current = nxt[0]
                else:
                    current = max(nxt, key=lambda n: reachable_count(n, visited))
                visited.add(current)
                path.append(current)
            return path

        best_path = []
        for ep in endpoints:
            p = guided_walk(ep)
            if len(p) > len(best_path):
                best_path = p

        return best_path

    def _buildCumulativeDistances(self, polydata):
        """Build a numpy array mapping each point index to its arc-length
        from the start of the main centerline path.

        Works for both vtkPolyLine cells (one cell per branch, many points)
        and individual vtkLine cells (one cell per segment, 2 points each),
        as produced by different VMTK/Slicer ExtractCenterline configurations.
        Builds a full adjacency graph, then walks the longest path.
        """
        nPoints = polydata.GetNumberOfPoints()
        distances = np.zeros(nPoints, dtype=np.float64)

        if polydata.GetNumberOfCells() == 0:
            return distances

        best_path = self._buildCenterlinePath(polydata)
        self._centerlinePath = best_path   # cache for scroll navigation

        if len(best_path) < 2:
            return distances

        # Assign cumulative arc-lengths to each point ID in the best path.
        cumDist = 0.0
        prevPt = np.array(polydata.GetPoint(best_path[0]))
        distances[best_path[0]] = 0.0
        for pid in best_path[1:]:
            currPt = np.array(polydata.GetPoint(pid))
            cumDist += np.linalg.norm(currPt - prevPt)
            distances[pid] = cumDist
            prevPt = currPt

        return distances

    def _ensureCumulativeDistances(self):
        """Populate _cumulativeDistances lazily if not yet built.

        Prefers the 'Length' point-data array produced by VMTK's
        vtkvmtkCenterlineGeometry (the same pass that generates FrenetTangent).
        Falls back to geometry-based computation when the array is absent
        (e.g. externally loaded .vtk files without VMTK geometry).
        """
        if self._cumulativeDistances is not None:
            return
        if not self.centerlineNode or not self.centerlineNode.GetPolyData():
            return

        pd = self.centerlineNode.GetPolyData()

        # Fast path: VMTK already stored cumulative arc-lengths in "Length".
        lengthArr = pd.GetPointData().GetArray("Length")
        if lengthArr is not None and lengthArr.GetNumberOfTuples() == pd.GetNumberOfPoints():
            nPts = pd.GetNumberOfPoints()
            self._cumulativeDistances = np.array(
                [lengthArr.GetValue(i) for i in range(nPts)],
                dtype=np.float64,
            )
            # Still need the ordered path for scroll navigation.
            if self._centerlinePath is None:
                self._centerlinePath = self._buildCenterlinePath(pd)
            return

        # Fallback: compute from cell connectivity (also populates _centerlinePath).
        self._cumulativeDistances = self._buildCumulativeDistances(pd)

    def _getDistanceFromPreviousLandmark(self, pointId):
        """Return arc-length distance from the previous placed landmark.

        Returns (distance_mm, label_str), or (None, None) if no previous
        landmark is available or distances are not cached.
        """
        if self._cumulativeDistances is None:
            return None, None

        prevIdx = None
        prevPid = -1
        for zIdx, pid in self.landmarkPointIds.items():
            if pid < 0:
                continue
            if zIdx < self.selectedZoneIndex:
                if prevIdx is None or zIdx > prevIdx:
                    prevIdx = zIdx
                    prevPid = pid

        if prevIdx is None or prevPid < 0:
            return None, None

        nPoints = len(self._cumulativeDistances)
        if pointId < 0 or pointId >= nPoints or prevPid >= nPoints:
            return None, None

        dist = abs(
            self._cumulativeDistances[pointId]
            - self._cumulativeDistances[prevPid]
        )
        label = SVS_STS_LANDMARKS[prevIdx]["label"]
        return dist, label

    def _computeCrossSectionDiameters(self, pos, normal, t1):
        """Cut the aorta surface at (pos, normal) and compute max/min lumen
        diameters via 2D PCA on the contour points.

        Returns (max_mm, min_mm, max_pt1_3d, max_pt2_3d, min_pt1_3d, min_pt2_3d)
        or None on failure.
        """
        surfPD = self.logic.getAortaSurfacePolyData()
        if surfPD is None or surfPD.GetNumberOfPoints() < 3:
            return None

        plane = vtk.vtkPlane()
        plane.SetOrigin(pos[0], pos[1], pos[2])
        plane.SetNormal(normal[0], normal[1], normal[2])

        cutter = vtk.vtkCutter()
        cutter.SetInputData(surfPD)
        cutter.SetCutFunction(plane)
        cutter.Update()

        # At curved sections (e.g. arch) the plane can intersect the aorta
        # wall twice, producing two separate contour loops.  Extract only the
        # loop whose centroid is closest to the centerline point so that PCA
        # operates on the correct single lumen cross-section.
        connFilter = vtk.vtkPolyDataConnectivityFilter()
        connFilter.SetInputData(cutter.GetOutput())
        connFilter.SetExtractionModeToClosestPointRegion()
        connFilter.SetClosestPoint(pos[0], pos[1], pos[2])
        connFilter.Update()
        cut = connFilter.GetOutput()

        if cut.GetNumberOfPoints() < 3:
            return None

        pos_np = np.array(pos, dtype=np.float64)
        t2 = np.cross(normal, t1)
        mag = np.linalg.norm(t2)
        if mag < 1e-9:
            return None
        t2 /= mag

        nCut = cut.GetNumberOfPoints()
        pts_2d = np.empty((nCut, 2), dtype=np.float64)
        for i in range(nCut):
            p = np.array(cut.GetPoint(i)) - pos_np
            pts_2d[i, 0] = np.dot(p, t1)
            pts_2d[i, 1] = np.dot(p, t2)

        cov = np.cov(pts_2d.T)
        eigenvalues, eigenvectors = np.linalg.eigh(cov)
        max_axis_2d = eigenvectors[:, 1]
        min_axis_2d = eigenvectors[:, 0]

        def _chord_endpoints(axis_2d):
            projections = pts_2d.dot(axis_2d)
            diameter = projections.max() - projections.min()
            half_extent = diameter / 2.0
            mid_proj = (projections.max() + projections.min()) / 2.0
            mid_pt_2d = mid_proj * axis_2d
            pt1_3d = pos_np + (mid_pt_2d - half_extent * axis_2d)[0] * t1 + \
                              (mid_pt_2d - half_extent * axis_2d)[1] * t2
            pt2_3d = pos_np + (mid_pt_2d + half_extent * axis_2d)[0] * t1 + \
                              (mid_pt_2d + half_extent * axis_2d)[1] * t2
            return diameter, pt1_3d, pt2_3d

        max_mm, max_pt1, max_pt2 = _chord_endpoints(max_axis_2d)
        min_mm, min_pt1, min_pt2 = _chord_endpoints(min_axis_2d)

        return max_mm, min_mm, max_pt1, max_pt2, min_pt1, min_pt2

    def _createOrUpdateDiameterChords(self, result, lm=None):
        """Create or update two line-markup chord nodes for max/min diameters."""
        self._removeDiameterChords()

        if result is None:
            return

        max_mm, min_mm, max_pt1, max_pt2, min_pt1, min_pt2 = result

        def _make_chord(name, pt1, pt2, color_rgb, label_text):
            lineNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsLineNode", name
            )
            # Convert to plain Python float — numpy scalars can silently
            # fail in some Slicer VTK Python bindings, placing points at origin.
            lineNode.AddControlPoint(float(pt1[0]), float(pt1[1]), float(pt1[2]))
            lineNode.AddControlPoint(float(pt2[0]), float(pt2[1]), float(pt2[2]))
            lineNode.SetLocked(True)
            disp = lineNode.GetDisplayNode()
            if disp is None:
                lineNode.CreateDefaultDisplayNodes()
                disp = lineNode.GetDisplayNode()
            if disp:
                disp.SetSelectedColor(*color_rgb)
                disp.SetColor(*color_rgb)
                disp.SetLineThickness(0.5)
                # GlyphScale 0 hides lines in some Slicer builds; use small positive.
                disp.SetGlyphScale(1.5)
                disp.SetTextScale(3.0)
                disp.SetSliceProjection(True)
                disp.SetSliceProjectionUseFiducialColor(True)
                # Hide the auto-generated node-name/measurement label.
                try:
                    disp.SetPropertiesLabelVisibility(False)
                except AttributeError:
                    pass
                disp.SetPointLabelsVisibility(True)
            lineNode.SetNthControlPointLabel(0, "")
            lineNode.SetNthControlPointLabel(1, label_text)
            return lineNode

        self._maxDiamNode = _make_chord(
            "Preview_MaxDiam_Temp",
            max_pt1, max_pt2,
            [1.0, 0.0, 0.0],
            f"Max: {max_mm:.1f} mm",
        )
        self._minDiamNode = _make_chord(
            "Preview_MinDiam_Temp",
            min_pt1, min_pt2,
            [0.0, 0.8, 1.0],
            f"Min: {min_mm:.1f} mm",
        )

    def _removeDiameterChords(self):
        """Remove max/min diameter chord nodes from the scene."""
        for attr in ("_maxDiamNode", "_minDiamNode"):
            node = getattr(self, attr, None)
            if node:
                try:
                    slicer.mrmlScene.RemoveNode(node)
                except Exception:
                    pass
            setattr(self, attr, None)

    # ------------------------------------------------------------------
    #  Preview state (after click)
    # ------------------------------------------------------------------

    def _updatePreviewState(self, pos, pointId):
        """Lock the preview location and show cross-section disk + slice views.

        After this call the preview fiducial is *unlocked* so the user can
        drag it in axial / coronal / sagittal slice views.  A snap-to-
        centerline timer re-projects the point on release.
        """
        self.currentPreviewPos = pos
        self.currentPreviewId = pointId

        # Pause hover while locked
        self._hoverTimer.stop()
        # Stop any active scroll observation before rebuilding the preview state.
        self._stopYellowScrollObservation()

        lm = (
            SVS_STS_LANDMARKS[self.selectedZoneIndex]
            if self.selectedZoneIndex < len(SVS_STS_LANDMARKS)
            else None
        )

        # Stop observing before modifying the preview node
        self._stopPreviewObservation()
        self._isSnapping = True

        # Update preview fiducial
        if self.previewNode:
            self.previewNode.RemoveAllControlPoints()
            self.previewNode.AddControlPoint(pos[0], pos[1], pos[2])
            label = lm["label"] if lm else "Preview"

            # Feature 1: append arc-length distance from previous landmark
            self._ensureCumulativeDistances()
            dist, prevLabel = self._getDistanceFromPreviousLandmark(pointId)
            if dist is not None:
                label = f"{label} (+{dist:.1f} mm from {prevLabel})"

            self.previewNode.SetNthControlPointLabel(0, f"\u25b6 {label}")
            if lm:
                self.previewNode.GetDisplayNode().SetSelectedColor(*lm["color"])

            # Unlock so the user can drag in slice views to adjust
            self.previewNode.SetLocked(False)

            # Ensure preview fiducial is visible in 2D slice views
            pDisp = self.previewNode.GetDisplayNode()
            if pDisp:
                # pDisp.SetSliceProjection(True)
                pDisp.SetSliceProjectionUseFiducialColor(True)

        self._isSnapping = False

        # Tangent and slice orientation
        tangent = self._getTangentAtPoint(self.centerlineNode.GetPolyData(), pointId)
        n = np.array(tangent, dtype=float)
        n /= np.linalg.norm(n)

        a = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
        t1 = np.cross(n, a)
        t1 /= np.linalg.norm(t1)

        # Orient Yellow slice as cross-section
        yellowSlice = slicer.app.layoutManager().sliceWidget("Yellow")
        yellowLogic = yellowSlice.sliceLogic()
        yellowLogic.GetSliceNode().SetSliceToRASByNTP(
            n[0], n[1], n[2],
            t1[0], t1[1], t1[2],
            pos[0], pos[1], pos[2], 0,
        )

        # Center Red (Axial) and Green (Coronal) slices on the point
        self._centerSliceViewsOnPoint(pos)

        # 3D disk
        self._createOrUpdatePreviewPlane(pos, n, t1, lm)

        # Feature 2: diameter chords on the cross-section
        diamResult = self._computeCrossSectionDiameters(pos, n, t1)
        self._createOrUpdateDiameterChords(diamResult, lm)

        # Start observing the preview node for user adjustments in slices
        self._startPreviewObservation()

        # Sync scroll-navigation index and enable Yellow-slice scroll.
        self._ensureCumulativeDistances()  # also populates _centerlinePath
        self._updatePathIndex(pointId)
        self._startYellowScrollObservation()

    def _createOrUpdatePreviewPlane(self, center, normal, xAxis, lm=None):
        if self.previewPlaneNode:
            slicer.mrmlScene.RemoveNode(self.previewPlaneNode)
            self.previewPlaneNode = None

        disk = vtk.vtkDiskSource()
        disk.SetInnerRadius(0)
        disk.SetOuterRadius(30)
        disk.SetRadialResolution(30)
        disk.SetCircumferentialResolution(30)

        yAxis = np.cross(normal, xAxis)
        yAxis /= np.linalg.norm(yAxis)

        matrix = vtk.vtkMatrix4x4()
        for i in range(3):
            matrix.SetElement(i, 0, xAxis[i])
            matrix.SetElement(i, 1, yAxis[i])
            matrix.SetElement(i, 2, normal[i])
            matrix.SetElement(i, 3, center[i])

        transform = vtk.vtkTransform()
        transform.SetMatrix(matrix)

        tf = vtk.vtkTransformPolyDataFilter()
        tf.SetInputConnection(disk.GetOutputPort())
        tf.SetTransform(transform)
        tf.Update()

        self.previewPlaneNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLModelNode", "Preview_Plane_Temp"
        )
        self.previewPlaneNode.SetAndObservePolyData(tf.GetOutput())
        self.previewPlaneNode.CreateDefaultDisplayNodes()

        color = lm["color"] if lm else [1, 0, 0]
        display = self.previewPlaneNode.GetDisplayNode()
        if display:
            display.SetColor(*color)
            display.SetOpacity(0.45)
            display.SetBackfaceCulling(False)
            display.SetVisibility(True)

        slicer.app.processEvents()
        threeDWidget = slicer.app.layoutManager().threeDWidget(0)
        if threeDWidget:
            threeDWidget.threeDView().forceRender()

    # ------------------------------------------------------------------
    #  Slice-view adjustment helpers
    # ------------------------------------------------------------------

    def _centerSliceViewsOnPoint(self, pos):
        """Center Red (Axial) and Green (Coronal) slices on *pos*."""
        for name in ["Red", "Green"]:
            sw = slicer.app.layoutManager().sliceWidget(name)
            if sw:
                sliceNode = sw.sliceLogic().GetSliceNode()
                sliceNode.JumpSlice(pos[0], pos[1], pos[2])

    def _startPreviewObservation(self):
        """Observe the preview fiducial for user drags in slice views."""
        self._stopPreviewObservation()
        if self.previewNode:
            self._previewObserverTag = self.previewNode.AddObserver(
                slicer.vtkMRMLMarkupsNode.PointModifiedEvent,
                self._onPreviewPointMoved,
            )

    def _stopPreviewObservation(self):
        """Remove the preview-point-moved observer."""
        if self._previewObserverTag is not None and self.previewNode:
            self.previewNode.RemoveObserver(self._previewObserverTag)
        self._previewObserverTag = None

    def _onPreviewPointMoved(self, caller, event):
        """Debounced handler for preview drag in slice views."""
        if self._isSnapping:
            return
        if not self._snapTimer.isActive():
            self._snapTimer.start()

    def _updatePathIndex(self, pointId):
        """Sync _currentPathIndex with the given centerline point ID."""
        if not self._centerlinePath:
            self._currentPathIndex = -1
            return
        try:
            self._currentPathIndex = self._centerlinePath.index(pointId)
        except ValueError:
            # Point is on a side branch — map to the closest main-trunk point.
            if not self.centerlineNode:
                self._currentPathIndex = -1
                return
            pd = self.centerlineNode.GetPolyData()
            clicked = np.array(pd.GetPoint(pointId))
            best_i, best_d = 0, float("inf")
            for i, pid in enumerate(self._centerlinePath):
                d = np.linalg.norm(np.array(pd.GetPoint(pid)) - clicked)
                if d < best_d:
                    best_d = d
                    best_i = i
            self._currentPathIndex = best_i

    # ------------------------------------------------------------------
    #  Yellow-slice scroll — walk along the centerline after a click
    # ------------------------------------------------------------------

    def _startYellowScrollObservation(self):
        """Install a Qt wheel event filter on the Yellow slice view so that
        scrolling steps along the centerline instead of the slice stack.

        Using a Qt-level filter (rather than a VTK observer) guarantees the
        event is consumed before VTK ever sees it.
        """
        self._stopYellowScrollObservation()
        ys = slicer.app.layoutManager().sliceWidget("Yellow")
        if not ys:
            return
        self._wheelFilter = _YellowWheelFilter(self)
        ys.sliceView().installEventFilter(self._wheelFilter)

    def _stopYellowScrollObservation(self):
        """Remove the Yellow-slice Qt wheel event filter."""
        if self._wheelFilter is not None:
            ys = slicer.app.layoutManager().sliceWidget("Yellow")
            if ys:
                try:
                    ys.sliceView().removeEventFilter(self._wheelFilter)
                except Exception:
                    pass
            self._wheelFilter = None

    def _scrollCenterline(self, delta):
        """Advance or retreat *delta* points along the centerline path.

        Positive *delta* moves toward higher path indices (distal direction
        as returned by the guided walk).  Negative moves proximally.
        Call with delta=+5 / -5 for coarser jumps if needed.
        """
        if not self._centerlinePath or self._currentPathIndex < 0:
            return
        if self.currentPreviewPos is None:
            return

        newIdx = max(
            0,
            min(len(self._centerlinePath) - 1, self._currentPathIndex + delta),
        )
        if newIdx == self._currentPathIndex:
            return

        newPointId = self._centerlinePath[newIdx]
        exactPos = self.centerlineNode.GetPolyData().GetPoints().GetPoint(newPointId)

        # _updatePreviewState stops+restarts scroll observers and calls
        # _updatePathIndex internally — save the target index so we can
        # restore it in case the path-lookup finds the same value already.
        self._updatePreviewState(exactPos, newPointId)
        # Ensure index matches the scroll target (not just the nearest point).
        self._currentPathIndex = newIdx

        if self.updateCallback:
            self.updateCallback()

    def _performSnapToLine(self):
        """Snap the preview fiducial to the nearest centerline point.

        Called after the user finishes dragging in a slice view.  Updates
        the cross-section disk and slice orientations accordingly.
        """
        if (
            not self.previewNode
            or self.previewNode.GetNumberOfControlPoints() == 0
        ):
            return

        pos = [0.0, 0.0, 0.0]
        self.previewNode.GetNthControlPointPosition(0, pos)

        nearestPointId = self._findNearestCenterlinePoint(pos)
        if nearestPointId < 0:
            return

        exactPos = (
            self.centerlineNode.GetPolyData().GetPoints().GetPoint(nearestPointId)
        )

        self._isSnapping = True
        try:
            self.previewNode.SetNthControlPointPosition(
                0, exactPos[0], exactPos[1], exactPos[2]
            )
            self.currentPreviewPos = exactPos
            self.currentPreviewId = nearestPointId
            self._updatePathIndex(nearestPointId)  # keep scroll index in sync

            # Recompute tangent / cross-section
            tangent = self._getTangentAtPoint(
                self.centerlineNode.GetPolyData(), nearestPointId
            )
            n = np.array(tangent, dtype=float)
            n /= np.linalg.norm(n)
            a = (
                np.array([0.0, 0.0, 1.0])
                if abs(n[2]) < 0.9
                else np.array([0.0, 1.0, 0.0])
            )
            t1 = np.cross(n, a)
            t1 /= np.linalg.norm(t1)

            lm = (
                SVS_STS_LANDMARKS[self.selectedZoneIndex]
                if self.selectedZoneIndex < len(SVS_STS_LANDMARKS)
                else None
            )

            # Update Yellow cross-section
            yellowSlice = slicer.app.layoutManager().sliceWidget("Yellow")
            if yellowSlice:
                yellowLogic = yellowSlice.sliceLogic()
                yellowLogic.GetSliceNode().SetSliceToRASByNTP(
                    n[0], n[1], n[2],
                    t1[0], t1[1], t1[2],
                    exactPos[0], exactPos[1], exactPos[2], 0,
                )

            # Re-center Axial / Coronal views
            self._centerSliceViewsOnPoint(exactPos)

            # Re-draw cross-section disk
            self._createOrUpdatePreviewPlane(exactPos, n, t1, lm)

            # Feature 2: update diameter chords
            diamResult = self._computeCrossSectionDiameters(exactPos, n, t1)
            self._createOrUpdateDiameterChords(diamResult, lm)
        finally:
            self._isSnapping = False

    # ------------------------------------------------------------------
    #  Confirm / commit zone point
    # ------------------------------------------------------------------

    def confirmZonePoint(self, zoneIndex=None):
        """Commit the current preview position as a zone landmark.

        If the landmark already exists it is *replaced* in-place, allowing
        easy corrections without undo-then-redo.

        Returns the landmark label string, or ``None`` on failure.
        """
        if self.currentPreviewPos is None:
            return None

        if zoneIndex is None:
            zoneIndex = self.selectedZoneIndex
        if zoneIndex < 0 or zoneIndex >= len(SVS_STS_LANDMARKS):
            return None

        lm = SVS_STS_LANDMARKS[zoneIndex]

        # --- Replace existing or add new ----------------------------------
        existingIdx = self._findExistingLandmark(zoneIndex)
        if existingIdx >= 0:
            # Update position of existing control point
            self.logic.zoneNode.SetNthControlPointPosition(
                existingIdx,
                self.currentPreviewPos[0],
                self.currentPreviewPos[1],
                self.currentPreviewPos[2],
            )
            self.logic.hasUnsavedWork = True
            print(f"[ZonePicker] Replaced {lm['label']} (cp #{existingIdx})")
        else:
            if (
                self.logic.zoneNode
                and self.logic.zoneNode.GetNumberOfControlPoints() >= self.MAX_ZONES
            ):
                slicer.util.warningDisplay("Maximum 10 zone landmarks reached.")
                return None
            result = self.logic.addZonePoint(self.currentPreviewPos, lm["label"])
            if result is None:
                return None
            print(f"[ZonePicker] Added {lm['label']}")

        # Track centreline point-ID for zone colouring
        self.landmarkPointIds[zoneIndex] = self.currentPreviewId

        # Stop slice-view adjustment and scroll observation
        self._stopPreviewObservation()
        self._snapTimer.stop()
        self._stopYellowScrollObservation()
        self._currentPathIndex = -1

        # Clean up preview artefacts
        if self.previewNode:
            self.previewNode.RemoveAllControlPoints()
            self.previewNode.SetLocked(True)
        if self.previewPlaneNode:
            slicer.mrmlScene.RemoveNode(self.previewPlaneNode)
            self.previewPlaneNode = None

        # Clean up diameter chords and hide Yellow slice plane
        self._removeDiameterChords()
        ys = slicer.app.layoutManager().sliceWidget("Yellow")
        if ys:
            ys.sliceLogic().GetSliceNode().SetSliceVisible(False)

        self.currentPreviewPos = None
        self.currentPreviewId = -1

        # Re-paint zone colours
        self.updateCenterlineZoneColors()

        if self.updateCallback:
            self.updateCallback()

        return lm["label"]

    def _findExistingLandmark(self, zoneIndex):
        """Return the control-point index for an already-placed landmark, or -1."""
        if not self.logic.zoneNode:
            return -1
        lm = SVS_STS_LANDMARKS[zoneIndex]
        n = self.logic.zoneNode.GetNumberOfControlPoints()
        for i in range(n):
            if self.logic.zoneNode.GetNthControlPointLabel(i) == lm["label"]:
                return i
        return -1

    # ------------------------------------------------------------------
    #  Centerline zone colouring
    # ------------------------------------------------------------------

    def updateCenterlineZoneColors(self):
        """Paint a coloured tube overlay on the centerline between placed landmarks.

        Uses direct RGBA unsigned-char scalars so no custom colour node is needed.
        """
        if not self.centerlineNode or not self.centerlineNode.GetPolyData():
            self._removeZoneColorNode()
            return

        polydata = self.centerlineNode.GetPolyData()
        nPoints = polydata.GetNumberOfPoints()

        # Gather placed landmarks with valid point IDs
        placed = [
            (pid, zIdx)
            for zIdx, pid in self.landmarkPointIds.items()
            if pid >= 0
        ]
        if len(placed) < 2:
            self._removeZoneColorNode()
            return

        placed.sort(key=lambda x: x[0])  # sort by centreline point order

        # Build RGBA array
        colors = vtk.vtkUnsignedCharArray()
        colors.SetName("ZoneRGB")
        colors.SetNumberOfComponents(4)
        colors.SetNumberOfTuples(nPoints)

        # Default: light gray, low opacity
        for i in range(nPoints):
            colors.SetTuple4(i, 140, 140, 140, 30)

        # Colour segments between consecutive landmarks
        for k in range(len(placed) - 1):
            startPid, zoneIdx = placed[k]
            endPid, _ = placed[k + 1]
            lm = SVS_STS_LANDMARKS[zoneIdx]
            c = lm["color"]
            r, g, b = int(c[0] * 255), int(c[1] * 255), int(c[2] * 255)
            for pid in range(startPid, endPid + 1):
                if 0 <= pid < nPoints:
                    colors.SetTuple4(pid, r, g, b, 220)

        # Build tube overlay
        coloredPD = vtk.vtkPolyData()
        coloredPD.DeepCopy(polydata)
        coloredPD.GetPointData().SetScalars(colors)

        tubeFilter = vtk.vtkTubeFilter()
        tubeFilter.SetInputData(coloredPD)
        tubeFilter.SetRadius(1.2)
        tubeFilter.SetNumberOfSides(12)
        tubeFilter.CappingOn()
        tubeFilter.Update()

        if not self.zoneColorNode:
            self.zoneColorNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLModelNode", "Centerline_Zone_Colors"
            )
            self.zoneColorNode.CreateDefaultDisplayNodes()

        self.zoneColorNode.SetAndObservePolyData(tubeFilter.GetOutput())

        display = self.zoneColorNode.GetDisplayNode()
        if display:
            display.SetActiveScalarName("ZoneRGB")
            display.SetScalarVisibility(True)
            display.SetAndObserveColorNodeID("")
            display.SetBackfaceCulling(False)
            # Direct-mapping for unsigned-char RGBA
            try:
                display.SetScalarRangeFlag(
                    slicer.vtkMRMLDisplayNode.UseDirectMapping
                )
            except AttributeError:
                # Fallback for older Slicer builds
                display.SetScalarRangeFlag(4)

    def _removeZoneColorNode(self):
        if self.zoneColorNode:
            try:
                slicer.mrmlScene.RemoveNode(self.zoneColorNode)
            except Exception:
                pass
            self.zoneColorNode = None