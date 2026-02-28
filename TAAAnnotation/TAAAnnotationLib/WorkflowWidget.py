"""
WorkflowWidget.py - Annotation workflow UI component.

Contains the step-by-step annotation workflow:
- Phase 1: Load Data
- Phase 2: Refine Mask
- Phase 3: Extract Centerline (VMTK)
- Phase 4: Zone Landmarks
- Phase 5: Export
"""

import qt
import slicer


class WorkflowWidget(qt.QWidget):
    """Widget containing the annotation workflow steps."""
    
    # Signals
    loadDataRequested = qt.Signal()  # Emitted when manual load is clicked
    refineRequested = qt.Signal()
    vmtkRequested = qt.Signal()
    exportRequested = qt.Signal()
    quickSaveRequested = qt.Signal()
    notesChanged = qt.Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.centerlinePicker = None
        self.logic = None
        self.orthancTempDir = ""  # Temporary directory for Orthanc studies
        self._activeProfile = None  # DatasetProfile for the loaded study
        self._setupUI()
        
    def setLogic(self, logic):
        """Set the logic instance."""
        self.logic = logic
        
    def setCenterlinePicker(self, picker):
        """Set the centerline picker instance."""
        self.centerlinePicker = picker
        
    def _setupUI(self):
        layout = qt.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Styles
        self.defaultStyle = "text-align: left; padding: 8px; font-size: 12px;"
        self.doneStyle = "background-color: #28a745; color: white; text-align: left; padding: 8px; font-weight: bold;"
        self.activeStyle = "background-color: #fff3cd; border: 2px solid #ffc107; text-align: left; padding: 8px;"
        
        # --- Recovery Banner (hidden by default) ---
        self.recoveryBanner = qt.QFrame()
        self.recoveryBanner.setStyleSheet("background-color: #ffc107; padding: 10px; border-radius: 5px;")
        recoveryLayout = qt.QHBoxLayout(self.recoveryBanner)
        self.recoveryLabel = qt.QLabel("⚠ Previous session detected")
        self.btnRecover = qt.QPushButton("Recover")
        self.btnIgnore = qt.QPushButton("Start Fresh")
        recoveryLayout.addWidget(self.recoveryLabel)
        recoveryLayout.addWidget(self.btnRecover)
        recoveryLayout.addWidget(self.btnIgnore)
        self.recoveryBanner.hide()
        layout.addWidget(self.recoveryBanner)
        
        # --- Phase 1: Load ---
        self.btnLoad = qt.QPushButton("1. Load Data & Initialize")
        self.btnLoad.setStyleSheet(self.defaultStyle)
        self.btnLoad.clicked.connect(lambda: self.loadDataRequested.emit())
        layout.addWidget(self.btnLoad)
        
        # --- Phase 2: Refine ---
        self.btnSeg = qt.QPushButton("2. Refine Mask")
        self.btnSeg.setStyleSheet(self.defaultStyle)
        self.btnSeg.setEnabled(False)
        self.btnSeg.clicked.connect(lambda: self.refineRequested.emit())
        layout.addWidget(self.btnSeg)
        
        # --- Phase 3: VMTK ---
        self.btnVmtk = qt.QPushButton("3. Extract VMTK Centerline")
        self.btnVmtk.setStyleSheet(self.defaultStyle)
        self.btnVmtk.setEnabled(False)
        self.btnVmtk.clicked.connect(lambda: self.vmtkRequested.emit())
        layout.addWidget(self.btnVmtk)
        
        # --- Phase 4: Zones ---
        self._createZonesGroup(layout)
        
        # --- Quick Save ---
        self.btnQuickSave = qt.QPushButton("💾 Quick Save Progress")
        self.btnQuickSave.setStyleSheet("background-color: #6c757d; color: white; padding: 8px;")
        self.btnQuickSave.setEnabled(False)
        self.btnQuickSave.clicked.connect(lambda: self.quickSaveRequested.emit())
        layout.addWidget(self.btnQuickSave)
        
        # --- Phase 5: Export ---
        line = qt.QFrame()
        line.setFrameShape(qt.QFrame.HLine)
        layout.addWidget(line)
        
        self.btnExport = qt.QPushButton("5. Export & Reset")
        self.btnExport.setStyleSheet("text-align: center; padding: 10px; font-weight: bold; background-color: #007bff; color: white;")
        self.btnExport.setEnabled(False)
        self.btnExport.clicked.connect(lambda: self.exportRequested.emit())
        layout.addWidget(self.btnExport)
        
        # --- Current ID Label ---
        self.currentIdLabel = qt.QLabel("")
        self.currentIdLabel.setStyleSheet("color: #6f42c1; font-weight: bold; margin-top: 5px;")
        layout.addWidget(self.currentIdLabel)
        
        # --- Status ---
        self.lblStatus = qt.QLabel("Status: Ready for Scan")
        self.lblStatus.setWordWrap(True)
        self.lblStatus.setStyleSheet("color: #666; margin-top: 10px;")
        layout.addWidget(self.lblStatus)
        
        # --- Notes ---
        notesLabel = qt.QLabel("Annotation Notes (saved with export)")
        notesLabel.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(notesLabel)
        
        self.notesEdit = qt.QPlainTextEdit()
        self.notesEdit.setPlaceholderText("Add observations about scan quality, artifacts, decisions...")
        self.notesEdit.setMaximumHeight(100)
        self.notesEdit.textChanged.connect(lambda: self.notesChanged.emit(self.notesEdit.toPlainText()))
        layout.addWidget(self.notesEdit)
        
    def _createZonesGroup(self, parentLayout):
        """Create the SVS/STS zone landmark picking group."""
        from TAAAnnotationLib.CenterlinePicker import SVS_STS_LANDMARKS

        self.groupZones = qt.QGroupBox("4. SVS/STS Zonal Landmarks")
        zonesLayout = qt.QVBoxLayout(self.groupZones)

        # --- Centerline selector ---
        clLayout = qt.QHBoxLayout()
        clLabel = qt.QLabel("Centerline:")
        self.centerlineCombo = slicer.qMRMLNodeComboBox()
        self.centerlineCombo.nodeTypes = ["vtkMRMLModelNode"]
        self.centerlineCombo.selectNodeUponCreation = False
        self.centerlineCombo.addEnabled = False
        self.centerlineCombo.removeEnabled = False
        self.centerlineCombo.noneEnabled = True
        self.centerlineCombo.showHidden = False
        self.centerlineCombo.setMRMLScene(slicer.mrmlScene)
        self.centerlineCombo.setToolTip("Select the centerline model")
        clLayout.addWidget(clLabel)
        clLayout.addWidget(self.centerlineCombo)
        zonesLayout.addLayout(clLayout)

        # --- Zone selector dropdown ---
        zoneSelLayout = qt.QHBoxLayout()
        zoneSelLabel = qt.QLabel("Placing:")
        zoneSelLabel.setStyleSheet("font-weight: bold;")
        self.zoneSelector = qt.QComboBox()
        for lm in SVS_STS_LANDMARKS:
            self.zoneSelector.addItem(
                f"{lm['id']}: {lm['label']}  \u2014  {lm['name']}"
            )
        self.zoneSelector.currentIndexChanged.connect(self._onZoneSelectionChanged)
        zoneSelLayout.addWidget(zoneSelLabel)
        zoneSelLayout.addWidget(self.zoneSelector)
        zonesLayout.addLayout(zoneSelLayout)

        # --- Anatomical instruction panel ---
        self.lblInstruction = qt.QLabel()
        self.lblInstruction.setWordWrap(True)
        self.lblInstruction.setStyleSheet(
            "background-color: #e8f4fd; border: 1px solid #b8daff; "
            "border-radius: 4px; padding: 8px; font-size: 12px; color: #004085;"
        )
        self._updateInstructionLabel(0)
        zonesLayout.addWidget(self.lblInstruction)

        # --- Picker / Confirm / Undo buttons ---
        btnLayout = qt.QHBoxLayout()
        self.btnClickMode = qt.QPushButton("\U0001f3af Start Zone Picker")
        self.btnClickMode.setStyleSheet(
            "background-color: #17a2b8; color: white; font-weight: bold; padding: 10px;"
        )
        self.btnClickMode.setEnabled(False)
        self.btnClickMode.clicked.connect(self.onToggleClickMode)
        btnLayout.addWidget(self.btnClickMode)

        self.btnConfirmPoint = qt.QPushButton("\u2705 Confirm")
        self.btnConfirmPoint.setStyleSheet(
            "background-color: #28a745; color: white; font-weight: bold; padding: 10px;"
        )
        self.btnConfirmPoint.setEnabled(False)
        self.btnConfirmPoint.clicked.connect(self.onConfirmZone)
        btnLayout.addWidget(self.btnConfirmPoint)

        self.btnUndoPoint = qt.QPushButton("\u21a9 Undo")
        self.btnUndoPoint.setStyleSheet("padding: 10px;")
        self.btnUndoPoint.setEnabled(False)
        self.btnUndoPoint.clicked.connect(self.onUndoZone)
        btnLayout.addWidget(self.btnUndoPoint)
        zonesLayout.addLayout(btnLayout)

        # --- Picker status ---
        self.lblClickStatus = qt.QLabel("Picker: OFF")
        self.lblClickStatus.setStyleSheet("color: #666; font-style: italic;")
        zonesLayout.addWidget(self.lblClickStatus)

        # --- Progress counter ---
        self.lblZoneCount = qt.QLabel("Landmarks: 0 / 10")
        self.lblZoneCount.setStyleSheet("font-weight: bold; color: #17a2b8;")
        zonesLayout.addWidget(self.lblZoneCount)

        # --- Zone progress table ---
        self.zoneTable = qt.QTableWidget(10, 3)
        self.zoneTable.setHorizontalHeaderLabels(["Landmark", "Status", "Position"])
        self.zoneTable.horizontalHeader().setStretchLastSection(True)
        self.zoneTable.setSelectionBehavior(qt.QAbstractItemView.SelectRows)
        self.zoneTable.setSelectionMode(qt.QAbstractItemView.SingleSelection)
        self.zoneTable.setEditTriggers(qt.QAbstractItemView.NoEditTriggers)
        self.zoneTable.setMaximumHeight(260)
        self.zoneTable.verticalHeader().hide()
        self.zoneTable.setToolTip(
            "Double-click a row to jump to that landmark.\n"
            "Right-click for delete / re-place options."
        )
        self.zoneTable.itemDoubleClicked.connect(self._onJumpToZoneFromTable)
        self.zoneTable.setContextMenuPolicy(qt.Qt.CustomContextMenu)
        self.zoneTable.customContextMenuRequested.connect(self._onZoneTableContextMenu)
        self.zoneTable.currentCellChanged.connect(self._onZoneTableRowChanged)

        # Populate skeleton rows
        for i, lm in enumerate(SVS_STS_LANDMARKS):
            # Column 0 — Landmark abbreviation (coloured + bold)
            nameItem = qt.QTableWidgetItem(f"{lm['label']}  ({lm['name']})")
            nameItem.setToolTip(f"{lm['name']}\n{lm['description']}")
            c = lm["color"]
            nameItem.setForeground(
                qt.QColor(int(c[0] * 255), int(c[1] * 255), int(c[2] * 255))
            )
            font = nameItem.font()
            font.setBold(True)
            nameItem.setFont(font)
            self.zoneTable.setItem(i, 0, nameItem)

            # Column 1 — Status
            statusItem = qt.QTableWidgetItem("\u25cb Not placed")
            statusItem.setForeground(qt.QColor(150, 150, 150))
            self.zoneTable.setItem(i, 1, statusItem)

            # Column 2 — Coordinates
            self.zoneTable.setItem(i, 2, qt.QTableWidgetItem("\u2014"))

        self.zoneTable.resizeColumnsToContents()
        zonesLayout.addWidget(self.zoneTable)

        parentLayout.addWidget(self.groupZones)

    # --- Zone instruction helpers ---

    def _updateInstructionLabel(self, zoneIndex):
        """Update the anatomical instruction for the selected zone."""
        from TAAAnnotationLib.CenterlinePicker import SVS_STS_LANDMARKS
        if 0 <= zoneIndex < len(SVS_STS_LANDMARKS):
            lm = SVS_STS_LANDMARKS[zoneIndex]
            zone_text = f"  \u2192  <i>{lm['zone_after']}</i>" if lm.get("zone_after") else ""
            self.lblInstruction.setText(
                f"<b>\U0001f4cd {lm['name']}</b>{zone_text}<br>"
                f"<span style='color:#555;'>{lm['description']}</span>"
            )
        else:
            self.lblInstruction.setText("")

    def _onZoneSelectionChanged(self, index):
        """Zone selector dropdown changed."""
        self._updateInstructionLabel(index)
        if self.centerlinePicker:
            self.centerlinePicker.setSelectedZone(index)

    def _onZoneTableRowChanged(self, row, col, prevRow, prevCol):
        """Clicking a table row auto-selects that zone in the dropdown."""
        if 0 <= row < 10:
            self.zoneSelector.setCurrentIndex(row)
        
    # --- Public Methods ---
    
    def setButtonsEnabled(self, enabled: bool):
        """Enable/disable all workflow buttons to prevent double-clicks during operations."""
        self.btnLoad.setEnabled(enabled)
        self.btnSeg.setEnabled(enabled)
        self.btnVmtk.setEnabled(enabled)
        self.btnExport.setEnabled(enabled)
        self.btnQuickSave.setEnabled(enabled)
        self.btnClickMode.setEnabled(enabled)
        slicer.app.processEvents()
    
    def updateUIState(self, phase: int, orthancMode: bool = False):
        """Update UI based on workflow phase and active profile."""
        centerlinePreloaded = (
            self._activeProfile is not None
            and self._activeProfile.has_precalculated_centerline
        )
        # Centerline is considered ready if VMTK phase was passed OR it was pre-loaded
        centerlineReady = (phase >= 3) or centerlinePreloaded

        self.btnSeg.setEnabled(phase >= 1)

        if centerlinePreloaded:
            # VMTK not needed — keep button disabled (already marked done)
            self.btnVmtk.setEnabled(False)
        else:
            self.btnVmtk.setEnabled(phase >= 2)

        self.btnClickMode.setEnabled(centerlineReady)
        self.btnQuickSave.setEnabled(phase >= 1)
        
        # Export only enabled for local workflow, not Orthanc
        if orthancMode:
            self.btnExport.setEnabled(False)
        else:
            self.btnExport.setEnabled(centerlineReady)
        
    def setCurrentId(self, current_id: str, source: str = ""):
        """Set the current study ID display."""
        if source:
            self.currentIdLabel.setText(f"Current: {current_id} ({source})")
        else:
            self.currentIdLabel.setText(f"Current: {current_id}" if current_id else "")

    def setProfile(self, profile):
        """Set the active dataset profile and adjust UI accordingly."""
        self._activeProfile = profile
        if profile is None:
            return

        if profile.has_precalculated_centerline:
            self.btnVmtk.setText("3. Centerline (Pre-loaded ✔)")
            self.btnVmtk.setStyleSheet(
                "background-color: #28a745; color: white; text-align: left; "
                "padding: 8px; font-weight: bold;"
            )
            self.btnVmtk.setEnabled(False)
            self.btnVmtk.setToolTip(
                "Centerline was provided with the dataset — "
                "VMTK extraction is not needed."
            )
        else:
            self.btnVmtk.setText("3. Extract VMTK Centerline")
            self.btnVmtk.setStyleSheet(self.defaultStyle)
            self.btnVmtk.setToolTip("")
            
    def setStatus(self, message: str):
        """Set status message."""
        self.lblStatus.setText(message)
        
    def markDone(self, phase: int, text: str):
        """Mark a phase button as done."""
        button_map = {
            1: self.btnLoad,
            2: self.btnSeg,
            3: self.btnVmtk,
        }
        button = button_map.get(phase)
        if button:
            button.setStyleSheet(self.doneStyle)
            button.setText(f"✔ {text}")
            
    def showRecoveryBanner(self, message: str):
        """Show recovery banner."""
        self.recoveryLabel.setText(message)
        self.recoveryBanner.show()
        
    def hideRecoveryBanner(self):
        """Hide recovery banner."""
        self.recoveryBanner.hide()
        
    def getNotesText(self) -> str:
        """Get the notes text."""
        return self.notesEdit.toPlainText().strip()
        
    def resetUI(self):
        """Reset UI to initial state."""
        from TAAAnnotationLib.CenterlinePicker import SVS_STS_LANDMARKS

        self._activeProfile = None

        self.btnLoad.setStyleSheet(self.defaultStyle)
        self.btnLoad.setText("1. Load Data & Initialize")
        self.btnSeg.setStyleSheet(self.defaultStyle)
        self.btnSeg.setText("2. Refine Mask")
        self.btnVmtk.setStyleSheet(self.defaultStyle)
        self.btnVmtk.setText("3. Extract VMTK Centerline")
        self.btnVmtk.setToolTip("")
        self.btnVmtk.setEnabled(False)
        self._setPickerButtonOff()
        self.btnConfirmPoint.setEnabled(False)
        self.centerlineCombo.setCurrentNode(None)
        self.centerlineCombo.setMRMLScene(slicer.mrmlScene)

        # Reset zone table
        for i in range(len(SVS_STS_LANDMARKS)):
            self.zoneTable.item(i, 1).setText("\u25cb Not placed")
            self.zoneTable.item(i, 1).setForeground(qt.QColor(150, 150, 150))
            self.zoneTable.item(i, 2).setText("\u2014")

        self.zoneSelector.setCurrentIndex(0)
        self._updateInstructionLabel(0)
        self.lblZoneCount.setText("Landmarks: 0 / 10")
        self.lblZoneCount.setStyleSheet("font-weight: bold; color: #17a2b8;")
        self.lblClickStatus.setText("Picker: OFF")
        self.lblClickStatus.setStyleSheet("color: #666; font-style: italic;")
        self.notesEdit.setPlainText("")
        self.currentIdLabel.setText("")
        self.lblStatus.setText("Status: Ready for Scan")
        
    def updateZoneUI(self):
        """Update the zone progress table and counters."""
        from TAAAnnotationLib.CenterlinePicker import SVS_STS_LANDMARKS

        if not self.logic or not self.logic.zoneNode:
            return

        totalCPs = self.logic.zoneNode.GetNumberOfControlPoints()
        self.btnUndoPoint.setEnabled(totalCPs > 0)

        # Build label -> position map for quick lookup
        labelPosMap = {}
        for i in range(totalCPs):
            label = self.logic.zoneNode.GetNthControlPointLabel(i)
            pos = [0, 0, 0]
            self.logic.zoneNode.GetNthControlPointPosition(i, pos)
            labelPosMap[label] = pos

        # Update table rows
        placedCount = 0
        for idx, lm in enumerate(SVS_STS_LANDMARKS):
            if lm["label"] in labelPosMap:
                pos = labelPosMap[lm["label"]]
                self.zoneTable.item(idx, 1).setText("\u2713 Placed")
                self.zoneTable.item(idx, 1).setForeground(qt.QColor(40, 167, 69))
                self.zoneTable.item(idx, 2).setText(
                    f"({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f})"
                )
                placedCount += 1
            else:
                self.zoneTable.item(idx, 1).setText("\u25cb Not placed")
                self.zoneTable.item(idx, 1).setForeground(qt.QColor(150, 150, 150))
                self.zoneTable.item(idx, 2).setText("\u2014")

        # Progress counter
        self.lblZoneCount.setText(f"Landmarks: {placedCount} / 10")

        if placedCount >= 10:
            self.lblZoneCount.setStyleSheet("font-weight: bold; color: #28a745;")
            self.lblZoneCount.setText("\u2713 All 10 landmarks placed!")
            if self.centerlinePicker and self.centerlinePicker.isActive:
                self.centerlinePicker.disable()
                self._setPickerButtonOff()
                self.lblClickStatus.setText("Picker: OFF \u2014 All zones complete")
                self.lblClickStatus.setStyleSheet("color: #28a745; font-weight: bold;")
        else:
            self.lblZoneCount.setStyleSheet("font-weight: bold; color: #17a2b8;")

        # Enable Confirm button if there is a preview position
        if (
            self.centerlinePicker
            and self.centerlinePicker.currentPreviewPos is not None
        ):
            self.btnConfirmPoint.setEnabled(True)
        else:
            self.btnConfirmPoint.setEnabled(False)
    
    # --- Zone Event Handlers ---
    
    # --- Picker button helpers ---

    def _setPickerButtonOn(self):
        self.btnClickMode.setText("\u23f8 Stop Picker")
        self.btnClickMode.setStyleSheet(
            "background-color: #dc3545; color: white; font-weight: bold; padding: 10px;"
        )

    def _setPickerButtonOff(self):
        self.btnClickMode.setText("\U0001f3af Start Zone Picker")
        self.btnClickMode.setStyleSheet(
            "background-color: #17a2b8; color: white; font-weight: bold; padding: 10px;"
        )

    # --- Zone Event Handlers ---

    def onToggleClickMode(self):
        """Toggle zone picker mode."""
        if not self.centerlinePicker:
            slicer.util.errorDisplay("Centerline picker not initialized")
            return

        try:
            centerlineNode = self.centerlineCombo.currentNode()
            placedCount = (
                self.centerlinePicker.getPlacedCount()
                if self.centerlinePicker
                else 0
            )

            if placedCount >= 10:
                slicer.util.warningDisplay(
                    "All 10 zone landmarks have been placed.\n"
                    "Use Undo or right-click a row to modify."
                )
                return

            result = self.centerlinePicker.toggleClickMode(centerlineNode)
            if result:
                from TAAAnnotationLib.CenterlinePicker import SVS_STS_LANDMARKS

                if self.centerlinePicker.isActive:
                    self._setPickerButtonOn()
                    zIdx = self.zoneSelector.currentIndex
                    lm = SVS_STS_LANDMARKS[zIdx]
                    self.lblClickStatus.setText(
                        f"Picker: ACTIVE \u2014 Click centerline to place {lm['label']} ({lm['name']})"
                    )
                    self.lblClickStatus.setStyleSheet(
                        "color: #28a745; font-weight: bold;"
                    )
                else:
                    self._setPickerButtonOff()
                    self.lblClickStatus.setText("Picker: OFF")
                    self.lblClickStatus.setStyleSheet(
                        "color: #666; font-style: italic;"
                    )
        except Exception as e:
            slicer.util.errorDisplay(f"Error toggling click mode: {str(e)}")
            import traceback
            traceback.print_exc()

    def onConfirmZone(self):
        """Confirm the current zone point using the zone selector index."""
        if not self.logic or not self.centerlinePicker:
            return

        from TAAAnnotationLib.CenterlinePicker import SVS_STS_LANDMARKS

        zoneIndex = self.zoneSelector.currentIndex
        label = self.centerlinePicker.confirmZonePoint(zoneIndex)

        if label:
            self.updateZoneUI()
            self.btnConfirmPoint.setEnabled(False)

            # Auto-advance to next unplaced zone
            nextIdx = self.centerlinePicker.getNextUnplacedZone()
            if nextIdx >= 0:
                self.zoneSelector.setCurrentIndex(nextIdx)
                lm = SVS_STS_LANDMARKS[nextIdx]
                self.lblStatus.setText(
                    f"\u2713 {label} confirmed.  Next: {lm['label']} ({lm['name']})"
                )
            else:
                self.lblStatus.setText("\u2713 All 10 zone landmarks complete!")

    def onUndoZone(self):
        """Undo last zone point."""
        if not self.logic or not self.logic.zoneNode:
            return
        n = self.logic.zoneNode.GetNumberOfControlPoints()
        if n > 0:
            label = self.logic.zoneNode.GetNthControlPointLabel(n - 1)
            self.logic.undoLastZonePoint()
            if self.centerlinePicker:
                self.centerlinePicker.onLandmarkRemoved(label)
            self.updateZoneUI()

    def _onJumpToZoneFromTable(self, item):
        """Jump to a zone landmark on double-click in the progress table."""
        if not self.logic or not self.logic.zoneNode:
            return
        from TAAAnnotationLib.CenterlinePicker import SVS_STS_LANDMARKS

        row = item.row()
        if row < 0 or row >= len(SVS_STS_LANDMARKS):
            return
        lm = SVS_STS_LANDMARKS[row]
        for i in range(self.logic.zoneNode.GetNumberOfControlPoints()):
            if self.logic.zoneNode.GetNthControlPointLabel(i) == lm["label"]:
                self.logic.jumpToZonePoint(i)
                return

    def _onZoneTableContextMenu(self, position):
        """Right-click menu on zone table: jump / delete / re-place."""
        from TAAAnnotationLib.CenterlinePicker import SVS_STS_LANDMARKS

        row = self.zoneTable.rowAt(position.y())
        if row < 0 or row >= len(SVS_STS_LANDMARKS) or not self.logic:
            return

        lm = SVS_STS_LANDMARKS[row]

        # Find existing control point for this landmark
        cpIndex = -1
        if self.logic.zoneNode:
            for i in range(self.logic.zoneNode.GetNumberOfControlPoints()):
                if self.logic.zoneNode.GetNthControlPointLabel(i) == lm["label"]:
                    cpIndex = i
                    break

        menu = qt.QMenu()
        jumpAction = None
        deleteAction = None
        if cpIndex >= 0:
            jumpAction = menu.addAction(f"Jump to {lm['label']}")
            deleteAction = menu.addAction(f"Delete {lm['label']}")
            menu.addSeparator()
        replaceAction = menu.addAction(f"Place {lm['label']} next")

        action = menu.exec_(self.zoneTable.viewport().mapToGlobal(position))
        if action is None:
            return

        if action == jumpAction and cpIndex >= 0:
            self.logic.jumpToZonePoint(cpIndex)
        elif action == deleteAction and cpIndex >= 0:
            removedLabel = self.logic.zoneNode.GetNthControlPointLabel(cpIndex)
            self.logic.deleteZonePoint(cpIndex)
            if self.centerlinePicker:
                self.centerlinePicker.onLandmarkRemoved(removedLabel)
            self.updateZoneUI()
        elif action == replaceAction:
            self.zoneSelector.setCurrentIndex(row)
    
    def setOrthancMode(self, enabled: bool):
        """Enable/disable Orthanc mode - disables local export when using Orthanc."""
        if enabled:
            self.btnExport.setEnabled(False)
            self.btnExport.setToolTip("Using Orthanc workflow - use 'Submit to Orthanc' instead")
            self.btnExport.setStyleSheet("background-color: #6c757d; color: white; padding: 10px;")
            self.btnExport.setText("5. Export & Reset (Disabled - Use Orthanc)")
        else:
            self.btnExport.setToolTip("")
            self.btnExport.setStyleSheet("text-align: center; padding: 10px; font-weight: bold; background-color: #007bff; color: white;")
            self.btnExport.setText("5. Export & Reset")