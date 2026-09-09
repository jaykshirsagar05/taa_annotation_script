"""
Tests for DataLoader._fixFileExtension.

The pure-Python / file-system part of the loading pipeline, exercised without
a running Slicer instance (modules are stubbed in conftest.py).
"""

import os
import struct
import tempfile
import unittest

from TAAAnnotationLib.DataLoader import DataLoader


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


if __name__ == '__main__':
    unittest.main()
