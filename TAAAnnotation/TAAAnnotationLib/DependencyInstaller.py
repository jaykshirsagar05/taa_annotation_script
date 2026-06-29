"""
DependencyInstaller.py — Auto-install required Python packages and Slicer extensions,
and initialize the DICOM database if it is not already open.

Called once at module startup (deferred by 1 s so Slicer finishes loading first).
Each check is fast on subsequent startups — packages are probed with importlib,
extensions with isExtensionInstalled(), and the database with db.isOpen.
"""

import importlib
import os

# ---------------------------------------------------------------------------
# Python packages required at runtime
# ---------------------------------------------------------------------------

# (import_name, pip_install_name)
_REQUIRED_PACKAGES = [
    ("requests", "requests"),
    ("pydicom",  "pydicom"),
]

# ---------------------------------------------------------------------------
# Slicer extensions required for full functionality
# (extension_name, human_readable_reason)
# ---------------------------------------------------------------------------

_REQUIRED_EXTENSIONS = [
    (
        "QuantitativeReporting",
        "DICOM SEG segmentation loading (DICOMSegPlugin)",
    ),
]

# SlicerVMTK / ExtractCenterline is already declared in
# self.parent.dependencies in TAAAnnotation.py, so Slicer will warn at load
# time if it is absent — no need to duplicate that check here.


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def ensureDependencies():
    """Install missing packages, initialize the DICOM database, and check
    required Slicer extensions.

    Safe to call from the main thread.  Each sub-step is idempotent and only
    does real work on the first run per machine.
    """
    _ensurePackages()
    _ensureDicomDatabase()
    _ensureExtensions()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensurePackages():
    """Install any missing pip packages into Slicer's Python environment."""
    missing = []
    for import_name, pip_name in _REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append((import_name, pip_name))

    if not missing:
        return

    try:
        import slicer
    except ImportError:
        # Running outside Slicer (unit tests) — skip silently.
        return

    pkg_labels = ", ".join(p for _, p in missing)
    print(f"[TAAAnnotation] Installing missing packages: {pkg_labels}")
    slicer.util.showStatusMessage(
        f"TAA Annotation: installing {pkg_labels} — please wait…"
    )
    slicer.app.processEvents()

    for import_name, pip_name in missing:
        try:
            slicer.util.pip_install(pip_name)
            print(f"[TAAAnnotation] Installed {pip_name}")
        except Exception as e:
            print(f"[TAAAnnotation] Could not install {pip_name}: {e}")

    slicer.util.showStatusMessage("")


def _ensureDicomDatabase():
    """Ensure slicer.dicomDatabase is open.

    On a fresh Slicer install the database may not be initialized if the user
    has never opened the DICOM module.  This function tries, in order:
      1. The path persisted in Slicer's QSettings (the normal path).
      2. A fallback directory inside slicer.app.temporaryPath.
    """
    try:
        import slicer
        import qt
    except ImportError:
        return

    db = slicer.dicomDatabase

    # isOpen is a Qt property exposed as an attribute in ctk Python bindings.
    try:
        already_open = db.isOpen
    except Exception:
        already_open = False

    if already_open:
        return

    print("[TAAAnnotation] DICOM database not open — attempting to initialize…")

    def _try_open(db_dir):
        try:
            db_path = os.path.join(db_dir, "ctkDICOM.sql")
            db.openDatabase(db_path)
            return bool(db.isOpen)
        except Exception as e:
            print(f"[TAAAnnotation] openDatabase({db_dir!r}) failed: {e}")
            return False

    # 1. Path from Slicer application settings
    settings = qt.QSettings()
    settings_dir = settings.value("DatabaseDirectory", "")
    if settings_dir and os.path.isdir(settings_dir):
        if _try_open(settings_dir):
            print(f"[TAAAnnotation] DICOM database opened from settings: {settings_dir}")
            return

    # 2. Fall back to a directory inside Slicer's temp folder
    fallback_dir = os.path.join(slicer.app.temporaryPath, "CtkDicomDatabase")
    try:
        os.makedirs(fallback_dir, exist_ok=True)
    except OSError:
        pass

    if _try_open(fallback_dir):
        print(f"[TAAAnnotation] DICOM database initialized at: {fallback_dir}")
        # Persist the path so Slicer and the DICOM module pick it up next launch.
        try:
            settings.setValue("DatabaseDirectory", fallback_dir)
        except Exception:
            pass
    else:
        print(
            "[TAAAnnotation] WARNING — could not open DICOM database.\n"
            "  CT DICOM loading may fail.  Open the DICOM module once to "
            "configure the database directory."
        )


