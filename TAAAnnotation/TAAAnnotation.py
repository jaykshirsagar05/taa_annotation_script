import os
import qt
import slicer
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin
from TAAAnnotationLib import (
    OrthancClient, AnnotationStatus,
    WorkflowWidget, OrthancIntegrationWidget,
    AutosaveManager, CenterlinePicker, ExportManager
)
import tempfile


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
        <li>Loading CT scans from Orthanc PACS or local files</li>
        <li>Refining segmentation using Segment Editor</li>
        <li>Extracting centerlines using VMTK</li>
        <li>Placing zonal landmarks on the centerline</li>
        <li>Exporting and submitting annotated data</li>
        </ul>
        """
        self.parent.acknowledgementText = """
        Developed for TAA research annotation workflow.
        """


class TAAAnnotationWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
    """Widget class - handles the UI"""

    def __init__(self, parent=None):
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)
        self.logic = None
        
        # Orthanc integration
        self.orthancClient = OrthancClient()
        self.orthancTempDir = None

    def setup(self):
        ScriptedLoadableModuleWidget.setup(self)
        
        # Create logic
        self.logic = TAAAnnotationLogic()
        
        # --- Main Layout ---
        mainWidget = qt.QWidget()
        mainLayout = qt.QVBoxLayout(mainWidget)
        
        # --- Header ---
        title = qt.QLabel("TAA Refinement Protocol")
        title.setStyleSheet("font-weight: bold; font-size: 16px; margin-bottom: 10px; color: #333;")
        title.setAlignment(qt.Qt.AlignCenter)
        mainLayout.addWidget(title)
        
        # --- Orthanc Integration Widget ---
        self.orthancWidget = OrthancIntegrationWidget(self.orthancClient)
        self.orthancWidget.studyLoaded.connect(self.onOrthancStudyLoaded)
        self.orthancWidget.annotationSubmitted.connect(self.onSubmitToOrthanc)
        self.orthancWidget.annotationApproved.connect(self.onApproveAnnotation)
        self.orthancWidget.annotationRejected.connect(self.onRejectAnnotation)
        self.orthancWidget.loggedOut.connect(self.onOrthancLogout)
        mainLayout.addWidget(self.orthancWidget)
        
        # --- Separator ---
        separator = qt.QFrame()
        separator.setFrameShape(qt.QFrame.HLine)
        separator.setStyleSheet("margin: 10px 0;")
        mainLayout.addWidget(separator)
        
        orLabel = qt.QLabel("— OR load manually below —")
        orLabel.setAlignment(qt.Qt.AlignCenter)
        orLabel.setStyleSheet("color: #999; margin: 5px 0;")
        mainLayout.addWidget(orLabel)
        
        # --- Workflow Widget ---
        self.workflowWidget = WorkflowWidget()
        self.workflowWidget.setLogic(self.logic)
        self.workflowWidget.loadDataRequested.connect(self.onLoadData)
        self.workflowWidget.refineRequested.connect(self.onRefineSetup)
        self.workflowWidget.vmtkRequested.connect(self.onVmtkSetup)
        self.workflowWidget.exportRequested.connect(self.onExport)
        self.workflowWidget.quickSaveRequested.connect(self.onQuickSave)
        self.workflowWidget.notesChanged.connect(self.onNotesChanged)
        self.workflowWidget.btnRecover.clicked.connect(self.onRecover)
        self.workflowWidget.btnIgnore.clicked.connect(self.onIgnoreRecovery)
        mainLayout.addWidget(self.workflowWidget)
        
        # Spacer
        mainLayout.addStretch(1)
        
        self.layout.addWidget(mainWidget)
        
        # Initialize autosave
        self.autosaveManager = AutosaveManager(self.logic)
        self.autosaveManager.attemptCrashRecovery(self.workflowWidget.showRecoveryBanner)
        
        # Initialize centerline picker
        self.centerlinePicker = CenterlinePicker(self.logic, self.workflowWidget.updateZoneUI)
        self.workflowWidget.setCenterlinePicker(self.centerlinePicker)
        
        # Initial UI state
        self.workflowWidget.updateUIState(0)

    # --- Manual Load Handler ---
    def onLoadData(self):
        """Handle manual data loading from folder."""
        folderPath = qt.QFileDialog.getExistingDirectory(self.parent, "Select Subject Directory")
        if folderPath:
            success = self.logic.loadData(folderPath)
            if success:
                self.workflowWidget.markDone(1, "Data Loaded")
                self.workflowWidget.updateUIState(self.logic.workflowState.get("phase", 0))
                self.workflowWidget.setStatus(f"✓ Loaded: {self.logic.currentId}")
                self.workflowWidget.setCurrentId(self.logic.currentId)

    # --- Orthanc Handlers ---
    def onOrthancStudyLoaded(self, study_id: str, study_info: dict):
        """Handle study loaded from Orthanc."""
        ct_path = study_info.get('_ct_path')
        unified_path = study_info.get('_unified_path')
        merged_path = study_info.get('_merged_path')
        self.orthancTempDir = study_info.get('_temp_dir')
        
        # Load using logic
        self.logic.currentId = study_info['patient_id']
        self.logic.rootDir = self.orthancTempDir
        self.logic.loadProcedureDataFromPaths(ct_path, unified_path, merged_path)
        
        # Update UI
        self.workflowWidget.setCurrentId(study_info['patient_id'], "from Orthanc")
        self.workflowWidget.markDone(1, "Data Loaded (Orthanc)")
        self.workflowWidget.updateUIState(self.logic.workflowState.get("phase", 0))
        
        # Load existing annotations for reviewers
        if self.orthancWidget.getRole() == "reviewer":
            self._loadExistingAnnotations(study_id, self.orthancTempDir)

    def _loadExistingAnnotations(self, study_id: str, temp_dir: str):
        """Load existing annotations for review."""
        # Download refined mask
        refined_path = self.orthancClient.download_nifti(
            study_id,
            OrthancClient.ATTACHMENT_REFINED_MASK,
            os.path.join(temp_dir, f"{self.logic.currentId}_refined_mask.seg.nrrd")
        )
        if refined_path:
            slicer.util.loadSegmentation(refined_path)
        
        # Download zones
        zones_path = self.orthancClient.download_nifti(
            study_id,
            OrthancClient.ATTACHMENT_ZONES,
            os.path.join(temp_dir, f"{self.logic.currentId}_Zones.fcsv")
        )
        if zones_path:
            slicer.util.loadMarkups(zones_path)
        
        # Download centerline
        centerline_path = self.orthancClient.download_nifti(
            study_id,
            OrthancClient.ATTACHMENT_CENTERLINE,
            os.path.join(temp_dir, f"{self.logic.currentId}_Centerline.vtk")
        )
        if centerline_path:
            slicer.util.loadModel(centerline_path)

    def onSubmitToOrthanc(self, study_id: str):
        """Handle annotation submission."""
        if not study_id:
            slicer.util.errorDisplay("No Orthanc study loaded")
            return
        
        # Validate zones
        zoneCount = self.logic.zoneNode.GetNumberOfControlPoints() if self.logic.zoneNode else 0
        if zoneCount < 10:
            if not slicer.util.confirmYesNoDisplay(
                f"Only {zoneCount}/10 zone landmarks placed. Submit anyway?",
                "Incomplete Zones"
            ):
                return
        
        # Show progress
        progressDialog = slicer.util.createProgressDialog(labelText="Submitting...", maximum=6)
        
        try:
            progressDialog.setValue(1)
            slicer.app.processEvents()
            
            export_dir = tempfile.mkdtemp(prefix="orthanc_submit_")
            files = {}
            
            # Save segmentation
            if self.logic.segNode:
                path = os.path.join(export_dir, f"{self.logic.currentId}_refined_mask.seg.nrrd")
                slicer.util.saveNode(self.logic.segNode, path)
                files["refined_mask"] = path
                print(f"[Submit] Saved refined mask: {path}")
            
            progressDialog.setValue(2)
            slicer.app.processEvents()
            
            # Save centerline - use logic node directly
            if self.logic.centerlineNode:
                path = os.path.join(export_dir, f"{self.logic.currentId}_Centerline.vtk")
                slicer.util.saveNode(self.logic.centerlineNode, path)
                files["centerline"] = path
                print(f"[Submit] Saved centerline: {path}")
            else:
                print("[Submit] WARNING: No centerline node found in logic")
            
            progressDialog.setValue(3)
            slicer.app.processEvents()
            
            # Save endpoints - use logic node directly
            if self.logic.endpointNode:
                path = os.path.join(export_dir, f"{self.logic.currentId}_Endpoints.fcsv")
                slicer.util.saveNode(self.logic.endpointNode, path)
                files["endpoints"] = path
                print(f"[Submit] Saved endpoints: {path}")
            else:
                print("[Submit] WARNING: No endpoints node found in logic")
            
            progressDialog.setValue(4)
            slicer.app.processEvents()
            
            # Save zones
            if self.logic.zoneNode:
                path = os.path.join(export_dir, f"{self.logic.currentId}_Zones.fcsv")
                slicer.util.saveNode(self.logic.zoneNode, path)
                files["zones"] = path
                print(f"[Submit] Saved zones: {path}")
            
            progressDialog.setValue(5)
            slicer.app.processEvents()
            
            notes = self.workflowWidget.getNotesText()
            
            progressDialog.setLabelText("Uploading...")
            slicer.app.processEvents()
            
            success, message = self.orthancClient.submit_annotation(study_id, files, notes)
            
            progressDialog.close()
            
            if success:
                slicer.util.infoDisplay(f"✓ Annotation submitted!\n\n{message}")
                self.logic.hasUnsavedWork = False
                self.orthancWidget.markSubmitted()
                self.orthancWidget.refreshWorklist()
                # Reset for next study after successful submission
                self.resetForNextStudy()
            else:
                slicer.util.errorDisplay(f"Failed: {message}")
                
        except Exception as e:
            progressDialog.close()
            slicer.util.errorDisplay(f"Error: {str(e)}")
            import traceback
            traceback.print_exc()

    def onApproveAnnotation(self, study_id: str):
        """Handle annotation approval."""
        if not slicer.util.confirmYesNoDisplay(
            "Approve this annotation as ground truth?",
            "Confirm Approval"
        ):
            return
        
        try:
            comments = self.orthancWidget.getReviewComments()
            success, message = self.orthancClient.approve_annotation(study_id, comments)
            
            if success:
                slicer.util.infoDisplay(f"✓ {message}")
                self.orthancWidget.disableReviewButtons()
                self.orthancWidget.refreshWorklist()
                self.resetForNextStudy()
            else:
                slicer.util.errorDisplay(f"Failed: {message}")
        except Exception as e:
            slicer.util.errorDisplay(f"Error: {str(e)}")

    def onRejectAnnotation(self, study_id: str, reason: str):
        """Handle annotation rejection."""
        if not slicer.util.confirmYesNoDisplay(
            f"Reject this annotation?\n\nReason: {reason[:100]}...",
            "Confirm Rejection"
        ):
            return
        
        try:
            success, message = self.orthancClient.reject_annotation(study_id, reason)
            
            if success:
                slicer.util.infoDisplay(f"✓ {message}")
                self.orthancWidget.disableReviewButtons()
                self.orthancWidget.refreshWorklist()
                self.resetForNextStudy()
            else:
                slicer.util.errorDisplay(f"Failed: {message}")
        except Exception as e:
            slicer.util.errorDisplay(f"Error: {str(e)}")

    def onOrthancLogout(self):
        """Handle Orthanc logout."""
        if self.logic.hasUnsavedWork:
            if not slicer.util.confirmYesNoDisplay(
                "You have unsaved work. Logout anyway?",
                "Unsaved Work"
            ):
                return
        slicer.mrmlScene.Clear(0)
        self.logic.reset()
        self.workflowWidget.resetUI()

    def resetForNextStudy(self):
        """Reset for next study."""
        slicer.mrmlScene.Clear(0)
        self.logic.reset()
        self.workflowWidget.resetUI()
        self.workflowWidget.setOrthancMode(False)  # Reset Orthanc mode
        self.orthancWidget.resetForNextStudy()
        self.workflowWidget.setStatus("Ready for next study")

    # --- Workflow Handlers ---
    def onRefineSetup(self):
        if self.logic.setupRefinement():
            self.workflowWidget.markDone(2, "Refine Mode")
            self.workflowWidget.updateUIState(self.logic.workflowState.get("phase", 0))

    def onVmtkSetup(self):
        if self.logic.setupVMTK():
            self.workflowWidget.markDone(3, "VMTK Ready")
            self.workflowWidget.updateUIState(self.logic.workflowState.get("phase", 0))

    def onQuickSave(self):
        self.autosaveManager.quickSave()
        self.workflowWidget.setStatus(f"✓ Quick saved at {self.logic.getTimestamp()}")

    def onExport(self):
        exporter = ExportManager(self.logic)
        notes = self.workflowWidget.getNotesText()
        if exporter.exportAll(notes):
            self.resetApplication()

    def onRecover(self):
        if self.autosaveManager.recoverSession():
            self.workflowWidget.hideRecoveryBanner()
            self.workflowWidget.updateUIState(self.logic.workflowState.get("phase", 0))

    def onIgnoreRecovery(self):
        self.autosaveManager.cleanup()
        self.workflowWidget.hideRecoveryBanner()

    def onNotesChanged(self, text):
        self.logic.hasUnsavedWork = True

    def resetApplication(self):
        """Full reset."""
        self.centerlinePicker.disable()
        self.logic.reset()
        self.autosaveManager.cleanup()
        self.workflowWidget.resetUI()
        self.workflowWidget.updateUIState(0)
        self.workflowWidget.setStatus("✓ Reset. Ready for next scan.")

    def cleanup(self):
        """Module cleanup."""
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
            refPath = os.path.join(folderPath, f"{self.currentId}_merged_mask.nii.gz")
            
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
    
    def loadProcedureDataFromPaths(self, ct_path: str, unified_path: str, merged_path: str):
        """
        Load procedure data from explicit file paths (for Orthanc integration).
        
        Args:
            ct_path: Path to CT NIfTI file
            unified_path: Path to unified mask NIfTI file  
            merged_path: Path to merged mask NIfTI file
        """
        slicer.mrmlScene.Clear(0)
        
        # Load CT volume
        print(f"[Loading] CT from: {ct_path}")
        self.volNode = slicer.util.loadVolume(ct_path)
        if not self.volNode:
            raise RuntimeError(f"Failed to load CT volume from {ct_path}")
        
        # Load unified segmentation (for editing)
        print(f"[Loading] Unified mask from: {unified_path}")
        unifiedLabelNode = slicer.util.loadLabelVolume(unified_path)
        if not unifiedLabelNode:
            raise RuntimeError(f"Failed to load unified mask from {unified_path}")
        
        self.segNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", 
                                                        f"{self.currentId}_Segmentation")
        slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
            unifiedLabelNode, self.segNode)
        slicer.mrmlScene.RemoveNode(unifiedLabelNode)
        self.segNode.CreateClosedSurfaceRepresentation()
        
        # Load merged segmentation (reference)
        print(f"[Loading] Merged mask from: {merged_path}")
        mergedLabelNode = slicer.util.loadLabelVolume(merged_path)
        if mergedLabelNode:
            # FIXED: Use self.refNode to be compatible with setupRefinement()
            self.refNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode",
                                                                    f"{self.currentId}_Merged")
            slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                mergedLabelNode, self.refNode)
            slicer.mrmlScene.RemoveNode(mergedLabelNode)
            self.refNode.CreateClosedSurfaceRepresentation()
            self.refNode.GetDisplayNode().SetVisibility(False)
        
        # Create binarized version for centerline
        self._createBinarizedMask(merged_path)
        
        # Setup views
        self._setupViews()
        
        self.workflowState["phase"] = 1
        self.hasUnsavedWork = False
        
        print(f"[Loading] Complete - ready for annotation")

    def _createBinarizedMask(self, merged_path):
        """Create binarized mask for centerline extraction from merged mask path."""
        if not os.path.exists(merged_path):
            return

        tempMergedBinary = slicer.util.loadLabelVolume(merged_path)
        array = slicer.util.arrayFromVolume(tempMergedBinary)
        array[array > 0] = 1
        slicer.util.updateVolumeFromArray(tempMergedBinary, array)
            
        self.binarizedMergedNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", f"{self.currentId}_merged_binary")
        slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(tempMergedBinary, self.binarizedMergedNode)
        slicer.mrmlScene.RemoveNode(tempMergedBinary)
        self.binarizedMergedNode.CreateClosedSurfaceRepresentation()
        self.binarizedMergedNode.GetDisplayNode().SetVisibility(False)

    def _setupViews(self):
        """Configure 4-up view and background volume."""
        if self.volNode:
            slicer.app.layoutManager().sliceWidget('Red').sliceLogic().GetSliceCompositeNode().SetBackgroundVolumeID(self.volNode.GetID())
        slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)

    def loadProcedureDataFromPaths(self, ct_path: str, unified_path: str, merged_path: str):
        """
        Load procedure data from explicit file paths (for Orthanc integration).
        
        Args:
            ct_path: Path to CT NIfTI file
            unified_path: Path to unified mask NIfTI file  
            merged_path: Path to merged mask NIfTI file
        """
        slicer.mrmlScene.Clear(0)
        
        # Load CT volume
        print(f"[Loading] CT from: {ct_path}")
        self.volNode = slicer.util.loadVolume(ct_path)
        if not self.volNode:
            raise RuntimeError(f"Failed to load CT volume from {ct_path}")
        
        # Load unified segmentation (for editing)
        print(f"[Loading] Unified mask from: {unified_path}")
        unifiedLabelNode = slicer.util.loadLabelVolume(unified_path)
        if not unifiedLabelNode:
            raise RuntimeError(f"Failed to load unified mask from {unified_path}")
        
        self.segNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", 
                                                        f"{self.currentId}_Segmentation")
        slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
            unifiedLabelNode, self.segNode)
        slicer.mrmlScene.RemoveNode(unifiedLabelNode)
        self.segNode.CreateClosedSurfaceRepresentation()
        
        # Load merged segmentation (reference)
        print(f"[Loading] Merged mask from: {merged_path}")
        mergedLabelNode = slicer.util.loadLabelVolume(merged_path)
        if mergedLabelNode:
            # FIXED: Use self.refNode to be compatible with setupRefinement()
            self.refNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode",
                                                                    f"{self.currentId}_Merged")
            slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                mergedLabelNode, self.refNode)
            slicer.mrmlScene.RemoveNode(mergedLabelNode)
            self.refNode.CreateClosedSurfaceRepresentation()
            self.refNode.GetDisplayNode().SetVisibility(False)
        
        # Create binarized version for centerline
        self._createBinarizedMask(merged_path)
        
        # Setup views
        self._setupViews()
        
        self.workflowState["phase"] = 1
        self.hasUnsavedWork = False
        
        print(f"[Loading] Complete - ready for annotation")


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