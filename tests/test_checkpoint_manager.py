"""
Tests for CheckpointManager — the file-system side of save/resume.

Node I/O goes through the stubbed ``slicer.util.saveNode``, so these exercise
the checkpoint layout, state.json contents, and the guard conditions.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

import slicer  # noqa: F401  (stubbed in conftest)
from TAAAnnotationLib import LocalDataset
from TAAAnnotationLib.CheckpointManager import CheckpointManager


class _Logic:
    """Minimal logic stand-in exposing the attributes CheckpointManager reads."""

    def __init__(self, caseDir):
        self.caseDir = caseDir
        self.currentId = os.path.basename(caseDir)
        self.volNode = MagicMock()
        self.segNode = MagicMock()
        self.centerlineNode = None
        self.networkNode = None
        self.endpointNode = None
        self.zoneNode = None
        self.hasUnsavedWork = True
        self.workflowState = {"phase": 2, "lastSave": None, "zoneCount": 0}


def _fiducial(n_points):
    node = MagicMock()
    node.GetNumberOfControlPoints.return_value = n_points
    return node


class _CheckpointTestCase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.caseDir = os.path.join(self.root, "scan_001")
        os.makedirs(self.caseDir)
        self.logic = _Logic(self.caseDir)
        self.manager = CheckpointManager(self.logic)
        slicer.util.saveNode = MagicMock(return_value=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _readState(self):
        with open(LocalDataset.checkpoint_state_path(self.caseDir)) as f:
            return json.load(f)


class TestSave(_CheckpointTestCase):

    def test_writes_state_into_case_folder(self):
        success, _ = self.manager.save("some notes")
        self.assertTrue(success)
        self.assertTrue(os.path.isdir(LocalDataset.checkpoint_dir(self.caseDir)))
        self.assertTrue(os.path.exists(
            LocalDataset.checkpoint_state_path(self.caseDir)))

    def test_state_records_phase_notes_and_case_id(self):
        self.manager.save("scan has motion artifact")
        state = self._readState()
        self.assertEqual(state["case_id"], "scan_001")
        self.assertEqual(state["phase"], 2)
        self.assertEqual(state["notes"], "scan has motion artifact")
        self.assertEqual(state["version"], 1)

    def test_saves_only_the_nodes_that_exist(self):
        self.manager.save()
        self.assertEqual(set(self._readState()["files"]), {"refined_mask"})

    def test_saves_every_populated_node(self):
        self.logic.centerlineNode = MagicMock()
        self.logic.networkNode = MagicMock()
        self.logic.endpointNode = MagicMock()
        self.logic.zoneNode = _fiducial(4)
        self.manager.save()
        self.assertEqual(
            set(self._readState()["files"]),
            {"refined_mask", "centerline", "network", "endpoints", "zones"},
        )

    def test_empty_zone_node_is_not_saved(self):
        self.logic.zoneNode = _fiducial(0)
        self.manager.save()
        state = self._readState()
        self.assertNotIn("zones", state["files"])
        self.assertEqual(state["zone_count"], 0)

    def test_zone_count_reflects_placed_landmarks(self):
        self.logic.zoneNode = _fiducial(7)
        self.manager.save()
        self.assertEqual(self._readState()["zone_count"], 7)

    def test_successful_save_clears_unsaved_flag(self):
        self.manager.save()
        self.assertFalse(self.logic.hasUnsavedWork)

    def test_refuses_without_a_case(self):
        self.logic.caseDir = ""
        success, message = self.manager.save()
        self.assertFalse(success)
        self.assertIn("No case loaded", message)

    def test_refuses_without_a_segmentation(self):
        self.logic.segNode = None
        success, message = self.manager.save()
        self.assertFalse(success)
        self.assertIn("No segmentation", message)

    def test_reports_nodes_that_failed_to_save(self):
        slicer.util.saveNode = MagicMock(return_value=False)
        success, message = self.manager.save()
        self.assertTrue(success)
        self.assertIn("refined_mask", message)


class TestDescribeAndClear(_CheckpointTestCase):

    def test_describe_is_empty_without_a_checkpoint(self):
        self.assertEqual(CheckpointManager.describe(self.caseDir), "")

    def test_describe_summarises_phase_and_landmarks(self):
        self.logic.zoneNode = _fiducial(4)
        self.manager.save()
        summary = CheckpointManager.describe(self.caseDir)
        self.assertIn("phase 2", summary)
        self.assertIn("4/10", summary)

    def test_clear_removes_the_checkpoint_directory(self):
        self.manager.save()
        self.assertTrue(CheckpointManager.clear(self.caseDir))
        self.assertFalse(os.path.exists(LocalDataset.checkpoint_dir(self.caseDir)))

    def test_clear_succeeds_when_nothing_to_remove(self):
        self.assertTrue(CheckpointManager.clear(self.caseDir))

    def test_cleared_case_reads_as_not_started(self):
        self.manager.save()
        CheckpointManager.clear(self.caseDir)
        self.assertEqual(LocalDataset.case_status(self.caseDir),
                         (LocalDataset.STATUS_NOT_STARTED, 0))


class TestRestore(_CheckpointTestCase):

    def test_missing_checkpoint_reports_an_error(self):
        state, errors = self.manager.restore(self.caseDir)
        self.assertIsNone(state)
        self.assertTrue(errors)

    def test_restores_phase_from_state(self):
        self.logic.workflowState["phase"] = 3
        self.manager.save()
        self.logic.workflowState["phase"] = 0

        slicer.util.loadSegmentation = MagicMock(return_value=MagicMock())
        state, errors = self.manager.restore(self.caseDir)

        self.assertEqual(state["phase"], 3)
        self.assertEqual(self.logic.workflowState["phase"], 3)
        self.assertEqual(errors, [])

    def test_restore_clears_unsaved_flag(self):
        self.manager.save()
        self.logic.hasUnsavedWork = True
        slicer.util.loadSegmentation = MagicMock(return_value=MagicMock())
        self.manager.restore(self.caseDir)
        self.assertFalse(self.logic.hasUnsavedWork)

    def test_reports_a_node_that_will_not_load(self):
        self.manager.save()
        slicer.util.loadSegmentation = MagicMock(return_value=None)
        _, errors = self.manager.restore(self.caseDir)
        self.assertTrue(any("Refined mask" in e for e in errors))


class TestRestoreExport(_CheckpointTestCase):
    """Reopening a finished case must reload its exported outputs."""

    def _writeExports(self, kinds=LocalDataset.REQUIRED_OUTPUTS, **metadata):
        import json as _json
        for kind in kinds:
            path = LocalDataset.output_path(self.caseDir, kind)
            if kind == "metadata":
                payload = {"case_id": "scan_001", "exported_at": "2026-09-08T10:00:00",
                           "zone_count": 10, "notes": "looks good"}
                payload.update(metadata)
                with open(path, 'w') as f:
                    _json.dump(payload, f)
            else:
                with open(path, 'w') as f:
                    f.write("x")

    def setUp(self):
        super().setUp()
        slicer.util.loadSegmentation = MagicMock(return_value=MagicMock())
        slicer.util.loadModel = MagicMock(return_value=MagicMock())
        slicer.util.loadMarkups = MagicMock(return_value=MagicMock())

    def test_describe_export_is_empty_for_an_unfinished_case(self):
        self.assertEqual(CheckpointManager.describeExport(self.caseDir), "")

    def test_describe_export_summarises_the_finished_annotation(self):
        self._writeExports()
        summary = CheckpointManager.describeExport(self.caseDir)
        self.assertIn("2026-09-08T10:00:00", summary)
        self.assertIn("10/10", summary)

    def test_reopens_at_the_zone_landmark_phase(self):
        self._writeExports()
        self.logic.workflowState["phase"] = 0
        metadata, errors = self.manager.restoreExport(self.caseDir)
        self.assertEqual(self.logic.workflowState["phase"], 3)
        self.assertEqual(errors, [])
        self.assertEqual(metadata["notes"], "looks good")

    def test_loads_the_refined_mask_over_the_input_mask(self):
        self._writeExports()
        originalSeg = self.logic.segNode
        self.manager.restoreExport(self.caseDir)
        self.assertIsNot(self.logic.segNode, originalSeg)
        slicer.util.loadSegmentation.assert_called_once()

    def test_loads_centerline_and_zones(self):
        self._writeExports(("refined_mask", "centerline", "zones", "metadata"))
        self.manager.restoreExport(self.caseDir)
        self.assertIsNotNone(self.logic.centerlineNode)
        self.assertIsNotNone(self.logic.zoneNode)

    def test_absent_outputs_are_skipped_without_error(self):
        self._writeExports(("refined_mask", "metadata"))
        _, errors = self.manager.restoreExport(self.caseDir)
        self.assertEqual(errors, [])
        self.assertIsNone(self.logic.centerlineNode)

    def test_unreadable_metadata_still_loads_the_nodes(self):
        self._writeExports(("refined_mask", "centerline", "zones"))
        with open(LocalDataset.output_path(self.caseDir, "metadata"), 'w') as f:
            f.write("{ not json")
        metadata, errors = self.manager.restoreExport(self.caseDir)
        self.assertIsNone(metadata)
        self.assertEqual(errors, [])
        self.assertIsNotNone(self.logic.centerlineNode)

    def test_reopen_clears_unsaved_flag(self):
        self._writeExports()
        self.logic.hasUnsavedWork = True
        self.manager.restoreExport(self.caseDir)
        self.assertFalse(self.logic.hasUnsavedWork)


if __name__ == '__main__':
    unittest.main()
