import os
import zipfile
import shutil
import tempfile

GITHUB_REPO = "jaykshirsagar05/taa_annotation_script"
MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # TAAAnnotation/
VERSION_FILE = os.path.join(MODULE_DIR, "VERSION")
API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# A payload missing any of these would leave an install that cannot load or cannot
# self-update again, so the swap is refused before the live tree is touched.
REQUIRED_PAYLOAD = (
    "VERSION",
    "TAAAnnotation.py",
    os.path.join("TAAAnnotationLib", "__init__.py"),
    os.path.join("TAAAnnotationLib", "AutoUpdater.py"),
)


def getLocalVersion():
    try:
        with open(VERSION_FILE) as f:
            return f.read().strip()
    except Exception:
        return "0.0.0"


def checkAndUpdate():
    """Check GitHub for a newer release and install it if available. Non-blocking via deferred call."""
    try:
        import requests
    except ImportError:
        print("[AutoUpdater] 'requests' not available — skipping update check")
        return

    try:
        resp = requests.get(API_URL, timeout=5)
        if resp.status_code != 200:
            print(f"[AutoUpdater] GitHub API returned {resp.status_code} — skipping")
            return

        release = resp.json()
        latestVersion = release["tag_name"].lstrip("v")
        localVersion = getLocalVersion()

        if latestVersion == localVersion:
            print(f"[AutoUpdater] Up to date (v{localVersion})")
            return

        print(f"[AutoUpdater] Update available: v{localVersion} → v{latestVersion}")
        _downloadAndInstall(release["zipball_url"], latestVersion)

    except Exception as e:
        print(f"[AutoUpdater] Update check failed: {e}")


def _missingFromPayload(srcModuleDir):
    """Return the REQUIRED_PAYLOAD entries absent from srcModuleDir."""
    return [rel for rel in REQUIRED_PAYLOAD
            if not os.path.exists(os.path.join(srcModuleDir, rel))]


def _copyTreeInto(srcDir, dstDir):
    for item in os.listdir(srcDir):
        src = os.path.join(srcDir, item)
        dst = os.path.join(dstDir, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def _clearModuleDir():
    # Empties MODULE_DIR but keeps the directory itself: Slicer registered the module
    # at this path, and on Windows the directory cannot be removed while it is loaded.
    for item in os.listdir(MODULE_DIR):
        path = os.path.join(MODULE_DIR, item)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)


def _installFresh(srcModuleDir, backupDir):
    """Replace the contents of MODULE_DIR with srcModuleDir, rolling back on failure."""
    shutil.copytree(MODULE_DIR, backupDir)
    try:
        _clearModuleDir()
        _copyTreeInto(srcModuleDir, MODULE_DIR)
    except Exception:
        try:
            _clearModuleDir()
            _copyTreeInto(backupDir, MODULE_DIR)
            print("[AutoUpdater] Install failed — rolled back to the previous version")
        except Exception as restoreError:
            print(f"[AutoUpdater] CRITICAL: rollback failed: {restoreError}")
            print(f"[AutoUpdater] A copy of the previous version is at: {backupDir}")
        raise


def _downloadAndInstall(zipUrl, newVersion):
    tmpDir = tempfile.mkdtemp()
    keepTmpDir = False
    try:
        import requests
        import slicer

        zipPath = os.path.join(tmpDir, "update.zip")

        print("[AutoUpdater] Downloading update...")
        resp = requests.get(zipUrl, stream=True, timeout=60)
        resp.raise_for_status()
        with open(zipPath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        extractDir = os.path.join(tmpDir, "extracted")
        with zipfile.ZipFile(zipPath, "r") as zf:
            zf.extractall(extractDir)

        # GitHub zipball has a root folder like "user-repo-abc1234/"
        extracted = [
            d for d in os.listdir(extractDir)
            if os.path.isdir(os.path.join(extractDir, d)) and d != "__MACOSX"
        ]
        if not extracted:
            print("[AutoUpdater] Could not locate extracted folder")
            return

        srcModuleDir = os.path.join(extractDir, extracted[0], "TAAAnnotation")
        if not os.path.exists(srcModuleDir):
            print("[AutoUpdater] TAAAnnotation/ not found in downloaded zip")
            return

        missing = _missingFromPayload(srcModuleDir)
        if missing:
            print(f"[AutoUpdater] Download incomplete, missing {missing} — keeping current version")
            return

        _installFresh(srcModuleDir, os.path.join(tmpDir, "backup"))
        print(f"[AutoUpdater] Successfully updated to v{newVersion}")

        slicer.util.infoDisplay(
            f"TAA Annotation has been updated to v{newVersion}.\n\nPlease restart Slicer to apply the update.",
            windowTitle="TAA Annotation — Update Applied"
        )

    except Exception as e:
        # The rollback copy lives under tmpDir, so leave it in place for recovery.
        keepTmpDir = True
        print(f"[AutoUpdater] Install failed: {e}")

    finally:
        if not keepTmpDir:
            shutil.rmtree(tmpDir, ignore_errors=True)