def _ensureExtensions():
    """Check required Slicer extensions and offer to install any that are missing.

    Installation requires a network connection and a Slicer restart to take
    effect.  If auto-install fails, a warning dialog shows manual instructions.
    """
    try:
        import slicer
        em = slicer.app.extensionsManagerModel()
    except (ImportError, AttributeError):
        return

    missing = [
        (name, reason)
        for name, reason in _REQUIRED_EXTENSIONS
        if not em.isExtensionInstalled(name)
    ]

    if not missing:
        return

    # Log immediately so the developer sees it even if the dialog is dismissed.
    for name, reason in missing:
        print(f"[TAAAnnotation] Missing Slicer extension: {name} — {reason}")

    # Attempt auto-install for each missing extension.
    auto_installed = []
    failed = []

    slicer.util.showStatusMessage(
        "TAA Annotation: fetching extension metadata — please wait…"
    )
    slicer.app.processEvents()

    try:
        # updateExtensionsMetadataFromServer(wait=True, quiet=True)
        em.updateExtensionsMetadataFromServer(True, True)
    except Exception as e:
        print(f"[TAAAnnotation] Could not fetch extension metadata: {e}")

    slicer.util.showStatusMessage("")

    for ext_name, reason in missing:
        try:
            # Strategy 1: by name (works in Slicer ≥ 5.6 builds that expose
            # a name-based overload of downloadAndInstallExtension).
            installed = em.downloadAndInstallExtension(ext_name)
            if installed:
                auto_installed.append(ext_name)
                print(f"[TAAAnnotation] Extension installed: {ext_name}")
                continue
        except Exception:
            pass

        try:
            # Strategy 2: resolve extension ID from metadata then install.
            metadata = em.extensionMetadataFromName(ext_name)
            ext_id = (metadata.get("extension_id")
                      or metadata.get("ExtensionID", ""))
            if ext_id:
                installed = em.downloadAndInstallExtension(ext_id)
                if installed:
                    auto_installed.append(ext_name)
                    print(f"[TAAAnnotation] Extension installed (via ID): {ext_name}")
                    continue
        except Exception as e:
            print(f"[TAAAnnotation] Auto-install failed for {ext_name}: {e}")

        failed.append((ext_name, reason))

    # Offer restart if anything was installed.
    if auto_installed:
        msg = (
            f"The following Slicer extension(s) were installed:\n"
            f"  {', '.join(auto_installed)}\n\n"
            "A Slicer restart is required to activate them.\n"
            "Restart now?"
        )
        if slicer.util.confirmYesNoDisplay(msg, windowTitle="Restart Required"):
            slicer.app.restart()
        return

    # Could not auto-install — show a clear manual instruction dialog.
    if failed:
        lines = "\n".join(f"  • {name}  ({reason})" for name, reason in failed)
        slicer.util.warningDisplay(
            "TAA Annotation requires the following Slicer extension(s) "
            "that are not currently installed:\n\n"
            f"{lines}\n\n"
            "To install them:\n"
            "  1. Open the Extensions Manager  "
            "(View → Extension Manager, or the puzzle icon in the toolbar)\n"
            "  2. Search for each extension by name\n"
            "  3. Click Install, then restart Slicer\n\n"
            "DICOM segmentation loading will not work until "
            "QuantitativeReporting is installed.",
            windowTitle="Missing Extensions",
        )
