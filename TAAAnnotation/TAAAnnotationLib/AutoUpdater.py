import os
import json
import zipfile
import shutil
import tempfile

GITHUB_REPO = "jaykshirsagar05/taa_annotation_script"
MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # TAAAnnotation/
VERSION_FILE = os.path.join(MODULE_DIR, "VERSION")
API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


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


def _downloadAndInstall(zipUrl, newVersion):
    try:
        import requests
        import slicer

        tmpDir = tempfile.mkdtemp()
        zipPath = os.path.join(tmpDir, "update.zip")

        print("[AutoUpdater] Downloading update...")
        resp = requests.get(zipUrl, stream=True, timeout=60)
        resp.raise_for_status()
        with open(zipPath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        with zipfile.ZipFile(zipPath, "r") as zf:
            zf.extractall(tmpDir)

        # GitHub zipball has a root folder like "user-repo-abc1234/"
        extracted = [
            d for d in os.listdir(tmpDir)
            if os.path.isdir(os.path.join(tmpDir, d)) and d != "__MACOSX"
        ]
        if not extracted:
            print("[AutoUpdater] Could not locate extracted folder")
            return

        srcModuleDir = os.path.join(tmpDir, extracted[0], "TAAAnnotation")
        if not os.path.exists(srcModuleDir):
            print("[AutoUpdater] TAAAnnotation/ not found in downloaded zip")
            return

        # Overwrite current module files with new ones
        for item in os.listdir(srcModuleDir):
            src = os.path.join(srcModuleDir, item)
            dst = os.path.join(MODULE_DIR, item)
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

        shutil.rmtree(tmpDir, ignore_errors=True)
        print(f"[AutoUpdater] Successfully updated to v{newVersion}")

        slicer.util.infoDisplay(
            f"TAA Annotation has been updated to v{newVersion}.\n\nPlease restart Slicer to apply the update.",
            windowTitle="TAA Annotation — Update Applied"
        )

    except Exception as e:
        print(f"[AutoUpdater] Install failed: {e}")
