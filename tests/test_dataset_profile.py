"""
Tests for DatasetProfile detection logic.

DatasetProfile.py is pure Python (no Slicer dependency), so these tests
run without any stubs.
"""

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(__file__)
_TAAA_DIR = os.path.join(_HERE, '..', 'TAAAnnotation')
if _TAAA_DIR not in sys.path:
    sys.path.insert(0, _TAAA_DIR)

from TAAAnnotationLib.DatasetProfile import (  # noqa: E402
    DatasetProfile,
    PROFILES,
    detect_profile_from_attachments,
    detect_profile_from_folder,
    download_filename,
)


# ─────────────────────────────────────────────────────────────────────────────
# detect_profile_from_attachments
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectFromAttachments(unittest.TestCase):

    def _detect(self, **flags):
        return detect_profile_from_attachments(flags)

    def test_dicom_native_takes_priority(self):
        profile = self._detect(ct_dicom=True, seg_dicom=True,
                               unified_mask=True, merged_mask=True,
                               centerline=True)
        self.assertEqual(profile.profile_type, DatasetProfile.DICOM_NATIVE)

    def test_mask_and_centerline_when_centerline_present(self):
        profile = self._detect(ct_nifti=True, unified_mask=True, centerline=True)
        self.assertEqual(profile.profile_type, DatasetProfile.MASK_AND_CENTERLINE)

    def test_dual_mask_when_both_nifti_masks(self):
        profile = self._detect(ct_nifti=True, unified_mask=True, merged_mask=True)
        self.assertEqual(profile.profile_type, DatasetProfile.DUAL_MASK)

    def test_ct_seg_mask_only(self):
        profile = self._detect(ct_nifti=True, unified_mask=True)
        self.assertEqual(profile.profile_type, DatasetProfile.CT_AND_SEG_MASK)

    def test_ct_dicom_without_seg_dicom_not_dicom_native(self):
        profile = self._detect(ct_dicom=True, unified_mask=True)
        # ct_dicom only, no seg_dicom → falls through to NIfTI path
        # unified is present but ct_nifti is False → only ct_dicom counts as ct
        # NIfTI path: has_ct = has_ct_nifti or has_ct_dicom → True
        self.assertEqual(profile.profile_type, DatasetProfile.CT_AND_SEG_MASK)

    def test_returns_none_when_no_ct(self):
        profile = self._detect(unified_mask=True, merged_mask=True)
        self.assertIsNone(profile)

    def test_returns_none_when_nothing(self):
        self.assertIsNone(self._detect())

    def test_mask_and_centerline_skips_phase_3(self):
        profile = self._detect(ct_nifti=True, unified_mask=True, centerline=True)
        self.assertTrue(profile.should_skip_phase(3))
        self.assertFalse(profile.should_skip_phase(1))

    def test_dual_mask_skips_no_phases(self):
        profile = self._detect(ct_nifti=True, unified_mask=True, merged_mask=True)
        self.assertFalse(profile.should_skip_phase(1))
        self.assertFalse(profile.should_skip_phase(3))


