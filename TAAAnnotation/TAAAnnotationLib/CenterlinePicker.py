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
#  Scroll-navigation tuning
# ---------------------------------------------------------------------------
# Scrolling advances a fixed *arc length* along the centerline rather than a
# fixed number of centerline points.
SCROLL_STEP_MM = 1.0            # per standard wheel notch

# Wheel events are accumulated for this long before one reformat is applied,
# which bounds the reformat rate to roughly one per display frame.
SCROLL_COALESCE_MS = 16

# Non-essential preview work (3D disk, Red/Green re-centre, labels, zone table)
# runs this long after the wheel goes quiet, so it never adds scroll latency.
SCROLL_SETTLE_MS = 120


# ---------------------------------------------------------------------------
#  Qt wheel-event filter used during centerline scroll navigation
# ---------------------------------------------------------------------------

class _YellowWheelFilter(qt.QObject):
    """Installed on the Yellow slice-view widget when a zone preview is active.

    Intercepts Qt-level QWheelEvent before it reaches VTK so the scroll wheel
    steps along the centerline instead of moving the slice stack.
    Returning True from eventFilter() fully consumes the event.

    The event is only *queued* here.  CenterlinePicker coalesces the accumulated
    delta and applies a single reformat, so a fast flick costs one update rather
    than one full update per notch.
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
            self._picker._queueScroll(delta)
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

        # Monotonic arc length (mm) along _centerlinePath — the scroll axis.
        self._pathArcLengths = None        # np.ndarray, len == len(_centerlinePath)

        # Wheel coalescing: events accumulate into _pendingScrollMm and a single
        # reformat is applied per timer tick.
        self._pendingScrollMm = 0.0
        self._isScrolling = False          # re-entrancy guard for the fast path
        self._scrollTimer = qt.QTimer()
        self._scrollTimer.setInterval(SCROLL_COALESCE_MS)
        self._scrollTimer.setSingleShot(True)
        self._scrollTimer.timeout.connect(self._applyPendingScroll)

        # Deferred heavy preview work, run once the wheel stops.
        self._settleTimer = qt.QTimer()
        self._settleTimer.setInterval(SCROLL_SETTLE_MS)
        self._settleTimer.setSingleShot(True)
        self._settleTimer.timeout.connect(self._onScrollSettled)

        # Reusable cross-section disk pipeline.
        self._diskTransform = None
        self._diskFilter = None
        self._diskColor = None

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
        self._pathArcLengths = None
        self._currentPathIndex = -1
        self._pendingScrollMm = 0.0

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

        # Hide Yellow slice plane in 3D
        ys = slicer.app.layoutManager().sliceWidget("Yellow")
        if ys:
            ys.sliceLogic().GetSliceNode().SetSliceVisible(False)

        self._removePointPicking()

        # Remove preview nodes
        if self.previewNode:
            slicer.mrmlScene.RemoveNode(self.previewNode)
            self.previewNode = None
        if self.previewPlaneNode:
            slicer.mrmlScene.RemoveNode(self.previewPlaneNode)
            self.previewPlaneNode = None

        # Release the reusable disk pipeline along with its model node.
        self._diskTransform = None
        self._diskFilter = None
        self._diskColor = None

        self.isActive = False
        self.centerlineNode = None
        self._cumulativeDistances = None
        self._centerlinePath = None
        self._pathArcLengths = None
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
        lm = self._currentLandmarkDef()
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

    def _ensurePathArcLengths(self):
        """Build the monotonic arc-length table along ``_centerlinePath``.

        ``_cumulativeDistances`` is indexed by point ID and, on the VMTK
        "Length" fast path, is not guaranteed to increase monotonically across
        branch boundaries.  Scroll navigation needs a strictly increasing axis
        it can binary-search, so measure arc length directly along the ordered
        main-trunk path.  Built once per centerline.
        """
        if self._pathArcLengths is not None:
            return
        if not self._centerlinePath or not self.centerlineNode:
            return
        pd = self.centerlineNode.GetPolyData()
        if pd is None or pd.GetNumberOfPoints() == 0:
            return

        from vtk.util.numpy_support import vtk_to_numpy

        allPts = vtk_to_numpy(pd.GetPoints().GetData())
        pathPts = allPts[np.asarray(self._centerlinePath, dtype=np.int64)]

        arc = np.zeros(len(self._centerlinePath), dtype=np.float64)
        if len(arc) > 1:
            segLens = np.linalg.norm(np.diff(pathPts, axis=0), axis=1)
            np.cumsum(segLens, out=arc[1:])
        self._pathArcLengths = arc

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

    # ------------------------------------------------------------------
    #  Preview state (after click)
    # ------------------------------------------------------------------

    def _currentLandmarkDef(self):
        """Landmark definition for the zone being placed, or None."""
        if 0 <= self.selectedZoneIndex < len(SVS_STS_LANDMARKS):
            return SVS_STS_LANDMARKS[self.selectedZoneIndex]
        return None

    def _previewLabel(self, pointId, lm):
        """Preview marker label, with arc-length offset from the previous landmark."""
        label = lm["label"] if lm else "Preview"
        dist, prevLabel = self._getDistanceFromPreviousLandmark(pointId)
        if dist is not None:
            label = f"{label} (+{dist:.1f} mm from {prevLabel})"
        return f"\u25b6 {label}"

    def _sliceFrameAtPoint(self, pointId):
        """Return (normal, inPlaneAxis) of the cross-section frame at *pointId*."""
        tangent = self._getTangentAtPoint(self.centerlineNode.GetPolyData(), pointId)
        n = np.array(tangent, dtype=float)
        norm = np.linalg.norm(n)
        n = np.array([0.0, 0.0, 1.0]) if norm < 1e-9 else n / norm

        a = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
        t1 = np.cross(n, a)
        t1 /= np.linalg.norm(t1)
        return n, t1

    def _reformatYellow(self, pos, pointId):
        """Orient the Yellow slice as the centerline cross-section at *pointId*.

        This is the only operation the scroll fast path performs, so it stays
        free of node creation, extra renders and secondary view updates.
        """
        n, t1 = self._sliceFrameAtPoint(pointId)
        yellowSlice = slicer.app.layoutManager().sliceWidget("Yellow")
        if yellowSlice:
            yellowSlice.sliceLogic().GetSliceNode().SetSliceToRASByNTP(
                n[0], n[1], n[2],
                t1[0], t1[1], t1[2],
                pos[0], pos[1], pos[2], 0,
            )
        return n, t1

    def _updatePreviewState(self, pos, pointId):
        """Lock the preview location and show cross-section disk + slice views.

        After this call the preview fiducial is *unlocked* so the user can
        drag it in axial / coronal / sagittal slice views.  A snap-to-
        centerline timer re-projects the point on release.
        """
        self.currentPreviewPos = pos
        self.currentPreviewId = pointId

        # Pause hover while locked, and drop any queued wheel delta since the
        # preview state is being rebuilt from scratch.
        self._hoverTimer.stop()
        self._scrollTimer.stop()
        self._settleTimer.stop()
        self._pendingScrollMm = 0.0

        lm = self._currentLandmarkDef()

        # Arc-length tables (also populate _centerlinePath / _pathArcLengths).
        self._ensureCumulativeDistances()
        self._ensurePathArcLengths()

        # Stop observing before modifying the preview node
        self._stopPreviewObservation()
        self._isSnapping = True

        # Update preview fiducial
        if self.previewNode:
            self.previewNode.RemoveAllControlPoints()
            self.previewNode.AddControlPoint(pos[0], pos[1], pos[2])
            self.previewNode.SetNthControlPointLabel(
                0, self._previewLabel(pointId, lm)
            )
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

        # Orient Yellow as the cross-section, then the secondary views.
        n, t1 = self._reformatYellow(pos, pointId)
        self._centerSliceViewsOnPoint(pos)
        self._createOrUpdatePreviewPlane(pos, n, t1, lm)

        # Start observing the preview node for user adjustments in slices
        self._startPreviewObservation()

        # Sync scroll-navigation index and enable Yellow-slice scroll.
        self._updatePathIndex(pointId)
        self._startYellowScrollObservation()

    def _createOrUpdatePreviewPlane(self, center, normal, xAxis, lm=None):
        """Place the 3D cross-section disk at *center* with the given frame."""
        yAxis = np.cross(normal, xAxis)
        yAxis /= np.linalg.norm(yAxis)

        matrix = vtk.vtkMatrix4x4()
        for i in range(3):
            matrix.SetElement(i, 0, xAxis[i])
            matrix.SetElement(i, 1, yAxis[i])
            matrix.SetElement(i, 2, normal[i])
            matrix.SetElement(i, 3, center[i])

        if self._diskFilter is None:
            disk = vtk.vtkDiskSource()
            disk.SetInnerRadius(0)
            disk.SetOuterRadius(30)
            disk.SetRadialResolution(30)
            disk.SetCircumferentialResolution(30)

            self._diskTransform = vtk.vtkTransform()
            self._diskFilter = vtk.vtkTransformPolyDataFilter()
            self._diskFilter.SetInputConnection(disk.GetOutputPort())
            self._diskFilter.SetTransform(self._diskTransform)

        self._diskTransform.SetMatrix(matrix)
        self._diskFilter.Update()

        if self.previewPlaneNode is None:
            self.previewPlaneNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLModelNode", "Preview_Plane_Temp"
            )
            self.previewPlaneNode.SetAndObservePolyData(self._diskFilter.GetOutput())
            self.previewPlaneNode.CreateDefaultDisplayNodes()
            self._diskColor = None

            display = self.previewPlaneNode.GetDisplayNode()
            if display:
                display.SetOpacity(0.45)
                display.SetBackfaceCulling(False)
                display.SetVisibility(True)
        else:
            # The filter reuses its output object, so the node already observes
            # the polydata whose points were just recomputed — only the change
            # notification is needed.
            pd = self.previewPlaneNode.GetPolyData()
            if pd is not None:
                pd.Modified()

        # Colour only changes when the selected zone does.
        color = tuple(lm["color"]) if lm else (1.0, 0.0, 0.0)
        if color != self._diskColor:
            display = self.previewPlaneNode.GetDisplayNode()
            if display:
                display.SetColor(*color)
            self._diskColor = color

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

            from vtk.util.numpy_support import vtk_to_numpy

            allPts = vtk_to_numpy(pd.GetPoints().GetData())
            pathPts = allPts[np.asarray(self._centerlinePath, dtype=np.int64)]
            d = np.linalg.norm(pathPts - allPts[pointId], axis=1)
            self._currentPathIndex = int(np.argmin(d))

    # ------------------------------------------------------------------
    #  Yellow-slice scroll — walk along the centerline after a click
    # ------------------------------------------------------------------

    def _startYellowScrollObservation(self):
        """Install a Qt wheel event filter on the Yellow slice view so that
        scrolling steps along the centerline instead of the slice stack.

        Using a Qt-level filter (rather than a VTK observer) guarantees the
        event is consumed before VTK ever sees it.
        """
        ys = slicer.app.layoutManager().sliceWidget("Yellow")
        if not ys:
            return
        if self._wheelFilter is None:
            self._wheelFilter = _YellowWheelFilter(self)
        ys.sliceView().installEventFilter(self._wheelFilter)

    def _stopYellowScrollObservation(self):
        """Remove the Yellow-slice wheel filter and drop queued scroll state."""
        self._scrollTimer.stop()
        self._settleTimer.stop()
        self._pendingScrollMm = 0.0
        if self._wheelFilter is not None:
            ys = slicer.app.layoutManager().sliceWidget("Yellow")
            if ys:
                try:
                    ys.sliceView().removeEventFilter(self._wheelFilter)
                except Exception:
                    pass
            self._wheelFilter = None

    def _queueScroll(self, angleDelta):
        """Accumulate a wheel delta and schedule one coalesced reformat."""
        if not angleDelta:
            return

        # 120 angle-delta units is one standard wheel notch.
        self._pendingScrollMm += (angleDelta / 120.0) * SCROLL_STEP_MM
        if not self._scrollTimer.isActive():
            self._scrollTimer.start()

    def _pathIndexAtOffset(self, fromIndex, offsetMm):
        """Path index reached by travelling *offsetMm* along the centerline."""
        arc = self._pathArcLengths
        target = arc[fromIndex] + offsetMm
        idx = int(np.searchsorted(arc, target))
        idx = max(0, min(len(arc) - 1, idx))
        if idx > 0 and abs(arc[idx - 1] - target) < abs(arc[idx] - target):
            idx -= 1
        return idx

    def _applyPendingScroll(self):
        """Apply the accumulated wheel delta — the scroll fast path."""
        if self._isScrolling:
            return                      # re-entrancy guard
        if self._pendingScrollMm == 0.0:
            return
        if (
            self.currentPreviewPos is None
            or not self._centerlinePath
            or self._currentPathIndex < 0
            or self._pathArcLengths is None
        ):
            self._pendingScrollMm = 0.0
            return

        newIdx = self._pathIndexAtOffset(self._currentPathIndex, self._pendingScrollMm)
        if newIdx == self._currentPathIndex:
            # Sub-point movement.  Keep the residual so that many tiny trackpad
            # increments accumulate into a step instead of being discarded.
            return
        self._pendingScrollMm = 0.0

        newPointId = self._centerlinePath[newIdx]
        exactPos = self.centerlineNode.GetPolyData().GetPoint(newPointId)

        self._isScrolling = True
        try:
            self.currentPreviewPos = exactPos
            self.currentPreviewId = newPointId
            self._currentPathIndex = newIdx

            # Move the existing control point rather than remove/re-add it.
            self._isSnapping = True         # suppress the snap-back handler
            try:
                self._snapTimer.stop()
                if (
                    self.previewNode
                    and self.previewNode.GetNumberOfControlPoints() > 0
                ):
                    self.previewNode.SetNthControlPointPosition(
                        0, exactPos[0], exactPos[1], exactPos[2]
                    )
            finally:
                self._isSnapping = False

            self._reformatYellow(exactPos, newPointId)
        finally:
            self._isScrolling = False

        # Restart the settle countdown — heavy work runs when the wheel stops.
        self._settleTimer.start()

    def _onScrollSettled(self):
        """Deferred preview work, run once the wheel has gone quiet."""
        if self.currentPreviewPos is None or self.currentPreviewId < 0:
            return
        if not self.centerlineNode:
            return

        pos = self.currentPreviewPos
        pointId = self.currentPreviewId
        lm = self._currentLandmarkDef()

        # Arc-length distance label from the previous placed landmark.
        if self.previewNode and self.previewNode.GetNumberOfControlPoints() > 0:
            self._isSnapping = True
            try:
                self.previewNode.SetNthControlPointLabel(
                    0, self._previewLabel(pointId, lm)
                )
            finally:
                self._isSnapping = False

        # Secondary views and the 3D cross-section disk.
        self._centerSliceViewsOnPoint(pos)
        n, t1 = self._sliceFrameAtPoint(pointId)
        self._createOrUpdatePreviewPlane(pos, n, t1, lm)

        if self.updateCallback:
            self.updateCallback()

    def _scrollCenterline(self, delta):
        """Advance or retreat *delta* points along the centerline path.

        Retained as a programmatic entry point (the wheel filter now goes
        through _queueScroll()).  Converts a point-count step into the
        equivalent arc-length offset and applies it immediately.
        """
        if (
            not self._centerlinePath
            or self._currentPathIndex < 0
            or self._pathArcLengths is None
            or self.currentPreviewPos is None
        ):
            return

        arc = self._pathArcLengths
        targetIdx = max(0, min(len(arc) - 1, self._currentPathIndex + delta))
        self._pendingScrollMm = float(arc[targetIdx] - arc[self._currentPathIndex])
        self._applyPendingScroll()

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

            lm = self._currentLandmarkDef()

            # Re-orient Yellow, re-centre Axial / Coronal, redraw the disk.
            n, t1 = self._reformatYellow(exactPos, nearestPointId)
            self._centerSliceViewsOnPoint(exactPos)
            self._createOrUpdatePreviewPlane(exactPos, n, t1, lm)
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

        # Hide Yellow slice plane
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