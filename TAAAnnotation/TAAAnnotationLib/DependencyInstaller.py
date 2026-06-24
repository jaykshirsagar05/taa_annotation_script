"""
DependencyInstaller.py — Auto-install required Python packages into Slicer.

Called once at module startup (deferred by 1 s so it doesn't block loading).
Each package is checked with a fast importlib probe before pip is invoked,
so the cost on subsequent startups is negligible.
"""

import importlib

# (import_name, pip_install_name)
REQUIRED = [
    ("requests", "requests"),
    ("pydicom",  "pydicom"),
]


def ensureDependencies():
    """Install any missing packages into Slicer's Python environment.

    Safe to call from the main thread — pip_install is synchronous but only
    does real work on the first run per machine (~2–5 s); subsequent calls
    return immediately after the import probe succeeds.
    """
    missing = []
    for import_name, pip_name in REQUIRED:
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append((import_name, pip_name))

    if not missing:
        return

    try:
        import slicer
    except ImportError:
        # Running outside Slicer (e.g., unit tests) — skip silently
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
