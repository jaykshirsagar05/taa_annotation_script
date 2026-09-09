"""
CaseBrowserWidget.py — offline case list for a dataset folder.

Lists every scan subdirectory under a chosen dataset root with its annotation
status, read from the checkpoint and output files already on disk. Replaces
the former Orthanc worklist; nothing here touches the network.
"""

import os
import qt

from TAAAnnotationLib import LocalDataset


SETTINGS_KEY_ROOT = "TAAAnnotation/DatasetRoot"

STATUS_COLORS = {
    LocalDataset.STATUS_NOT_STARTED: (150, 150, 150),
    LocalDataset.STATUS_IN_PROGRESS: (255, 143, 0),
    LocalDataset.STATUS_COMPLETE:    (40, 167, 69),
}


class CaseBrowserWidget(qt.QWidget):
    """Dataset-folder picker and case table."""

    caseSelected = qt.Signal(str)

    COL_CASE = 0
    COL_STATUS = 1
    COL_ZONES = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rootDir = ""
        self._cases = []
        self._currentCaseDir = ""
        self._setupUI()
        self._restoreLastRoot()

    def _setupUI(self):
        layout = qt.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        group = qt.QGroupBox("Dataset")
        groupLayout = qt.QVBoxLayout(group)

        folderLayout = qt.QHBoxLayout()
        self.btnChooseRoot = qt.QPushButton("📁 Choose Dataset Folder…")
        self.btnChooseRoot.setStyleSheet(
            "background-color: #17a2b8; color: white; font-weight: bold; padding: 8px;"
        )
        self.btnChooseRoot.clicked.connect(self.onChooseRoot)
        folderLayout.addWidget(self.btnChooseRoot)

        self.btnRefresh = qt.QPushButton("⟳ Refresh")
        self.btnRefresh.setToolTip("Re-read case status from disk")
        self.btnRefresh.setEnabled(False)
        self.btnRefresh.clicked.connect(self.refresh)
        folderLayout.addWidget(self.btnRefresh)
        groupLayout.addLayout(folderLayout)

        self.lblRoot = qt.QLabel("No dataset folder selected")
        self.lblRoot.setWordWrap(True)
        self.lblRoot.setStyleSheet("color: #666; font-style: italic;")
        groupLayout.addWidget(self.lblRoot)

        self.caseTable = qt.QTableWidget(0, 3)
        self.caseTable.setHorizontalHeaderLabels(["Case", "Status", "Landmarks"])
        self.caseTable.horizontalHeader().setStretchLastSection(True)
        self.caseTable.setSelectionBehavior(qt.QAbstractItemView.SelectRows)
        self.caseTable.setSelectionMode(qt.QAbstractItemView.SingleSelection)
        self.caseTable.setEditTriggers(qt.QAbstractItemView.NoEditTriggers)
        self.caseTable.setMaximumHeight(220)
        self.caseTable.verticalHeader().hide()
        self.caseTable.setToolTip("Double-click a case to load it.")
        self.caseTable.itemDoubleClicked.connect(self.onCaseDoubleClicked)
        self.caseTable.itemSelectionChanged.connect(self._updateLoadButton)
        groupLayout.addWidget(self.caseTable)

        self.btnLoadCase = qt.QPushButton("1. Load Selected Case")
        self.btnLoadCase.setStyleSheet(
            "background-color: #007bff; color: white; font-weight: bold; padding: 10px;"
        )
        self.btnLoadCase.setEnabled(False)
        self.btnLoadCase.clicked.connect(self.onLoadSelected)
        groupLayout.addWidget(self.btnLoadCase)

        self.lblSummary = qt.QLabel("")
        self.lblSummary.setStyleSheet("color: #666; margin-top: 4px;")
        groupLayout.addWidget(self.lblSummary)

        layout.addWidget(group)

    # --- Public API ---

    def setRootDir(self, path):
        """Point the browser at a dataset folder and list its cases."""
        if not path or not os.path.isdir(path):
            return False
        self.rootDir = path
        self.lblRoot.setText(path)
        self.lblRoot.setStyleSheet("color: #333;")
        self.btnRefresh.setEnabled(True)
        self._saveLastRoot(path)
        self.refresh()
        return True

    def refresh(self):
        """Re-read every case's status from disk and repopulate the table."""
        if not self.rootDir:
            return

        self._cases = LocalDataset.list_cases(self.rootDir)
        self.caseTable.setRowCount(len(self._cases))

        for row, case in enumerate(self._cases):
            nameItem = qt.QTableWidgetItem(case["case_id"])
            nameItem.setToolTip(case["path"])
            if case["path"] == self._currentCaseDir:
                font = nameItem.font()
                font.setBold(True)
                nameItem.setFont(font)
            self.caseTable.setItem(row, self.COL_CASE, nameItem)

            statusItem = qt.QTableWidgetItem(
                LocalDataset.STATUS_LABELS[case["status"]])
            statusItem.setForeground(qt.QColor(*STATUS_COLORS[case["status"]]))
            self.caseTable.setItem(row, self.COL_STATUS, statusItem)

            zones = case["zone_count"]
            zonesItem = qt.QTableWidgetItem(f"{zones}/10" if zones else "—")
            self.caseTable.setItem(row, self.COL_ZONES, zonesItem)

        self.caseTable.resizeColumnsToContents()
        self._updateSummary()
        self._updateLoadButton()

    def setCurrentCase(self, caseDir):
        """Mark a case as the one currently loaded and refresh its status."""
        self._currentCaseDir = caseDir or ""
        self.refresh()

    def selectedCaseDir(self):
        """Return the highlighted case's directory, or "" when none is selected."""
        row = self.caseTable.currentRow()
        if 0 <= row < len(self._cases):
            return self._cases[row]["path"]
        return ""

    def setEnabledState(self, enabled):
        """Enable or disable the controls while a load is in flight."""
        self.btnChooseRoot.setEnabled(enabled)
        self.btnRefresh.setEnabled(enabled and bool(self.rootDir))
        self.caseTable.setEnabled(enabled)
        self.btnLoadCase.setEnabled(enabled and bool(self.selectedCaseDir()))

    # --- Handlers ---

    def onChooseRoot(self):
        path = qt.QFileDialog.getExistingDirectory(
            self, "Select Dataset Folder", self.rootDir or "")
        if not path:
            return
        if not self.setRootDir(path):
            return
        if not self._cases:
            self.lblSummary.setText(
                "No cases found — expected subfolders each holding a "
                "'_ct' and a '_mask' NIfTI file."
            )

    def onCaseDoubleClicked(self, item):
        row = item.row()
        if 0 <= row < len(self._cases):
            self.caseSelected.emit(self._cases[row]["path"])

    def onLoadSelected(self):
        caseDir = self.selectedCaseDir()
        if caseDir:
            self.caseSelected.emit(caseDir)

    # --- Internal helpers ---

    def _updateLoadButton(self):
        self.btnLoadCase.setEnabled(bool(self.selectedCaseDir()))

    def _updateSummary(self):
        if not self._cases:
            self.lblSummary.setText("No cases found in this folder.")
            return
        complete = sum(1 for c in self._cases
                       if c["status"] == LocalDataset.STATUS_COMPLETE)
        inProgress = sum(1 for c in self._cases
                         if c["status"] == LocalDataset.STATUS_IN_PROGRESS)
        self.lblSummary.setText(
            f"{len(self._cases)} case(s) — {complete} complete, "
            f"{inProgress} in progress"
        )

    def _restoreLastRoot(self):
        path = qt.QSettings().value(SETTINGS_KEY_ROOT, "")
        if path and os.path.isdir(path):
            self.setRootDir(path)

    @staticmethod
    def _saveLastRoot(path):
        try:
            qt.QSettings().setValue(SETTINGS_KEY_ROOT, path)
        except Exception as e:
            print(f"[CaseBrowser] Could not persist dataset folder: {e}")
