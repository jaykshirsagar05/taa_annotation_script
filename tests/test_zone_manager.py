"""
Tests for ZoneManager CRUD and navigation.

Uses a minimal logic stub and a MagicMock fiducial node so no Slicer
process is required.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, call

# ── Slicer / Qt stubs ────────────────────────────────────────────────────────
for _mod in ['slicer', 'slicer.util', 'qt', 'vtk']:
    sys.modules.setdefault(_mod, MagicMock())

_HERE = os.path.dirname(__file__)
_TAAA_DIR = os.path.join(_HERE, '..', 'TAAAnnotation')
if _TAAA_DIR not in sys.path:
    sys.path.insert(0, _TAAA_DIR)

import slicer  # noqa: E402  (the mock above)
from TAAAnnotationLib.ZoneManager import ZoneManager  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

class _Logic:
    """Minimal logic stub that ZoneManager reads from / writes to."""

    def __init__(self):
        self.zoneNode = None
        self.currentId = "TEST001"
        self.workflowState = {"zoneCount": 0}
        self.hasUnsavedWork = False


def _make_fiducial(n_points=0):
    """Return a mock fiducial node pre-configured with n_points control points."""
    node = MagicMock()
    node.GetNumberOfControlPoints.return_value = n_points
    node.GetDisplayNode.return_value = MagicMock()
    return node


# ─────────────────────────────────────────────────────────────────────────────
# createZoneNode
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateZoneNode(unittest.TestCase):

    def setUp(self):
        self.logic = _Logic()
        self.manager = ZoneManager(self.logic)
        self.mock_node = _make_fiducial()
        slicer.mrmlScene.AddNewNodeByClass.reset_mock()
        slicer.mrmlScene.AddNewNodeByClass.return_value = self.mock_node

    def test_creates_node_when_none_exists(self):
        node = self.manager.createZoneNode()
        slicer.mrmlScene.AddNewNodeByClass.assert_called_once_with(
            "vtkMRMLMarkupsFiducialNode", "TEST001_Zones"
        )
        self.assertIs(node, self.mock_node)
        self.assertIs(self.logic.zoneNode, self.mock_node)

    def test_returns_existing_node_without_recreating(self):
        existing = _make_fiducial()
        self.logic.zoneNode = existing
        node = self.manager.createZoneNode()
        slicer.mrmlScene.AddNewNodeByClass.assert_not_called()
        self.assertIs(node, existing)


# ─────────────────────────────────────────────────────────────────────────────
# addZonePoint
# ─────────────────────────────────────────────────────────────────────────────

class TestAddZonePoint(unittest.TestCase):

    def setUp(self):
        self.logic = _Logic()
        self.manager = ZoneManager(self.logic)

    def _attach_node(self, n_points=0):
        node = _make_fiducial(n_points)
        self.logic.zoneNode = node
        slicer.mrmlScene.AddNewNodeByClass.return_value = node
        return node

    def test_returns_default_label(self):
        self._attach_node(0)
        label = self.manager.addZonePoint([1.0, 2.0, 3.0])
        self.assertEqual(label, "Zone_1")

    def test_returns_custom_label(self):
        self._attach_node(0)
        label = self.manager.addZonePoint([0, 0, 0], zoneName="SVS")
        self.assertEqual(label, "SVS")

    def test_calls_add_control_point_with_correct_coords(self):
        node = self._attach_node(0)
        self.manager.addZonePoint([10.5, -3.0, 7.2])
        node.AddControlPoint.assert_called_once_with(10.5, -3.0, 7.2)

    def test_label_is_set_on_node(self):
        node = self._attach_node(3)
        self.manager.addZonePoint([0, 0, 0])
        node.SetNthControlPointLabel.assert_called_once_with(3, "Zone_4")

    def test_custom_name_set_on_node(self):
        node = self._attach_node(0)
        self.manager.addZonePoint([0, 0, 0], zoneName="STS")
        node.SetNthControlPointLabel.assert_called_once_with(0, "STS")

    def test_increments_workflow_zone_count(self):
        self._attach_node(2)
        self.manager.addZonePoint([0, 0, 0])
        self.assertEqual(self.logic.workflowState["zoneCount"], 3)

    def test_marks_unsaved_work(self):
        self._attach_node(0)
        self.logic.hasUnsavedWork = False
        self.manager.addZonePoint([0, 0, 0])
        self.assertTrue(self.logic.hasUnsavedWork)

    def test_returns_none_at_max_capacity(self):
        self._attach_node(10)
        result = self.manager.addZonePoint([0, 0, 0])
        self.assertIsNone(result)

    def test_no_add_when_at_max_capacity(self):
        node = self._attach_node(10)
        self.manager.addZonePoint([0, 0, 0])
        node.AddControlPoint.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# undoLastZonePoint
# ─────────────────────────────────────────────────────────────────────────────

class TestUndoLastZonePoint(unittest.TestCase):

    def setUp(self):
        self.logic = _Logic()
        self.manager = ZoneManager(self.logic)

    def test_removes_last_point(self):
        node = _make_fiducial(3)
        self.logic.zoneNode = node
        self.manager.undoLastZonePoint()
        node.RemoveNthControlPoint.assert_called_once_with(2)

    def test_decrements_zone_count(self):
        node = _make_fiducial(3)
        self.logic.zoneNode = node
        self.logic.workflowState["zoneCount"] = 3
        self.manager.undoLastZonePoint()
        self.assertEqual(self.logic.workflowState["zoneCount"], 2)

    def test_no_op_when_empty(self):
        node = _make_fiducial(0)
        self.logic.zoneNode = node
        self.manager.undoLastZonePoint()
        node.RemoveNthControlPoint.assert_not_called()

    def test_no_op_when_zone_node_is_none(self):
        self.logic.zoneNode = None
        # Should not raise
        self.manager.undoLastZonePoint()


# ─────────────────────────────────────────────────────────────────────────────
# deleteZonePoint
# ─────────────────────────────────────────────────────────────────────────────

class TestDeleteZonePoint(unittest.TestCase):

    def setUp(self):
        self.logic = _Logic()
        self.manager = ZoneManager(self.logic)

    def test_removes_correct_index(self):
        node = _make_fiducial(5)
        self.logic.zoneNode = node
        self.manager.deleteZonePoint(2)
        node.RemoveNthControlPoint.assert_called_once_with(2)

    def test_no_op_for_negative_index(self):
        node = _make_fiducial(5)
        self.logic.zoneNode = node
        self.manager.deleteZonePoint(-1)
        node.RemoveNthControlPoint.assert_not_called()

    def test_no_op_for_index_equal_to_count(self):
        node = _make_fiducial(3)
        self.logic.zoneNode = node
        self.manager.deleteZonePoint(3)
        node.RemoveNthControlPoint.assert_not_called()

    def test_no_op_when_node_is_none(self):
        self.logic.zoneNode = None
        self.manager.deleteZonePoint(0)


# ─────────────────────────────────────────────────────────────────────────────
# renameZonePoint
# ─────────────────────────────────────────────────────────────────────────────

class TestRenameZonePoint(unittest.TestCase):

    def setUp(self):
        self.logic = _Logic()
        self.manager = ZoneManager(self.logic)

    def test_sets_label(self):
        node = _make_fiducial(3)
        self.logic.zoneNode = node
        self.manager.renameZonePoint(1, "SVS_Renamed")
        node.SetNthControlPointLabel.assert_called_once_with(1, "SVS_Renamed")

    def test_no_op_for_out_of_bounds(self):
        node = _make_fiducial(2)
        self.logic.zoneNode = node
        self.manager.renameZonePoint(5, "X")
        node.SetNthControlPointLabel.assert_not_called()

    def test_no_op_when_node_is_none(self):
        self.logic.zoneNode = None
        self.manager.renameZonePoint(0, "X")


if __name__ == '__main__':
    unittest.main()