# ─────────────────────────────────────────────────────────────────────────────
# detect_profile_from_folder
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectFromFolder(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _touch(self, *names):
        for name in names:
            open(os.path.join(self.tmpdir, name), 'w').close()

    def _detect(self, pid='P001'):
        return detect_profile_from_folder(self.tmpdir, pid)

    def test_dual_mask_profile(self):
        self._touch('ct_scan_P001.nii.gz',
                    'P001_unified_mask_smoothed.nii.gz',
                    'P001_merged_mask.nii.gz')
        profile, found = self._detect()
        self.assertIsNotNone(profile)
        self.assertEqual(profile.profile_type, DatasetProfile.DUAL_MASK)
        self.assertIn('ct', found)
        self.assertIn('seg_mask', found)
        self.assertIn('ref_mask', found)

    def test_mask_and_centerline_profile_from_vtk(self):
        self._touch('ct_scan_P001.nii.gz',
                    'P001_unified_mask_smoothed.nii.gz',
                    'P001_Centerline.vtk')
        profile, found = self._detect()
        self.assertEqual(profile.profile_type, DatasetProfile.MASK_AND_CENTERLINE)
        self.assertIn('centerline', found)

    def test_mask_and_centerline_profile_from_vtp(self):
        self._touch('ct_scan_P001.nii.gz',
                    'P001_unified_mask_smoothed.nii.gz',
                    'P001_centerline.vtp')
        profile, found = self._detect()
        self.assertEqual(profile.profile_type, DatasetProfile.MASK_AND_CENTERLINE)

    def test_ct_and_seg_mask_only(self):
        self._touch('ct_scan_P001.nii.gz',
                    'P001_unified_mask_smoothed.nii.gz')
        profile, found = self._detect()
        self.assertEqual(profile.profile_type, DatasetProfile.CT_AND_SEG_MASK)
        self.assertNotIn('ref_mask', found)

    def test_returns_none_when_no_ct_file(self):
        self._touch('P001_unified_mask_smoothed.nii.gz')
        profile, found = self._detect()
        self.assertIsNone(profile)

    def test_returns_none_when_no_seg_file(self):
        self._touch('ct_scan_P001.nii.gz')
        profile, found = self._detect()
        self.assertIsNone(profile)

    def test_empty_folder_returns_none(self):
        profile, found = self._detect()
        self.assertIsNone(profile)
        self.assertEqual(found, {})

    def test_lone_vtp_file_used_as_centerline(self):
        self._touch('ct_scan_P001.nii.gz',
                    'P001_unified_mask_smoothed.nii.gz',
                    'unknown_centerline.vtp')
        profile, found = self._detect()
        self.assertEqual(profile.profile_type, DatasetProfile.MASK_AND_CENTERLINE)
        self.assertIn('centerline', found)

    def test_multiple_vtp_files_not_autoselected(self):
        """Two .vtp files → ambiguous; no centerline auto-assigned."""
        self._touch('ct_scan_P001.nii.gz',
                    'P001_unified_mask_smoothed.nii.gz',
                    'cl_a.vtp', 'cl_b.vtp')
        profile, found = self._detect()
        # Two vtp candidates means no auto-detection → falls to CT+Seg
        self.assertEqual(profile.profile_type, DatasetProfile.CT_AND_SEG_MASK)

    def test_centerline_priority_over_dual_mask(self):
        """Centerline + dual masks → Mask+Centerline wins."""
        self._touch('ct_scan_P001.nii.gz',
                    'P001_unified_mask_smoothed.nii.gz',
                    'P001_merged_mask.nii.gz',
                    'P001_Centerline.vtk')
        profile, _ = self._detect()
        self.assertEqual(profile.profile_type, DatasetProfile.MASK_AND_CENTERLINE)


# ─────────────────────────────────────────────────────────────────────────────
# DatasetProfile attributes
# ─────────────────────────────────────────────────────────────────────────────

class TestDatasetProfileAttributes(unittest.TestCase):

    def test_ct_and_seg_mask_binarizes_before_refine(self):
        profile = PROFILES[DatasetProfile.CT_AND_SEG_MASK]
        self.assertTrue(profile.binarize_mask_before_refine)

    def test_dual_mask_does_not_binarize_before_refine(self):
        profile = PROFILES[DatasetProfile.DUAL_MASK]
        self.assertFalse(profile.binarize_mask_before_refine)

    def test_mask_and_centerline_has_precalculated_centerline(self):
        profile = PROFILES[DatasetProfile.MASK_AND_CENTERLINE]
        self.assertTrue(profile.has_precalculated_centerline)

    def test_dual_mask_no_precalculated_centerline(self):
        profile = PROFILES[DatasetProfile.DUAL_MASK]
        self.assertFalse(profile.has_precalculated_centerline)

    def test_dicom_native_binarizes_before_refine(self):
        profile = PROFILES[DatasetProfile.DICOM_NATIVE]
        self.assertTrue(profile.binarize_mask_before_refine)

    def test_repr_contains_profile_type_and_name(self):
        profile = PROFILES[DatasetProfile.DUAL_MASK]
        r = repr(profile)
        self.assertIn(DatasetProfile.DUAL_MASK, r)

    def test_should_skip_phase_true(self):
        profile = PROFILES[DatasetProfile.MASK_AND_CENTERLINE]
        self.assertTrue(profile.should_skip_phase(3))

    def test_should_skip_phase_false(self):
        profile = PROFILES[DatasetProfile.DUAL_MASK]
        self.assertFalse(profile.should_skip_phase(3))


# ─────────────────────────────────────────────────────────────────────────────
# download_filename
# ─────────────────────────────────────────────────────────────────────────────

class TestDownloadFilename(unittest.TestCase):

    def test_ct_filename(self):
        self.assertEqual(download_filename("ct", "P001"), "ct_scan_P001.nii.gz")

    def test_seg_mask_filename(self):
        self.assertEqual(
            download_filename("seg_mask", "P001"),
            "P001_unified_mask_smoothed.nii.gz"
        )

    def test_ref_mask_filename(self):
        self.assertEqual(
            download_filename("ref_mask", "P001"),
            "P001_merged_mask.nii.gz"
        )

    def test_centerline_filename(self):
        self.assertEqual(
            download_filename("centerline", "P001"),
            "P001_Centerline.vtk"
        )

    def test_unknown_key_fallback(self):
        result = download_filename("unknown_key", "P001")
        self.assertIn("P001", result)
        self.assertIn("unknown_key", result)


if __name__ == '__main__':
    unittest.main()
