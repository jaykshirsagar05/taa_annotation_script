"""
Tests for LocalDataset — case discovery, output naming, and status derivation.

Pure file-system logic; no Slicer process required.
"""

import json
import os
import shutil
import tempfile
import unittest

from TAAAnnotationLib import LocalDataset


def _touch(path):
    with open(path, 'w') as f:
        f.write("x")


class _TempDirTestCase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _makeCase(self, name, ct="scan_ct.nii.gz", mask="scan_mask.nii.gz"):
        caseDir = os.path.join(self.root, name)
        os.makedirs(caseDir)
        if ct:
            _touch(os.path.join(caseDir, ct))
        if mask:
            _touch(os.path.join(caseDir, mask))
        return caseDir


class TestCaseId(_TempDirTestCase):

    def test_case_id_is_folder_name(self):
        caseDir = self._makeCase("scan_001")
        self.assertEqual(LocalDataset.case_id(caseDir), "scan_001")

    def test_trailing_separator_ignored(self):
        caseDir = self._makeCase("scan_002")
        self.assertEqual(LocalDataset.case_id(caseDir + os.sep), "scan_002")


class TestFindInputs(_TempDirTestCase):

    def test_matches_ct_and_mask_tokens(self):
        caseDir = self._makeCase("s1", "TAA042_ct.nii.gz", "TAA042_mask.nii.gz")
        found = LocalDataset.find_inputs(caseDir)
        self.assertEqual(os.path.basename(found["ct"]), "TAA042_ct.nii.gz")
        self.assertEqual(os.path.basename(found["mask"]), "TAA042_mask.nii.gz")

    def test_mask_token_wins_when_both_tokens_present(self):
        caseDir = self._makeCase("s1", "a_ct.nii.gz", "a_ct_mask.nii.gz")
        found = LocalDataset.find_inputs(caseDir)
        self.assertEqual(os.path.basename(found["ct"]), "a_ct.nii.gz")
        self.assertEqual(os.path.basename(found["mask"]), "a_ct_mask.nii.gz")

    def test_token_match_is_case_insensitive(self):
        caseDir = self._makeCase("s1", "SCAN_CT.NII.GZ", "SCAN_MASK.NII.GZ")
        found = LocalDataset.find_inputs(caseDir)
        self.assertIn("ct", found)
        self.assertIn("mask", found)

    def test_plain_nii_accepted(self):
        caseDir = self._makeCase("s1", "x_ct.nii", "x_mask.nii")
        self.assertEqual(set(LocalDataset.find_inputs(caseDir)), {"ct", "mask"})

    def test_non_nifti_files_ignored(self):
        caseDir = self._makeCase("s1")
        _touch(os.path.join(caseDir, "notes_ct.txt"))
        found = LocalDataset.find_inputs(caseDir)
        self.assertEqual(os.path.basename(found["ct"]), "scan_ct.nii.gz")

    def test_missing_mask_reported_as_absent(self):
        caseDir = self._makeCase("s1", mask=None)
        self.assertNotIn("mask", LocalDataset.find_inputs(caseDir))

    def test_nonexistent_directory_returns_empty(self):
        self.assertEqual(LocalDataset.find_inputs("/no/such/dir"), {})

    def test_refined_mask_output_is_not_matched_as_input(self):
        caseDir = self._makeCase("scan_001", "scan_001_ct.nii.gz",
                                 "scan_001_mask.nii.gz")
        _touch(LocalDataset.output_path(caseDir, "refined_mask"))
        found = LocalDataset.find_inputs(caseDir)
        self.assertEqual(os.path.basename(found["mask"]), "scan_001_mask.nii.gz")

    def test_input_renamed_to_nrrd_stays_discoverable(self):
        caseDir = self._makeCase("scan_001", "scan_001_ct.nrrd",
                                 "scan_001_mask.nrrd")
        self.assertEqual(set(LocalDataset.find_inputs(caseDir)), {"ct", "mask"})

    def test_finished_case_still_lists_with_its_original_inputs(self):
        caseDir = self._makeCase("scan_001", "scan_001_ct.nii.gz",
                                 "scan_001_mask.nii.gz")
        for kind in LocalDataset.REQUIRED_OUTPUTS:
            _touch(LocalDataset.output_path(caseDir, kind))
        cases = LocalDataset.list_cases(self.root)
        self.assertEqual(len(cases), 1)
        self.assertEqual(os.path.basename(cases[0]["mask"]),
                         "scan_001_mask.nii.gz")
        self.assertEqual(cases[0]["status"], LocalDataset.STATUS_COMPLETE)

    def test_first_match_wins_deterministically(self):
        caseDir = self._makeCase("s1", "b_ct.nii.gz", "b_mask.nii.gz")
        _touch(os.path.join(caseDir, "a_ct.nii.gz"))
        found = LocalDataset.find_inputs(caseDir)
        self.assertEqual(os.path.basename(found["ct"]), "a_ct.nii.gz")


class TestOutputPath(_TempDirTestCase):

    def test_outputs_are_case_id_prefixed_and_flat(self):
        caseDir = self._makeCase("scan_007")
        path = LocalDataset.output_path(caseDir, "refined_mask")
        self.assertEqual(os.path.dirname(path), caseDir)
        self.assertEqual(os.path.basename(path),
                         "scan_007_refined_mask.seg.nrrd")

    def test_every_output_kind_has_a_path(self):
        caseDir = self._makeCase("scan_008")
        for kind in LocalDataset.OUTPUT_SUFFIXES:
            self.assertTrue(
                os.path.basename(LocalDataset.output_path(caseDir, kind))
                .startswith("scan_008_")
            )


