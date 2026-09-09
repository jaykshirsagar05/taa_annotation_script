"""
Tests for AutoUpdater's fresh-install replace, payload validation and rollback.

MODULE_DIR is patched to a temporary tree so no real install is touched.
"""

import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock, patch

from TAAAnnotationLib import AutoUpdater


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)


def _tree(root):
    found = set()
    for dirPath, _dirNames, fileNames in os.walk(root):
        for name in fileNames:
            found.add(os.path.relpath(os.path.join(dirPath, name), root))
    return found


class AutoUpdaterInstallTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.live = os.path.join(self.tmp, 'live')
        self.payload = os.path.join(self.tmp, 'payload')

        # A 1.3.1-era install: Orthanc modules plus cached bytecode for them.
        _write(os.path.join(self.live, 'VERSION'), '1.3.1\n')
        _write(os.path.join(self.live, 'TAAAnnotation.py'), 'old module\n')
        _write(os.path.join(self.live, 'TAAAnnotationLib', '__init__.py'), 'old init\n')
        _write(os.path.join(self.live, 'TAAAnnotationLib', 'AutoUpdater.py'), 'old updater\n')
        _write(os.path.join(self.live, 'TAAAnnotationLib', 'OrthancClient.py'), 'stale\n')
        _write(os.path.join(self.live, 'TAAAnnotationLib', 'DatasetProfile.py'), 'stale\n')
        _write(os.path.join(self.live, 'TAAAnnotationLib', '__pycache__',
                            'OrthancClient.cpython-39.pyc'), 'stale bytecode\n')

        # A 2.0.0 payload: no Orthanc modules, one new module.
        _write(os.path.join(self.payload, 'VERSION'), '2.0.0\n')
        _write(os.path.join(self.payload, 'TAAAnnotation.py'), 'new module\n')
        _write(os.path.join(self.payload, 'TAAAnnotationLib', '__init__.py'), 'new init\n')
        _write(os.path.join(self.payload, 'TAAAnnotationLib', 'AutoUpdater.py'), 'new updater\n')
        _write(os.path.join(self.payload, 'TAAAnnotationLib', 'LocalDataset.py'), 'new\n')

        self.patcher = patch.object(AutoUpdater, 'MODULE_DIR', self.live)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stale_files_are_removed(self):
        AutoUpdater._installFresh(self.payload, os.path.join(self.tmp, 'backup'))
        self.assertEqual(_tree(self.live), _tree(self.payload))

    def test_stale_bytecode_is_removed(self):
        AutoUpdater._installFresh(self.payload, os.path.join(self.tmp, 'backup'))
        self.assertFalse(os.path.exists(os.path.join(self.live, 'TAAAnnotationLib', '__pycache__')))

    def test_payload_contents_replace_live_contents(self):
        AutoUpdater._installFresh(self.payload, os.path.join(self.tmp, 'backup'))
        with open(os.path.join(self.live, 'VERSION')) as f:
            self.assertEqual(f.read().strip(), '2.0.0')
        with open(os.path.join(self.live, 'TAAAnnotationLib', 'AutoUpdater.py')) as f:
            self.assertEqual(f.read().strip(), 'new updater')

    def test_module_dir_itself_survives(self):
        liveStat = os.stat(self.live)
        AutoUpdater._installFresh(self.payload, os.path.join(self.tmp, 'backup'))
        self.assertTrue(os.path.isdir(self.live))
        self.assertEqual(os.stat(self.live).st_ino, liveStat.st_ino)

    def test_rollback_restores_previous_tree(self):
        before = _tree(self.live)
        realCopy = AutoUpdater._copyTreeInto
        calls = []

        def failFirstCopy(srcDir, dstDir):
            calls.append(srcDir)
            if len(calls) == 1:
                os.makedirs(os.path.join(dstDir, 'half-written'), exist_ok=True)
                raise OSError('disk full')
            return realCopy(srcDir, dstDir)

        with patch.object(AutoUpdater, '_copyTreeInto', failFirstCopy):
            with self.assertRaises(OSError):
                AutoUpdater._installFresh(self.payload, os.path.join(self.tmp, 'backup'))

        self.assertEqual(_tree(self.live), before)
        with open(os.path.join(self.live, 'VERSION')) as f:
            self.assertEqual(f.read().strip(), '1.3.1')


class AutoUpdaterPayloadValidationTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _completePayload(self):
        root = os.path.join(self.tmp, 'payload')
        for rel in AutoUpdater.REQUIRED_PAYLOAD:
            _write(os.path.join(root, rel), 'x\n')
        return root

    def test_complete_payload_passes(self):
        self.assertEqual(AutoUpdater._missingFromPayload(self._completePayload()), [])

    def test_missing_updater_is_reported(self):
        root = self._completePayload()
        os.remove(os.path.join(root, 'TAAAnnotationLib', 'AutoUpdater.py'))
        self.assertIn(os.path.join('TAAAnnotationLib', 'AutoUpdater.py'),
                      AutoUpdater._missingFromPayload(root))

    def test_missing_version_is_reported(self):
        root = self._completePayload()
        os.remove(os.path.join(root, 'VERSION'))
        self.assertIn('VERSION', AutoUpdater._missingFromPayload(root))

    def test_empty_payload_reports_every_requirement(self):
        empty = os.path.join(self.tmp, 'empty')
        os.makedirs(empty)
        self.assertEqual(sorted(AutoUpdater._missingFromPayload(empty)),
                         sorted(AutoUpdater.REQUIRED_PAYLOAD))


class AutoUpdaterDownloadTest(unittest.TestCase):
    """End-to-end cover of the zipball download, extraction and swap."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.live = os.path.join(self.tmp, 'live')
        _write(os.path.join(self.live, 'VERSION'), '1.3.1\n')
        _write(os.path.join(self.live, 'TAAAnnotation.py'), 'old\n')
        _write(os.path.join(self.live, 'TAAAnnotationLib', '__init__.py'), 'old\n')
        _write(os.path.join(self.live, 'TAAAnnotationLib', 'AutoUpdater.py'), 'old\n')
        _write(os.path.join(self.live, 'TAAAnnotationLib', 'OrthancClient.py'), 'stale\n')
        self.patcher = patch.object(AutoUpdater, 'MODULE_DIR', self.live)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _zipball(self, entries):
        """Build a GitHub-style zipball: everything under one repo-sha root folder."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            for rel, text in entries.items():
                zf.writestr(os.path.join('jaykshirsagar05-taa_annotation_script-abc1234', rel), text)
        return buf.getvalue()

    def _install(self, zipBytes):
        fakeResp = MagicMock()
        fakeResp.iter_content.return_value = [zipBytes]
        fakeRequests = MagicMock()
        fakeRequests.get.return_value = fakeResp
        with patch.dict(sys.modules, {'requests': fakeRequests}):
            AutoUpdater._downloadAndInstall('https://example.invalid/zipball', '2.0.0')

    def test_full_release_replaces_install(self):
        self._install(self._zipball({
            'TAAAnnotation/VERSION': '2.0.0\n',
            'TAAAnnotation/TAAAnnotation.py': 'new\n',
            'TAAAnnotation/TAAAnnotationLib/__init__.py': 'new\n',
            'TAAAnnotation/TAAAnnotationLib/AutoUpdater.py': 'new\n',
            'TAAAnnotation/TAAAnnotationLib/LocalDataset.py': 'new\n',
            'tests/test_local_dataset.py': 'not part of the module\n',
        }))
        self.assertEqual(_tree(self.live), {
            'VERSION', 'TAAAnnotation.py',
            os.path.join('TAAAnnotationLib', '__init__.py'),
            os.path.join('TAAAnnotationLib', 'AutoUpdater.py'),
            os.path.join('TAAAnnotationLib', 'LocalDataset.py'),
        })

    def test_truncated_release_leaves_install_untouched(self):
        before = _tree(self.live)
        self._install(self._zipball({'TAAAnnotation/VERSION': '2.0.0\n'}))
        self.assertEqual(_tree(self.live), before)
        with open(os.path.join(self.live, 'VERSION')) as f:
            self.assertEqual(f.read().strip(), '1.3.1')

    def test_zip_without_module_dir_leaves_install_untouched(self):
        before = _tree(self.live)
        self._install(self._zipball({'README.md': 'wrong shape\n'}))
        self.assertEqual(_tree(self.live), before)


if __name__ == '__main__':
    unittest.main()
