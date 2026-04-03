"""
OfflineLoadWidget.py - Quick offline data loading for development and testing.

Provides a collapsible panel that lets users load data from a local folder
without needing to log in to Orthanc.  The last-used path and a short
history of recent paths are persisted across Slicer restarts via QSettings.
"""

import os
import qt
import slicer


# QSettings key prefix for this widget
_SETTINGS_PREFIX = "TAAAnnotation/OfflineLoad"
_MAX_RECENT = 5


class OfflineLoadWidget(qt.QWidget):
    """Collapsible panel for one-click offline data loading."""

    # Emitted with the folder path when the user clicks Load / Reload
    loadRequested = qt.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setupUI()
        self._restoreSettings()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setupUI(self):
        outerLayout = qt.QVBoxLayout(self)
        outerLayout.setContentsMargins(0, 0, 0, 0)

        # Collapsible group
        self.group = qt.QGroupBox("⚡ Offline / Dev Loading")
        self.group.setCheckable(True)
        self.group.setChecked(False)  # collapsed by default
        self.group.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 1px solid #ccc; "
            "border-radius: 4px; margin-top: 6px; padding-top: 14px; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )
        groupLayout = qt.QVBoxLayout(self.group)

        # --- Recent paths combo ---
        recentLayout = qt.QHBoxLayout()
        recentLabel = qt.QLabel("Recent:")
        recentLabel.setStyleSheet("font-weight: normal;")
        self.recentCombo = qt.QComboBox()
        self.recentCombo.setSizePolicy(
            qt.QSizePolicy.Expanding, qt.QSizePolicy.Fixed
        )
        self.recentCombo.setToolTip("Previously used data folders")
        self.recentCombo.currentIndexChanged.connect(self._onRecentSelected)
        recentLayout.addWidget(recentLabel)
        recentLayout.addWidget(self.recentCombo)
        groupLayout.addLayout(recentLayout)

        # --- Path input + browse ---
        pathLayout = qt.QHBoxLayout()
        self.pathEdit = qt.QLineEdit()
        self.pathEdit.setPlaceholderText("Path to subject data folder…")
        self.pathEdit.setToolTip(
            "Full path to a folder containing ct_scan_*.nii.gz and mask files"
        )
        btnBrowse = qt.QPushButton("Browse…")
        btnBrowse.setToolTip("Select a data folder")
        btnBrowse.clicked.connect(self._onBrowse)
        pathLayout.addWidget(self.pathEdit)
        pathLayout.addWidget(btnBrowse)
        groupLayout.addLayout(pathLayout)

        # --- Action buttons ---
        btnLayout = qt.QHBoxLayout()

        self.btnLoad = qt.QPushButton("⚡ Load")
        self.btnLoad.setStyleSheet(
            "background-color: #17a2b8; color: white; "
            "font-weight: bold; padding: 8px;"
        )
        self.btnLoad.setToolTip("Load data from the folder above")
        self.btnLoad.clicked.connect(self._onLoad)
        btnLayout.addWidget(self.btnLoad)

        self.btnReload = qt.QPushButton("🔄 Reload")
        self.btnReload.setStyleSheet(
            "background-color: #6c757d; color: white; padding: 8px;"
        )
        self.btnReload.setToolTip("Reload the same data (e.g. after code changes)")
        self.btnReload.setEnabled(False)
        self.btnReload.clicked.connect(self._onReload)
        btnLayout.addWidget(self.btnReload)

        btnClear = qt.QPushButton("Clear History")
        btnClear.setStyleSheet("padding: 8px;")
        btnClear.setToolTip("Clear the list of recent paths")
        btnClear.clicked.connect(self._onClearHistory)
        btnLayout.addWidget(btnClear)

        groupLayout.addLayout(btnLayout)

        # --- Status ---
        self.lblStatus = qt.QLabel("")
        self.lblStatus.setStyleSheet("color: #666; font-style: italic;")
        self.lblStatus.setWordWrap(True)
        groupLayout.addWidget(self.lblStatus)

        outerLayout.addWidget(self.group)

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------

    def _settings(self):
        return qt.QSettings()

    def _restoreSettings(self):
        """Populate the combo and path edit from saved settings."""
        s = self._settings()
        recent = s.value(f"{_SETTINGS_PREFIX}/recentPaths", [])
        if isinstance(recent, str):
            recent = [recent] if recent else []
        self._setRecentPaths(recent)

        lastPath = s.value(f"{_SETTINGS_PREFIX}/lastPath", "")
        if lastPath:
            self.pathEdit.setText(lastPath)

    def _saveSettings(self):
        """Persist the recent list and current path."""
        s = self._settings()
        paths = [self.recentCombo.itemText(i) for i in range(self.recentCombo.count)]
        s.setValue(f"{_SETTINGS_PREFIX}/recentPaths", paths)
        s.setValue(f"{_SETTINGS_PREFIX}/lastPath", self.pathEdit.text.strip())

    def _setRecentPaths(self, paths):
        """Replace the combo items with *paths*."""
        self.recentCombo.blockSignals(True)
        self.recentCombo.clear()
        for p in paths:
            if p:
                self.recentCombo.addItem(p)
        self.recentCombo.blockSignals(False)

    def _addRecentPath(self, path):
        """Push *path* to the top of the recent list and persist."""
        paths = [self.recentCombo.itemText(i) for i in range(self.recentCombo.count)]
        # Remove duplicates then prepend
        paths = [p for p in paths if p != path]
        paths.insert(0, path)
        paths = paths[:_MAX_RECENT]
        self._setRecentPaths(paths)
        self.recentCombo.setCurrentIndex(0)
        self._saveSettings()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _onRecentSelected(self, index):
        if index >= 0:
            self.pathEdit.setText(self.recentCombo.itemText(index))

    def _onBrowse(self):
        startDir = self.pathEdit.text.strip() or ""
        folder = qt.QFileDialog.getExistingDirectory(
            self, "Select Subject Data Folder", startDir
        )
        if folder:
            self.pathEdit.setText(folder)

    def _onLoad(self):
        path = self.pathEdit.text.strip()
        if not path:
            self.lblStatus.setText("⚠ Enter or browse for a data folder first.")
            return
        if not os.path.isdir(path):
            self.lblStatus.setText(f"⚠ Folder not found: {path}")
            return
        self._addRecentPath(path)
        self.btnReload.setEnabled(True)
        self.lblStatus.setText(f"Loading from {path} …")
        slicer.app.processEvents()
        self.loadRequested.emit(path)

    def _onReload(self):
        path = self.pathEdit.text.strip()
        if not path or not os.path.isdir(path):
            self.lblStatus.setText("⚠ No valid folder to reload.")
            return
        self.lblStatus.setText(f"Reloading from {path} …")
        slicer.app.processEvents()
        self.loadRequested.emit(path)

    def _onClearHistory(self):
        self.recentCombo.clear()
        self._saveSettings()
        self.lblStatus.setText("History cleared.")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def setStatus(self, message: str):
        """Update the status label (called by the parent widget after load)."""
        self.lblStatus.setText(message)

    def setEnabled(self, enabled: bool):
        """Enable / disable action buttons during a load operation."""
        self.btnLoad.setEnabled(enabled)
        self.btnReload.setEnabled(enabled and bool(self.pathEdit.text.strip()))