class TestCaseStatus(_TempDirTestCase):

    def _writeCheckpoint(self, caseDir, **fields):
        ckptDir = LocalDataset.checkpoint_dir(caseDir)
        os.makedirs(ckptDir, exist_ok=True)
        state = {"version": 1, "phase": 2, "zone_count": 4}
        state.update(fields)
        with open(LocalDataset.checkpoint_state_path(caseDir), 'w') as f:
            json.dump(state, f)

    def _writeOutputs(self, caseDir, kinds):
        for kind in kinds:
            _touch(LocalDataset.output_path(caseDir, kind))

    def test_fresh_case_is_not_started(self):
        caseDir = self._makeCase("s1")
        self.assertEqual(LocalDataset.case_status(caseDir),
                         (LocalDataset.STATUS_NOT_STARTED, 0))

    def test_checkpoint_makes_case_in_progress(self):
        caseDir = self._makeCase("s1")
        self._writeCheckpoint(caseDir, zone_count=4)
        self.assertEqual(LocalDataset.case_status(caseDir),
                         (LocalDataset.STATUS_IN_PROGRESS, 4))

    def test_all_required_outputs_make_case_complete(self):
        caseDir = self._makeCase("s1")
        self._writeOutputs(caseDir, LocalDataset.REQUIRED_OUTPUTS)
        status, _ = LocalDataset.case_status(caseDir)
        self.assertEqual(status, LocalDataset.STATUS_COMPLETE)

    def test_partial_outputs_are_not_complete(self):
        caseDir = self._makeCase("s1")
        self._writeOutputs(caseDir, ("refined_mask", "centerline"))
        status, _ = LocalDataset.case_status(caseDir)
        self.assertEqual(status, LocalDataset.STATUS_NOT_STARTED)

    def test_complete_beats_checkpoint(self):
        caseDir = self._makeCase("s1")
        self._writeCheckpoint(caseDir)
        self._writeOutputs(caseDir, LocalDataset.REQUIRED_OUTPUTS)
        status, _ = LocalDataset.case_status(caseDir)
        self.assertEqual(status, LocalDataset.STATUS_COMPLETE)

    def test_corrupt_checkpoint_is_ignored(self):
        caseDir = self._makeCase("s1")
        os.makedirs(LocalDataset.checkpoint_dir(caseDir))
        with open(LocalDataset.checkpoint_state_path(caseDir), 'w') as f:
            f.write("{ not json")
        self.assertIsNone(LocalDataset.read_checkpoint_state(caseDir))
        self.assertEqual(LocalDataset.case_status(caseDir),
                         (LocalDataset.STATUS_NOT_STARTED, 0))


class TestCountFcsvPoints(_TempDirTestCase):

    def test_counts_only_data_rows(self):
        path = os.path.join(self.root, "zones.fcsv")
        with open(path, 'w') as f:
            f.write("# Markups fiducial file version = 4.11\n")
            f.write("# columns = id,x,y,z\n")
            f.write("vtkMRMLMarkupsFiducialNode_0,1,2,3\n")
            f.write("\n")
            f.write("vtkMRMLMarkupsFiducialNode_1,4,5,6\n")
        self.assertEqual(LocalDataset.count_fcsv_points(path), 2)

    def test_missing_file_counts_zero(self):
        self.assertEqual(
            LocalDataset.count_fcsv_points(os.path.join(self.root, "no.fcsv")), 0)


class TestListCases(_TempDirTestCase):

    def test_lists_subdirectory_cases_sorted(self):
        self._makeCase("scan_003")
        self._makeCase("scan_001")
        self._makeCase("scan_002")
        cases = LocalDataset.list_cases(self.root)
        self.assertEqual([c["case_id"] for c in cases],
                         ["scan_001", "scan_002", "scan_003"])

    def test_skips_directories_without_both_inputs(self):
        self._makeCase("good")
        self._makeCase("ct_only", mask=None)
        os.makedirs(os.path.join(self.root, "empty"))
        cases = LocalDataset.list_cases(self.root)
        self.assertEqual([c["case_id"] for c in cases], ["good"])

    def test_case_directory_itself_returns_single_entry(self):
        caseDir = self._makeCase("scan_001")
        cases = LocalDataset.list_cases(caseDir)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["case_id"], "scan_001")

    def test_entries_carry_input_paths_and_status(self):
        self._makeCase("scan_001")
        case = LocalDataset.list_cases(self.root)[0]
        self.assertTrue(case["ct"].endswith("scan_ct.nii.gz"))
        self.assertTrue(case["mask"].endswith("scan_mask.nii.gz"))
        self.assertEqual(case["status"], LocalDataset.STATUS_NOT_STARTED)
        self.assertEqual(case["zone_count"], 0)

    def test_nonexistent_root_returns_empty(self):
        self.assertEqual(LocalDataset.list_cases("/no/such/root"), [])


class TestDescribeMissingInputs(_TempDirTestCase):

    def test_empty_when_case_is_valid(self):
        caseDir = self._makeCase("s1")
        self.assertEqual(LocalDataset.describe_missing_inputs(caseDir), "")

    def test_names_the_missing_token_and_lists_what_was_found(self):
        caseDir = self._makeCase("s1", ct="scan_ct.nii.gz", mask=None)
        message = LocalDataset.describe_missing_inputs(caseDir)
        self.assertIn("_mask", message)
        self.assertIn("scan_ct.nii.gz", message)

    def test_reports_when_no_nifti_files_present(self):
        caseDir = os.path.join(self.root, "empty")
        os.makedirs(caseDir)
        message = LocalDataset.describe_missing_inputs(caseDir)
        self.assertIn("no .nii", message)


if __name__ == '__main__':
    unittest.main()
