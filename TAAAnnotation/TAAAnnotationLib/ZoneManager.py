"""
ZoneManager.py — Fiducial zone landmark CRUD and navigation.

Extracted from TAAAnnotationLogic to isolate the zone-point operations
that are shared between CenterlinePicker and the workflow UI.
"""

import slicer


class ZoneManager:
    """Manages the zone fiducial node: creation, CRUD, and slice-view navigation."""

    def __init__(self, logic):
        self._logic = logic

    def createZoneNode(self):
        """Create zone fiducial node if it does not already exist."""
        if not self._logic.zoneNode:
            self._logic.zoneNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsFiducialNode",
                f"{self._logic.currentId}_Zones"
            )
            display = self._logic.zoneNode.GetDisplayNode()
            if display:
                display.SetSelectedColor(1, 0.5, 0)
                display.SetGlyphScale(3.0)
                display.SetTextScale(4.0)
        return self._logic.zoneNode

    def addZonePoint(self, worldPos, zoneName=None):
        """Add a zone point.

        Args:
            worldPos:  3D position (x, y, z).
            zoneName:  Optional label; defaults to "Zone_{n+1}".

        Returns:
            Label string, or None if the 10-point maximum is already reached.
        """
        zoneNode = self.createZoneNode()
        n = zoneNode.GetNumberOfControlPoints()

        if n >= 10:
            return None

        zoneNode.AddControlPoint(worldPos[0], worldPos[1], worldPos[2])

        label = zoneName if zoneName else f"Zone_{n + 1}"
        zoneNode.SetNthControlPointLabel(n, label)

        self._logic.workflowState["zoneCount"] = n + 1
        self._logic.hasUnsavedWork = True
        return label

    def undoLastZonePoint(self):
        """Remove the most-recently added zone point."""
        if self._logic.zoneNode:
            n = self._logic.zoneNode.GetNumberOfControlPoints()
            if n > 0:
                self._logic.zoneNode.RemoveNthControlPoint(n - 1)
                self._logic.workflowState["zoneCount"] = n - 1

    def deleteZonePoint(self, index):
        """Delete zone point at the given index."""
        if (self._logic.zoneNode
                and 0 <= index < self._logic.zoneNode.GetNumberOfControlPoints()):
            self._logic.zoneNode.RemoveNthControlPoint(index)

    def renameZonePoint(self, index, newName):
        """Rename zone point at the given index."""
        if (self._logic.zoneNode
                and 0 <= index < self._logic.zoneNode.GetNumberOfControlPoints()):
            self._logic.zoneNode.SetNthControlPointLabel(index, newName)

    def jumpToZonePoint(self, index):
        """Centre all slice views and the 3D camera on the given zone point."""
        if (self._logic.zoneNode
                and 0 <= index < self._logic.zoneNode.GetNumberOfControlPoints()):
            pos = [0, 0, 0]
            self._logic.zoneNode.GetNthControlPointPosition(index, pos)

            # Map slice-colour to the axis used as the scroll offset
            offsets = {'Red': pos[2], 'Yellow': pos[0], 'Green': pos[1]}
            for color, offset in offsets.items():
                sliceWidget = slicer.app.layoutManager().sliceWidget(color)
                if sliceWidget:
                    sliceWidget.sliceLogic().SetSliceOffset(offset)

            threeDWidget = slicer.app.layoutManager().threeDWidget(0)
            if threeDWidget:
                threeDWidget.threeDView().setFocalPoint(pos[0], pos[1], pos[2])
