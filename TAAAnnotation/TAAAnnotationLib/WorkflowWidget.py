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
        """Create the zones picking group."""
        self.groupZones = qt.QGroupBox("4. Zonal Landmarks (Preview & Confirm)")
        zonesLayout = qt.QVBoxLayout(self.groupZones)
        
        # Centerline selector
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
        
        # Picker buttons
        btnLayout = qt.QHBoxLayout()
        self.btnClickMode = qt.QPushButton("🎯 Start Zone Picker")
        self.btnClickMode.setStyleSheet("background-color: #17a2b8; color: white; font-weight: bold; padding: 10px;")
        self.btnClickMode.setEnabled(False)
        self.btnClickMode.clicked.connect(self.onToggleClickMode)
        btnLayout.addWidget(self.btnClickMode)
        
        self.btnConfirmPoint = qt.QPushButton("✅ Confirm Zone")
        self.btnConfirmPoint.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 10px;")
        self.btnConfirmPoint.setEnabled(False)
        self.btnConfirmPoint.clicked.connect(self.onConfirmZone)
        btnLayout.addWidget(self.btnConfirmPoint)
        zonesLayout.addLayout(btnLayout)
        
        # Status labels
        self.lblClickStatus = qt.QLabel("Picker: OFF")
        self.lblClickStatus.setStyleSheet("color: #666; font-style: italic;")
        zonesLayout.addWidget(self.lblClickStatus)
        
        # Current zone picker instruction
        self.lblCurrentZone = qt.QLabel("Ready to pick: Zone 0 start")
        self.lblCurrentZone.setStyleSheet("font-weight: bold; color: #ff6b6b; font-size: 13px; background-color: #fff3cd; padding: 8px; border-radius: 4px;")
        zonesLayout.addWidget(self.lblCurrentZone)
        
        self.btnUndoPoint = qt.QPushButton("↩ Undo Last Point")
        self.btnUndoPoint.setEnabled(False)
        self.btnUndoPoint.clicked.connect(self.onUndoZone)
        zonesLayout.addWidget(self.btnUndoPoint)
        
        self.lblZoneCount = qt.QLabel("Zone Points: 0 / 10")
        self.lblZoneCount.setStyleSheet("font-weight: bold; color: #17a2b8;")
        zonesLayout.addWidget(self.lblZoneCount)
        
        # Zone list
        self.zoneList = qt.QListWidget()
        self.zoneList.setMaximumHeight(120)
        self.zoneList.setToolTip("Double-click to jump, right-click to delete")
        self.zoneList.setContextMenuPolicy(qt.Qt.CustomContextMenu)
        self.zoneList.itemDoubleClicked.connect(self.onJumpToZone)
        self.zoneList.customContextMenuRequested.connect(self.onZoneContextMenu)
        zonesLayout.addWidget(self.zoneList)
        
        parentLayout.addWidget(self.groupZones)
        
    # --- Public Methods ---
    
    def updateUIState(self, phase: int):
        """Update UI based on workflow phase."""
        self.btnSeg.setEnabled(phase >= 1)
        self.btnVmtk.setEnabled(phase >= 2)
        self.btnClickMode.setEnabled(phase >= 3)
        self.btnQuickSave.setEnabled(phase >= 1)
        self.btnExport.setEnabled(phase >= 3)
        
    def setCurrentId(self, current_id: str, source: str = ""):
        """Set the current study ID display."""
        if source:
            self.currentIdLabel.setText(f"Current: {current_id} ({source})")
        else:
            self.currentIdLabel.setText(f"Current: {current_id}" if current_id else "")
            
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
        self.btnLoad.setStyleSheet(self.defaultStyle)
        self.btnLoad.setText("1. Load Data & Initialize")
        self.btnSeg.setStyleSheet(self.defaultStyle)
        self.btnSeg.setText("2. Refine Mask")
        self.btnVmtk.setStyleSheet(self.defaultStyle)
        self.btnVmtk.setText("3. Extract VMTK Centerline")
        self.btnClickMode.setText("🎯 Start Zone Picker")
        self.btnClickMode.setStyleSheet("background-color: #17a2b8; color: white; font-weight: bold; padding: 10px;")
        self.btnConfirmPoint.setEnabled(False)
        self.centerlineCombo.setCurrentNode(None)
        self.centerlineCombo.setMRMLScene(slicer.mrmlScene)
        
        self.zoneList.clear()
        self.lblZoneCount.setText("Zone Points: 0 / 10")
        self.lblCurrentZone.setText("Ready to pick: Zone 0 start")
        self.lblCurrentZone.setStyleSheet("font-weight: bold; color: #ff6b6b; font-size: 13px; background-color: #fff3cd; padding: 8px; border-radius: 4px;")
        self.lblClickStatus.setText("Picker: OFF")
        self.lblClickStatus.setStyleSheet("color: #666; font-style: italic;")
        self.notesEdit.setPlainText("")
        self.currentIdLabel.setText("")
        self.lblStatus.setText("Status: Ready for Scan")
        
    def updateZoneUI(self):
        """Update zone-related UI elements."""
        if not self.logic or not self.logic.zoneNode:
            return
            
        count = self.logic.zoneNode.GetNumberOfControlPoints()
        self.lblZoneCount.setText(f"Zone Points: {count} / 10")
        self.btnUndoPoint.setEnabled(count > 0)
        
        # Update current zone instruction
        if count < 10:
            self.lblCurrentZone.setText(f"Ready to pick: Zone {count} start")
            self.lblCurrentZone.setStyleSheet("font-weight: bold; color: #ff6b6b; font-size: 13px; background-color: #fff3cd; padding: 8px; border-radius: 4px;")
        else:
            self.lblCurrentZone.setText("✓ All 10 zones picked!")
            self.lblCurrentZone.setStyleSheet("font-weight: bold; color: #28a745; font-size: 13px; background-color: #d4edda; padding: 8px; border-radius: 4px;")
            # Disable picker when all zones are picked
            if self.centerlinePicker and self.centerlinePicker.isActive:
                self.centerlinePicker.disable()
                self.btnClickMode.setText("🎯 Start Zone Picker")
                self.btnClickMode.setStyleSheet("background-color: #17a2b8; color: white; font-weight: bold; padding: 10px;")
                self.lblClickStatus.setText("Picker: OFF - All zones complete")
                self.lblClickStatus.setStyleSheet("color: #28a745; font-weight: bold;")
        
        # Update list
        self.zoneList.clear()
        for i in range(count):
            label = self.logic.zoneNode.GetNthControlPointLabel(i)
            pos = [0, 0, 0]
            self.logic.zoneNode.GetNthControlPointPosition(i, pos)
            self.zoneList.addItem(f"{label}: ({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f})")
    
        # Enable Confirm button if there's a preview point AND we haven't picked all zones yet
        if self.centerlinePicker and self.centerlinePicker.currentPreviewPos is not None and count < 10:
            self.btnConfirmPoint.setEnabled(True)
        else:
            self.btnConfirmPoint.setEnabled(False)
    
    # --- Zone Event Handlers ---
    
    def onToggleClickMode(self):
        """Toggle zone picker mode."""
        if not self.centerlinePicker:
            slicer.util.errorDisplay("Centerline picker not initialized")
            return
            
        try:
            centerlineNode = self.centerlineCombo.currentNode()
            currentCount = self.logic.zoneNode.GetNumberOfControlPoints() if self.logic and self.logic.zoneNode else 0
            
            if currentCount >= 10:
                slicer.util.warningDisplay("All 10 zone landmarks have been picked. Use Undo to remove points if needed.")
                return
            
            result = self.centerlinePicker.toggleClickMode(centerlineNode)
            if result:
                if self.centerlinePicker.isActive:
                    self.btnClickMode.setText("⏸ Stop Picker")
                    self.btnClickMode.setStyleSheet("background-color: #dc3545; color: white; font-weight: bold; padding: 10px;")
                    self.lblClickStatus.setText(f"Picker: ACTIVE - Click centerline to pick Zone {currentCount} start")
                    self.lblClickStatus.setStyleSheet("color: #28a745; font-weight: bold;")
                else:
                    self.btnClickMode.setText("🎯 Start Zone Picker")
                    self.btnClickMode.setStyleSheet("background-color: #17a2b8; color: white; font-weight: bold; padding: 10px;")
                    self.lblClickStatus.setText("Picker: OFF")
                    self.lblClickStatus.setStyleSheet("color: #666; font-style: italic;")
        except Exception as e:
            slicer.util.errorDisplay(f"Error toggling click mode: {str(e)}")
            import traceback
            traceback.print_exc()

    def onConfirmZone(self):
        """Confirm the current zone point."""
        if not self.logic:
            return
        currentCount = self.logic.zoneNode.GetNumberOfControlPoints() if self.logic.zoneNode else 0
        zoneName = f"Zone_{currentCount}_start"
        self.centerlinePicker.confirmZonePoint(zoneName)
        self.updateZoneUI()
        self.btnConfirmPoint.setEnabled(False)
        
        # Update status
        newCount = currentCount + 1
        if newCount < 10:
            self.lblStatus.setText(f"✓ {zoneName} confirmed. Next: Zone {newCount} start")
        else:
            self.lblStatus.setText(f"✓ All 10 zone landmarks complete!")

    def onUndoZone(self):
        """Undo last zone point."""
        if self.logic:
            self.logic.undoLastZonePoint()
            self.updateZoneUI()

    def onJumpToZone(self, item):
        """Jump to zone point on double-click."""
        if self.logic:
            index = self.zoneList.row(item)
            self.logic.jumpToZonePoint(index)

    def onZoneContextMenu(self, position):
        """Handle zone list context menu."""
        item = self.zoneList.itemAt(position)
        if not item or not self.logic:
            return
        
        menu = qt.QMenu()
        deleteAction = menu.addAction("Delete Point")
        renameAction = menu.addAction("Rename Point")
        
        action = menu.exec_(self.zoneList.mapToGlobal(position))
        index = self.zoneList.row(item)
        
        if action == deleteAction:
            self.logic.deleteZonePoint(index)
            self.updateZoneUI()
        elif action == renameAction:
            newName, ok = qt.QInputDialog.getText(
                self, "Rename Point", "New label:",
                qt.QLineEdit.Normal, self.logic.zoneNode.GetNthControlPointLabel(index)
            )
            if ok and newName:
                self.logic.renameZonePoint(index, newName)
                self.updateZoneUI()