import os
import qt
import slicer
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin

#
# TAAAnnotation
#
class TAAAnnotation(ScriptedLoadableModule):
    """Main module class - defines metadata and help text"""
    
    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = "TAA Annotation"
        self.parent.categories = ["Segmentation"]
        self.parent.dependencies = ["SegmentEditor", "ExtractCenterline"]
        self.parent.contributors = ["University of Ottawa Heart Institute (Canada)"]
        self.parent.helpText = """
        TAA (Thoracic Aortic Aneurysm) Annotation Protocol Module.
        <br><br>
        This module provides a guided workflow for:
        <ul>
        <li>Loading CT scans and segmentation masks</li>
        <li>Refining segmentation using Segment Editor</li>
        <li>Extracting centerlines using VMTK</li>
        <li>Placing zonal landmarks on the centerline</li>
        <li>Exporting annotated data bundles</li>
        </ul>
        """
        self.parent.acknowledgementText = """
        Developed for TAA research annotation workflow.
        """

#
# TAAAnnotationWidget
#
class TAAAnnotationWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
    """Widget class - handles the UI"""

    def __init__(self, parent=None):
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)
        self.logic = None
        self._parameterNode = None
        self._updatingGUIFromParameterNode = False

    def setup(self):
        ScriptedLoadableModuleWidget.setup(self)
        
        # Load widget from .ui file or create programmatically
        self.uiWidget = self.createUI()
        self.layout.addWidget(self.uiWidget)
        
        # Create logic instance
        self.logic = TAAAnnotationLogic()
        
        # Connect signals
        self.connectSignals()
        
        # Initialize autosave
        from TAAAnnotationLib.AutosaveManager import AutosaveManager
        self.autosaveManager = AutosaveManager(self.logic)
        self.autosaveManager.attemptCrashRecovery(self.showRecoveryBanner)
        
        # Initialize centerline picker
        from TAAAnnotationLib.CenterlinePicker import CenterlinePicker
        self.centerlinePicker = CenterlinePicker(self.logic, self.updateZoneUI)
        
        # Add spacer
        self.layout.addStretch(1)
        
        # Initial UI state
        self.updateUIState()

    def createUI(self):
        """Create the UI programmatically"""
        widget = qt.QWidget()
        layout = qt.QVBoxLayout(widget)
        
        # Styles
        self.defaultStyle = "text-align: left; padding: 8px; font-size: 12px;"
        self.doneStyle = "background-color: #28a745; color: white; text-align: left; padding: 8px; font-weight: bold;"
        self.activeStyle = "background-color: #fff3cd; border: 2px solid #ffc107; text-align: left; padding: 8px;"
        
        # --- Header ---
        title = qt.QLabel("TAA Refinement Protocol")
        title.setStyleSheet("font-weight: bold; font-size: 16px; margin-bottom: 10px; color: #333;")
        title.setAlignment(qt.Qt.AlignCenter)
        layout.addWidget(title)
        
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
        layout.addWidget(self.btnLoad)
        
        # --- Phase 2: Refine ---
        self.btnSeg = qt.QPushButton("2. Refine Mask")
        self.btnSeg.setStyleSheet(self.defaultStyle)
        self.btnSeg.setEnabled(False)
        layout.addWidget(self.btnSeg)
        
        # --- Phase 3: VMTK ---
        self.btnVmtk = qt.QPushButton("3. Extract VMTK Centerline")
        self.btnVmtk.setStyleSheet(self.defaultStyle)
        self.btnVmtk.setEnabled(False)
        layout.addWidget(self.btnVmtk)
        
        # --- Phase 4: Zones ---
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
        btnLayout.addWidget(self.btnClickMode)
        
        self.btnConfirmPoint = qt.QPushButton("✅ Confirm Zone")
        self.btnConfirmPoint.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 10px;")
        self.btnConfirmPoint.setEnabled(False)
        btnLayout.addWidget(self.btnConfirmPoint)
        zonesLayout.addLayout(btnLayout)
        
        # Status labels
        self.lblClickStatus = qt.QLabel("Picker: OFF")
        self.lblClickStatus.setStyleSheet("color: #666; font-style: italic;")
        zonesLayout.addWidget(self.lblClickStatus)
        
        self.btnUndoPoint = qt.QPushButton("↩ Undo Last Point")
        self.btnUndoPoint.setEnabled(False)
        zonesLayout.addWidget(self.btnUndoPoint)
        
        self.lblZoneCount = qt.QLabel("Zone Points: 0")
        self.lblZoneCount.setStyleSheet("font-weight: bold; color: #17a2b8;")
        zonesLayout.addWidget(self.lblZoneCount)
        
        # Zone list
        self.zoneList = qt.QListWidget()
        self.zoneList.setMaximumHeight(120)
        self.zoneList.setToolTip("Double-click to jump, right-click to delete")
        self.zoneList.setContextMenuPolicy(qt.Qt.CustomContextMenu)
        zonesLayout.addWidget(self.zoneList)
        
        layout.addWidget(self.groupZones)
        
        # --- Quick Save ---
        self.btnQuickSave = qt.QPushButton("💾 Quick Save Progress")
        self.btnQuickSave.setStyleSheet("background-color: #6c757d; color: white; padding: 8px;")
        self.btnQuickSave.setEnabled(False)
        layout.addWidget(self.btnQuickSave)
        
        # --- Phase 5: Export ---
        line = qt.QFrame()
        line.setFrameShape(qt.QFrame.HLine)
        layout.addWidget(line)
        
        self.btnExport = qt.QPushButton("5. Export & Reset")
        self.btnExport.setStyleSheet("text-align: center; padding: 10px; font-weight: bold; background-color: #007bff; color: white;")
        self.btnExport.setEnabled(False)
        layout.addWidget(self.btnExport)
        
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
        layout.addWidget(self.notesEdit)
        
        return widget

    def connectSignals(self):
        """Connect UI signals to slots"""
        self.btnLoad.connect('clicked()', self.onLoadData)
        self.btnSeg.connect('clicked()', self.onRefineSetup)
        self.btnVmtk.connect('clicked()', self.onVmtkSetup)
        self.btnClickMode.connect('clicked()', self.onToggleClickMode)
        self.btnConfirmPoint.connect('clicked()', self.onConfirmZone)
        self.btnUndoPoint.connect('clicked()', self.onUndoZone)
        self.btnQuickSave.connect('clicked()', self.onQuickSave)
        self.btnExport.connect('clicked()', self.onExport)
        self.btnRecover.connect('clicked()', self.onRecover)
        self.btnIgnore.connect('clicked()', self.onIgnoreRecovery)
        self.zoneList.itemDoubleClicked.connect(self.onJumpToZone)
        self.zoneList.customContextMenuRequested.connect(self.onZoneContextMenu)
        self.notesEdit.textChanged.connect(self.onNotesChanged)

    def showRecoveryBanner(self, message):
        """Show recovery banner with message"""
        self.recoveryLabel.setText(message)
        self.recoveryBanner.show()

    def updateUIState(self):
        """Update UI based on current workflow state"""
        phase = self.logic.workflowState.get("phase", 0)
        
        self.btnSeg.setEnabled(phase >= 1)
        self.btnVmtk.setEnabled(phase >= 2)
        self.btnClickMode.setEnabled(phase >= 3)
        self.btnQuickSave.setEnabled(phase >= 1)
        self.btnExport.setEnabled(phase >= 3)

    def updateZoneUI(self):
        """Update zone-related UI elements"""
        if self.logic.zoneNode:
            count = self.logic.zoneNode.GetNumberOfControlPoints()
            self.lblZoneCount.setText(f"Zone Points: {count}")
            self.btnUndoPoint.setEnabled(count > 0)
            
            # Update list
            self.zoneList.clear()
            for i in range(count):
                label = self.logic.zoneNode.GetNthControlPointLabel(i)
                pos = [0, 0, 0]
                self.logic.zoneNode.GetNthControlPointPosition(i, pos)
                self.zoneList.addItem(f"{label}: ({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f})")
        
        # NEW: Enable Confirm button if there's a preview point
        if hasattr(self, 'centerlinePicker') and self.centerlinePicker.currentPreviewPos is not None:
            self.btnConfirmPoint.setEnabled(True)
        else:
            self.btnConfirmPoint.setEnabled(False)

    # --- Event Handlers ---
    def onLoadData(self):
        folderPath = qt.QFileDialog.getExistingDirectory(self.parent, "Select Subject Directory")
        if folderPath:
            success = self.logic.loadData(folderPath)
            if success:
                self.markDone(self.btnLoad, "Data Loaded")
                self.updateUIState()
                self.lblStatus.setText(f"✓ Loaded: {self.logic.currentId}")

    def onRefineSetup(self):
        if self.logic.setupRefinement():
            self.markDone(self.btnSeg, "Refine Mode")
            self.updateUIState()

    def onVmtkSetup(self):
        if self.logic.setupVMTK():
            self.markDone(self.btnVmtk, "VMTK Ready")
            self.updateUIState()

    def onToggleClickMode(self):
        try:
            centerlineNode = self.centerlineCombo.currentNode()
            result = self.centerlinePicker.toggleClickMode(centerlineNode)
            print(f"DEBUG: toggleClickMode returned: {result}, isActive: {self.centerlinePicker.isActive}")
            if result:
                isActive = self.centerlinePicker.isActive
                self.btnClickMode.setText("🛑 Stop Picker" if isActive else "🎯 Start Zone Picker")
                self.btnClickMode.setStyleSheet(
                    "background-color: #dc3545; color: white; font-weight: bold; padding: 10px;" if isActive
                    else "background-color: #17a2b8; color: white; font-weight: bold; padding: 10px;"
                )
                self.lblClickStatus.setText("Picker: ON" if isActive else "Picker: OFF")
                self.lblClickStatus.setStyleSheet(
                    "color: #28a745; font-weight: bold;" if isActive 
                    else "color: #666; font-style: italic;"
                )
                
                if isActive:
                    if self.logic.segNode:
                        self.logic.segNode.GetDisplayNode().SetVisibility(True)
                        self.logic.segNode.GetDisplayNode().SetOpacity(0.5)
                    if self.logic.refNode:
                        self.logic.refNode.GetDisplayNode().SetVisibility(True)
                        self.logic.refNode.GetDisplayNode().SetOpacity(0.2)
        except Exception as e:
            import traceback
            traceback.print_exc()
            slicer.util.errorDisplay(f"Error in toggleClickMode: {str(e)}")

    def onConfirmZone(self):
        self.centerlinePicker.confirmZonePoint()
        self.updateZoneUI()
        self.btnConfirmPoint.setEnabled(False)

    def onUndoZone(self):
        self.logic.undoLastZonePoint()
        self.updateZoneUI()

    def onQuickSave(self):
        self.autosaveManager.quickSave()
        self.lblStatus.setText(f"✓ Quick saved at {self.logic.getTimestamp()}")

    def onExport(self):
        from TAAAnnotationLib.ExportManager import ExportManager
        exporter = ExportManager(self.logic)
        notes = self.notesEdit.toPlainText().strip()
        if exporter.exportAll(notes):
            self.resetApplication()

    def onRecover(self):
        if self.autosaveManager.recoverSession():
            self.recoveryBanner.hide()
            self.updateUIState()

    def onIgnoreRecovery(self):
        self.autosaveManager.cleanup()
        self.recoveryBanner.hide()

    def onJumpToZone(self, item):
        index = self.zoneList.row(item)
        self.logic.jumpToZonePoint(index)

    def onZoneContextMenu(self, position):
        item = self.zoneList.itemAt(position)
        if not item:
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
                self.parent, "Rename Point", "New label:",
                qt.QLineEdit.Normal, self.logic.zoneNode.GetNthControlPointLabel(index)
            )
            if ok and newName:
                self.logic.renameZonePoint(index, newName)
                self.updateZoneUI()

    def onNotesChanged(self):
        self.logic.hasUnsavedWork = True

    def markDone(self, button, text):
        button.setStyleSheet(self.doneStyle)
        button.setText(f"✔ {text}")

    def resetApplication(self):
        """Reset to initial state"""
        self.centerlinePicker.disable()
        self.logic.reset()
        self.autosaveManager.cleanup()
        
        # Reset UI buttons
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
        
        self.updateUIState()
        self.zoneList.clear()
        self.lblZoneCount.setText("Zone Points: 0")
        self.lblClickStatus.setText("Picker: OFF")
        self.lblClickStatus.setStyleSheet("color: #666; font-style: italic;")  # NEW: Reset style
        self.notesEdit.setPlainText("")
        self.lblStatus.setText("✓ Reset. Ready for next scan.")

    def cleanup(self):
        """Called when module is unloaded"""
        self.centerlinePicker.disable()
        self.autosaveManager.stop()


