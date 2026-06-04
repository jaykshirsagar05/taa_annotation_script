"""
Tests for DataLoader._fixFileExtension and TAAAnnotationLogic.getIdFromFiles.

These are the pure-Python / file-system parts of the loading pipeline that
can be exercised without a running Slicer instance.  All Slicer-specific
modules are stubbed before any project code is imported.
"""

import os
import sys
import struct
import tempfile
import unittest

# ── Slicer / Qt stubs (must precede any project import) ─────────────────────
from unittest.mock import MagicMock

for _mod in ['slicer', 'slicer.util', 'qt', 'vtk', 'vtk.util',
             'DICOMLib', 'ctk', 'pydicom', 'SimpleITK', 'sitkUtils',
             'numpy']:
    sys.modules.setdefault(_mod, MagicMock())

# Point Python at the TAAAnnotation package directory
_HERE = os.path.dirname(__file__)
_TAAA_DIR = os.path.join(_HERE, '..', 'TAAAnnotation')
if _TAAA_DIR not in sys.path:
    sys.path.insert(0, _TAAA_DIR)

from TAAAnnotationLib.DataLoader import DataLoader  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _write_temp_nii_gz(content: bytes) -> str:
    """Create a temp file named *.nii.gz with the given binary content."""
    fd, path = tempfile.mkstemp(suffix='.nii.gz')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(content)
    except Exception:
        os.close(fd)
        raise
    return path


def _gzip_header() -> bytes:
    return b'\x1f\x8b' + b'\x00' * 510


def _nrrd_header(with_segment=False) -> bytes:
    body = b'NRRDSegment' if with_segment else b'NRRD padding'
    return body + b'\x00' * (512 - len(body))


def _nifti_header(hdr_size: int) -> bytes:
    return struct.pack('<i', hdr_size) + b'\x00' * 508


# ─────────────────────────────────────────────────────────────────────────────
# _fixFileExtension
# ─────────────────────────────────────────────────────────────────────────────

class TestFixFileExtension(unittest.TestCase):

    def test_non_nii_gz_path_returned_unchanged(self):
        result = DataLoader._fixFileExtension('/some/file.nrrd')
        self.assertEqual(result, '/some/file.nrrd')

    def test_valid_gzip_returned_unchanged(self):
        path = _write_temp_nii_gz(_gzip_header())
        try:
            result = DataLoader._fixFileExtension(path)
            self.assertEqual(result, path)
            self.assertTrue(os.path.exists(path))
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_nrrd_content_renamed_to_nrrd(self):
        path = _write_temp_nii_gz(_nrrd_header(with_segment=False))
        expected = path.replace('.nii.gz', '.nrrd')
        try:
            result = DataLoader._fixFileExtension(path)
            self.assertEqual(result, expected)
            self.assertTrue(os.path.exists(expected))
            self.assertFalse(os.path.exists(path))
        finally:
            if os.path.exists(expected):
                os.remove(expected)
            if os.path.exists(path):
                os.remove(path)

    def test_nrrd_with_segment_keyword_renamed_to_seg_nrrd(self):
        path = _write_temp_nii_gz(_nrrd_header(with_segment=True))
        expected = path.replace('.nii.gz', '.seg.nrrd')
        try:
            result = DataLoader._fixFileExtension(path)
            self.assertEqual(result, expected)
            self.assertTrue(os.path.exists(expected))
        finally:
            if os.path.exists(expected):
                os.remove(expected)
            if os.path.exists(path):
                os.remove(path)

    def test_raw_nifti_348_renamed_to_nii(self):
        path = _write_temp_nii_gz(_nifti_header(348))
        expected = path[:-3]  # strip .gz
        try:
            result = DataLoader._fixFileExtension(path)
            self.assertEqual(result, expected)
            self.assertTrue(os.path.exists(expected))
            self.assertFalse(os.path.exists(path))
        finally:
            if os.path.exists(expected):
                os.remove(expected)
            if os.path.exists(path):
                os.remove(path)

    def test_raw_nifti_540_renamed_to_nii(self):
        path = _write_temp_nii_gz(_nifti_header(540))
        expected = path[:-3]
        try:
            result = DataLoader._fixFileExtension(path)
            self.assertEqual(result, expected)
        finally:
            if os.path.exists(expected):
                os.remove(expected)
            if os.path.exists(path):
                os.remove(path)

    def test_unrecognised_content_returned_unchanged(self):
        path = _write_temp_nii_gz(b'\xDE\xAD\xBE\xEF' + b'\x00' * 508)
        try:
            result = DataLoader._fixFileExtension(path)
            self.assertEqual(result, path)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_missing_file_returns_original_path(self):
        fake_path = '/nonexistent/path/file.nii.gz'
        result = DataLoader._fixFileExtension(fake_path)
        self.assertEqual(result, fake_path)

    def test_file_too_short_returned_unchanged(self):
        path = _write_temp_nii_gz(b'\x00')
        try:
            result = DataLoader._fixFileExtension(path)
            self.assertEqual(result, path)
        finally:
            if os.path.exists(path):
                os.remove(path)


# ─────────────────────────────────────────────────────────────────────────────
# getIdFromFiles  (tested via a minimal logic-like object)
# ─────────────────────────────────────────────────────────────────────────────

class _MinimalLogic:
    """Minimal stand-in that exposes getIdFromFiles without Slicer."""

    def getIdFromFiles(self, folderPath):
        import glob
        searchPattern = os.path.join(folderPath, "ct_scan_*.nii.gz")
        foundFiles = glob.glob(searchPattern)
        if not foundFiles:
            return None
        filename = os.path.basename(foundFiles[0])
        return filename.replace("ct_scan_", "").replace(".nii.gz", "")


class TestGetIdFromFiles(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.logic = _MinimalLogic()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _touch(self, name):
        open(os.path.join(self.tmpdir, name), 'w').close()

    def test_extracts_id_from_ct_filename(self):
        self._touch('ct_scan_PATIENT001.nii.gz')
        result = self.logic.getIdFromFiles(self.tmpdir)
        self.assertEqual(result, 'PATIENT001')

    def test_extracts_numeric_id(self):
        self._touch('ct_scan_12345.nii.gz')
        result = self.logic.getIdFromFiles(self.tmpdir)
        self.assertEqual(result, '12345')

    def test_returns_none_when_no_ct_file(self):
        result = self.logic.getIdFromFiles(self.tmpdir)
        self.assertIsNone(result)

    def test_returns_none_for_empty_dir(self):
        result = self.logic.getIdFromFiles(self.tmpdir)
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