#
# TAAAnnotationLogic
#
class TAAAnnotationLogic(ScriptedLoadableModuleLogic):
    """Logic class - handles all business logic"""

    def __init__(self):
        ScriptedLoadableModuleLogic.__init__(self)
        self.reset()

    def reset(self):
        """Reset all state"""
        self.currentId = ""
        self.rootDir = ""
        self.volNode = None
        self.segNode = None
        self.refNode = None
        self.binarizedMergedNode = None
        self.zoneNode = None
        self.networkNode = None
        self.endpointNode = None
        self.centerlineNode = None
        self.hasUnsavedWork = False
        self.workflowState = {
            "phase": 0,
            "lastSave": None,
            "zoneCount": 0
        }
        slicer.mrmlScene.Clear(0)

    def getTimestamp(self):
        from datetime import datetime
        return datetime.now().strftime("%H:%M:%S")

    def getIdFromFiles(self, folderPath):
        """Extract patient ID from CT scan filename"""
        import glob
        searchPattern = os.path.join(folderPath, "ct_scan_*.nii.gz")
        foundFiles = glob.glob(searchPattern)
        if not foundFiles:
            return None
        filename = os.path.basename(foundFiles[0])
        return filename.replace("ct_scan_", "").replace(".nii.gz", "")

    def loadData(self, folderPath):
        """Load all data for a subject"""
        import numpy as np
        
        try:
            self.rootDir = folderPath
            detectedId = self.getIdFromFiles(folderPath)
            
            if not detectedId:
                slicer.util.errorDisplay("Could not find 'ct_scan_*.nii.gz' in folder")
                return False
            
            self.currentId = detectedId
            
            # Clear scene
            if slicer.mrmlScene.GetNumberOfNodes() > 0:
                slicer.mrmlScene.Clear(0)
            
            # Define paths
            volPath = os.path.join(folderPath, f"ct_scan_{self.currentId}.nii.gz")
            segPath = os.path.join(folderPath, f"{self.currentId}_unified_mask_smoothed.nii.gz")
            refPath = os.path.join(folderPath, f"{self.currentId}_merged.nii.gz")
            
            # Validate files
            for path, name in [(volPath, "CT scan"), (segPath, "unified segmentation"), (refPath, "merged segmentation")]:
                if not os.path.exists(path):
                    slicer.util.errorDisplay(f"Missing {name}: {path}")
                    return False
            
            # Load CT volume
            self.volNode = slicer.util.loadVolume(volPath)
            
            # Load unified segmentation
            tempUnified = slicer.util.loadLabelVolume(segPath)
            self.segNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", f"{self.currentId}_unified")
            slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(tempUnified, self.segNode)
            slicer.mrmlScene.RemoveNode(tempUnified)
            self.segNode.CreateClosedSurfaceRepresentation()
            
            # Load merged segmentation
            tempMerged = slicer.util.loadLabelVolume(refPath)
            self.refNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", f"{self.currentId}_merged")
            slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(tempMerged, self.refNode)
            slicer.mrmlScene.RemoveNode(tempMerged)
            self.refNode.CreateClosedSurfaceRepresentation()
            
            # Create binarized merged
            tempMergedBinary = slicer.util.loadLabelVolume(refPath)
            array = slicer.util.arrayFromVolume(tempMergedBinary)
            array[array > 0] = 1
            slicer.util.updateVolumeFromArray(tempMergedBinary, array)
            
            self.binarizedMergedNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", f"{self.currentId}_merged_binary")
            slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(tempMergedBinary, self.binarizedMergedNode)
            slicer.mrmlScene.RemoveNode(tempMergedBinary)
            self.binarizedMergedNode.CreateClosedSurfaceRepresentation()
            
            # Set visibility
            self.segNode.GetDisplayNode().SetVisibility(True)
            self.refNode.GetDisplayNode().SetVisibility(False)
            self.binarizedMergedNode.GetDisplayNode().SetVisibility(False)
            
            # Setup view
            slicer.app.layoutManager().sliceWidget('Red').sliceLogic().GetSliceCompositeNode().SetBackgroundVolumeID(self.volNode.GetID())
            slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)
            
            self.workflowState["phase"] = 1
            self.hasUnsavedWork = True
            return True
            
        except Exception as e:
            slicer.util.errorDisplay(f"Failed to load data: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

    def setupRefinement(self):
        """Setup segment editor for refinement"""
        try:
            if not self.segNode:
                slicer.util.errorDisplay("No segmentation loaded")
                return False
            
            slicer.util.selectModule("SegmentEditor")
            
            segmentEditorNode = slicer.mrmlScene.GetSingletonNode("SegmentEditor", "vtkMRMLSegmentEditorNode")
            if segmentEditorNode:
                segmentEditorNode.SetAndObserveSegmentationNode(self.segNode)
                segmentEditorNode.SetAndObserveSourceVolumeNode(self.volNode)
            
            if self.refNode:
                self.refNode.GetDisplayNode().SetVisibility(False)
            self.segNode.GetDisplayNode().SetVisibility(True)
            
            self.workflowState["phase"] = 2
            return True
            
        except Exception as e:
            slicer.util.errorDisplay(f"Failed to setup refinement: {str(e)}")
            return False

    def setupVMTK(self):
        """Setup VMTK centerline extraction"""
        import vtk
        import numpy as np
        
        try:
            if not hasattr(slicer.modules, 'extractcenterline'):
                slicer.util.errorDisplay("VMTK Extension not installed")
                return False
            
            # Export refined segmentation to temporary label volume
            if not self.segNode:
                slicer.util.errorDisplay("No refined segmentation available")
                return False
            
            # Create temporary label volume from refined segmentation
            tempRefinedLabel = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "temp_refined_label")
            slicer.modules.segmentations.logic().ExportAllSegmentsToLabelmapNode(
                self.segNode, tempRefinedLabel, slicer.vtkSegmentation.EXTENT_REFERENCE_GEOMETRY
            )
            
            # Binarize - merge all labels into single mask
            array = slicer.util.arrayFromVolume(tempRefinedLabel)
            array[array > 0] = 1
            slicer.util.updateVolumeFromArray(tempRefinedLabel, array)
            
            # Create segmentation node from binarized volume
            binarizedRefinedNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLSegmentationNode", 
                f"{self.currentId}_refined_binary"
            )
            slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                tempRefinedLabel, binarizedRefinedNode
            )
            binarizedRefinedNode.CreateClosedSurfaceRepresentation()
            
            # Clean up temporary label volume
            slicer.mrmlScene.RemoveNode(tempRefinedLabel)
            
            # Setup VMTK module
            slicer.util.selectModule("ExtractCenterline")
            slicer.app.processEvents()
            
            vmtkWidget = slicer.modules.extractcenterline.widgetRepresentation()
            if not vmtkWidget:
                raise RuntimeError("Failed to get VMTK widget")
            
            widgetSelf = vmtkWidget.self()
            parameterNode = widgetSelf._parameterNode
            
            if not parameterNode:
                logic = widgetSelf.logic
                parameterNode = logic.getParameterNode()
            
            # Create output nodes
            self.networkNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode", f"{self.currentId}_Network")
            self.endpointNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", f"{self.currentId}_Endpoints")
            self.centerlineNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode", f"{self.currentId}_Centerline")
            
            # Create surface model from binarized refined mask
            inputSurfaceModel = None
            if binarizedRefinedNode:
                inputSurfaceModel = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode", f"{self.currentId}_refined_binary_surface")
                segmentation = binarizedRefinedNode.GetSegmentation()
                segmentId = segmentation.GetNthSegmentID(0)
                binarizedRefinedNode.CreateClosedSurfaceRepresentation()
                polyData = vtk.vtkPolyData()
                binarizedRefinedNode.GetClosedSurfaceRepresentation(segmentId, polyData)
                
                if polyData.GetNumberOfPoints() > 0:
                    inputSurfaceModel.SetAndObservePolyData(polyData)
                    inputSurfaceModel.CreateDefaultDisplayNodes()
                    displayNode = inputSurfaceModel.GetDisplayNode()
                    if displayNode:
                        displayNode.SetVisibility(True)
                        displayNode.SetOpacity(0.3)
                        displayNode.SetColor(0.8, 0.8, 0.0)
        
            # Set node references
            if inputSurfaceModel:
                parameterNode.SetNodeReferenceID("InputSurface", inputSurfaceModel.GetID())
            parameterNode.SetNodeReferenceID("OutputCenterlineModel", self.centerlineNode.GetID())
            parameterNode.SetNodeReferenceID("NetworkModel", self.networkNode.GetID())
            parameterNode.SetNodeReferenceID("EndPoints", self.endpointNode.GetID())
            
            # Hide other segmentations
            for node in [self.segNode, self.refNode, self.binarizedMergedNode]:
                if node:
                    node.GetDisplayNode().SetVisibility(False)
            
            # Hide the temporary binarized node as well (keep only surface visible)
            if binarizedRefinedNode:
                binarizedRefinedNode.GetDisplayNode().SetVisibility(False)
            
            widgetSelf.updateGUIFromParameterNode()
            slicer.app.processEvents()
            
            self.workflowState["phase"] = 3
            return True
        
        except Exception as e:
            slicer.util.errorDisplay(f"Failed to setup VMTK: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

    def createZoneNode(self):
        """Create zone fiducial node if needed"""
        if not self.zoneNode:
            self.zoneNode = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsFiducialNode",
                f"{self.currentId}_Zones"
            )
            display = self.zoneNode.GetDisplayNode()
            if display:
                display.SetSelectedColor(1, 0.5, 0)
                display.SetGlyphScale(3.0)
                display.SetTextScale(4.0)
        return self.zoneNode

    def addZonePoint(self, worldPos, pointId=-1):
        """Add a zone point"""
        zoneNode = self.createZoneNode()
        n = zoneNode.GetNumberOfControlPoints()
        zoneNode.AddControlPoint(worldPos[0], worldPos[1], worldPos[2])
        
        label = f"Zone_{n+1}"
        if pointId >= 0:
            label += f"_P{pointId}"
        zoneNode.SetNthControlPointLabel(n, label)
        
        self.workflowState["zoneCount"] = n + 1
        self.hasUnsavedWork = True
        return label

    def undoLastZonePoint(self):
        """Remove last zone point"""
        if self.zoneNode:
            n = self.zoneNode.GetNumberOfControlPoints()
            if n > 0:
                self.zoneNode.RemoveNthControlPoint(n - 1)
                self.workflowState["zoneCount"] = n - 1

    def deleteZonePoint(self, index):
        """Delete zone point at index"""
        if self.zoneNode and 0 <= index < self.zoneNode.GetNumberOfControlPoints():
            self.zoneNode.RemoveNthControlPoint(index)

    def renameZonePoint(self, index, newName):
        """Rename zone point"""
        if self.zoneNode and 0 <= index < self.zoneNode.GetNumberOfControlPoints():
            self.zoneNode.SetNthControlPointLabel(index, newName)

    def jumpToZonePoint(self, index):
        """Jump views to zone point"""
        if self.zoneNode and 0 <= index < self.zoneNode.GetNumberOfControlPoints():
            pos = [0, 0, 0]
            self.zoneNode.GetNthControlPointPosition(index, pos)
            
            for color in ['Red', 'Yellow', 'Green']:
                sliceWidget = slicer.app.layoutManager().sliceWidget(color)
                if sliceWidget:
                    offset = pos[2] if color == 'Red' else (pos[1] if color == 'Green' else pos[0])
                    sliceWidget.sliceLogic().SetSliceOffset(offset)
            
            threeDWidget = slicer.app.layoutManager().threeDWidget(0)
            if threeDWidget:
                threeDWidget.threeDView().setFocalPoint(pos[0], pos[1], pos[2])


#
# TAAAnnotationTest
#
class TAAAnnotationTest(ScriptedLoadableModuleTest):
    """Test case for module"""

    def setUp(self):
        slicer.mrmlScene.Clear()

    def runTest(self):
        self.setUp()
        self.test_TAAAnnotation1()

    def test_TAAAnnotation1(self):
        self.delayDisplay("Starting test")
        logic = TAAAnnotationLogic()
        self.assertIsNotNone(logic)
        self.delayDisplay("Test passed")