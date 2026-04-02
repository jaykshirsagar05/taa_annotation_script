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
from TAAAnnotationLib.DatasetProfile import (
    DatasetProfile, PROFILES,
    detect_profile_from_folder, detect_profile_from_attachments,
)
from TAAAnnotationLib import config as _cfg
import tempfile


class TAAAnnotation(ScriptedLoadableModule):
    """Main module class - defines metadata and help text"""

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = "TAA Annotation"
        # Schedule update check 3 s after startup so it doesn't block module loading
        qt.QTimer.singleShot(3000, self._checkForUpdates)

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

    def _checkForUpdates(self):
        try:
            from TAAAnnotationLib.AutoUpdater import checkAndUpdate
            checkAndUpdate()
        except Exception as e:
            print(f"[TAAAnnotation] Update check error: {e}")


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
        """Handle manual data loading from folder (profile auto-detected)."""
        # Warn if there's unsaved work
        if self.logic.hasUnsavedWork:
            if not slicer.util.confirmYesNoDisplay(
                "You have unsaved work. Loading new data will discard it.\n\nContinue?",
                "Unsaved Work"
            ):
                return
        
        folderPath = qt.QFileDialog.getExistingDirectory(self.parent, "Select Subject Directory")
        if folderPath:
            # Disable buttons during load to prevent double-clicks
            self.workflowWidget.setButtonsEnabled(False)
            try:
                success = self.logic.loadData(folderPath)
                if success:
                    profile = self.logic.activeProfile
                    self.workflowWidget.setProfile(profile)
                    self.workflowWidget.markDone(1, "Data Loaded")
                    
                    if profile and profile.has_precalculated_centerline and self.logic.centerlineNode:
                        self.workflowWidget.markDone(3, "Centerline Pre-loaded")
                    
                    self.workflowWidget.updateUIState(self.logic.workflowState.get("phase", 0))
                    profile_label = f" [{profile.name}]" if profile else ""
                    self.workflowWidget.setStatus(f"✓ Loaded: {self.logic.currentId}{profile_label}")
                    self.workflowWidget.setCurrentId(self.logic.currentId)
            finally:
                self.workflowWidget.setButtonsEnabled(True)

    # --- Orthanc Handlers ---
    def onOrthancStudyLoaded(self, study_id: str, study_info: dict):
        """Handle study loaded from Orthanc (profile-aware)."""
        try:
            profile = study_info.get('_profile')
            file_paths = study_info.get('_file_paths', {})
            self.orthancTempDir = study_info.get('_temp_dir')
            
            # Backward-compat: if no profile, build a legacy dual-mask paths dict
            if profile is None:
                profile = PROFILES[DatasetProfile.DUAL_MASK]
                file_paths = {
                    "ct": study_info.get('_ct_path'),
                    "seg_mask": study_info.get('_unified_path'),
                    "ref_mask": study_info.get('_merged_path'),
                }
            
            print(f"[Orthanc] Loading profile: {profile.name}, paths: {list(file_paths.keys())}")
            for k, v in file_paths.items():
                exists = os.path.exists(v) if v else 'N/A'
                print(f"  {k}: {v} (exists={exists})")
            
            # Load using logic
            self.logic.currentId = study_info['patient_id']
            self.logic.rootDir = self.orthancTempDir
            self.logic.activeProfile = profile
            errors = self.logic.loadDataWithProfile(profile, file_paths)
            self.logic.applyConfiguredCtWindowLevel()
            
            # Update UI with profile info
            self.workflowWidget.setProfile(profile)
            self.workflowWidget.setCurrentId(
                study_info['patient_id'],
                f"from Orthanc — {profile.name}"
            )
            self.workflowWidget.markDone(1, "Data Loaded (Orthanc)")
            
            # If centerline was pre-loaded, mark VMTK phase as done
            if profile.has_precalculated_centerline and self.logic.centerlineNode:
                self.workflowWidget.markDone(3, "Centerline Pre-loaded")
            
            self.workflowWidget.updateUIState(self.logic.workflowState.get("phase", 0))
            
            # Show loading errors if any
            if errors:
                slicer.util.warningDisplay(
                    "Some files could not be loaded:\n\n" + "\n".join(errors),
                    "Partial Load"
                )
            
            # Load existing annotations for reviewers
            if self.orthancWidget.getRole() == "reviewer":
                self._loadExistingAnnotations(study_id, self.orthancTempDir)
        
        except Exception as e:
            slicer.util.errorDisplay(f"Failed to load study data:\n{str(e)}")
            import traceback
            traceback.print_exc()

    def _loadExistingAnnotations(self, series_id: str, temp_dir: str):
        """Load existing annotations for review (series-level attachments)."""
        pid = self.logic.currentId

        # Refined mask
        refined_path = self.orthancClient.download_nifti_from_series(
            series_id,
            OrthancClient.ATTACHMENT_REFINED_MASK,
            os.path.join(temp_dir, f"{pid}_refined_mask.seg.nrrd"),
        )
        if refined_path:
            slicer.util.loadSegmentation(refined_path)

        # Zones
        zones_path = self.orthancClient.download_nifti_from_series(
            series_id,
            OrthancClient.ATTACHMENT_ZONES,
            os.path.join(temp_dir, f"{pid}_Zones.fcsv"),
        )
        if zones_path:
            slicer.util.loadMarkups(zones_path)

        # Centerline
        centerline_path = self.orthancClient.download_nifti_from_series(
            series_id,
            OrthancClient.ATTACHMENT_CENTERLINE,
            os.path.join(temp_dir, f"{pid}_Centerline.vtk"),
        )
        if centerline_path:
            slicer.util.loadModel(centerline_path)

    def onSubmitToOrthanc(self, study_id: str):
        """Handle annotation submission."""
        if not study_id:
            slicer.util.errorDisplay("No Orthanc study loaded")
            return
        
        # Validate segmentation exists
        if not self.logic.segNode:
            slicer.util.errorDisplay("No segmentation to submit. Please complete refinement first.")
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
            
            success, message = self.orthancClient.submit_annotation_on_series(study_id, files, notes)
            
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
            success, message = self.orthancClient.approve_annotation_on_series(study_id, comments)
            
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
            success, message = self.orthancClient.reject_annotation_on_series(study_id, reason)
            
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
        # Disable picker first (removes VTK observers from scene nodes before
        # the scene is cleared; skipping this step crashes VTK/Slicer).
        # Then delegate to resetApplication which does the correct teardown order.
        self.resetApplication()

    def resetForNextStudy(self):
        """Reset for next study."""
        # Disable picker before scene clear: removes VTK observers and MRML node
        # refs while nodes are still alive.  Skipping this leaves dangling state
        # that crashes Slicer when disable() is called later (e.g. on logout).
        self.centerlinePicker.cleanup()
        slicer.mrmlScene.Clear(0)
        self.logic.reset()
        self.workflowWidget.resetUI()
        self.workflowWidget.setOrthancMode(False)  # Reset Orthanc mode
        self.orthancWidget.resetForNextStudy()
        self.workflowWidget.setStatus("Ready for next study")

    # --- Workflow Handlers ---
    def onRefineSetup(self):
        self.workflowWidget.setButtonsEnabled(False)
        try:
            if self.logic.setupRefinement():
                self.workflowWidget.markDone(2, "Refine Mode")
                self.workflowWidget.updateUIState(self.logic.workflowState.get("phase", 0))
        finally:
            self.workflowWidget.setButtonsEnabled(True)

    def onVmtkSetup(self):
        # If centerline is already pre-loaded, skip VMTK
        if (self.logic.activeProfile
                and self.logic.activeProfile.has_precalculated_centerline
                and self.logic.centerlineNode):
            slicer.util.infoDisplay(
                "Centerline is pre-loaded for this dataset.\n"
                "VMTK extraction is not needed — proceed to zone placement."
            )
            return
        self.workflowWidget.setButtonsEnabled(False)
        try:
            if self.logic.setupVMTK():
                self.workflowWidget.markDone(3, "VMTK Ready")
                self.workflowWidget.updateUIState(self.logic.workflowState.get("phase", 0))
        finally:
            self.workflowWidget.setButtonsEnabled(True)

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

    def reset(self, clearScene=True):
        """Reset all state.
        
        Args:
            clearScene: If True, clears the MRML scene. Set to False if 
                        the caller will clear the scene separately.
        """
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
        self.activeProfile = None  # DatasetProfile for the loaded study
        self.hasUnsavedWork = False
        self.workflowState = {
            "phase": 0,
            "lastSave": None,
            "zoneCount": 0
        }
        if clearScene:
            slicer.mrmlScene.Clear(0)

    def getTimestamp(self):
        from datetime import datetime
        return datetime.now().strftime("%H:%M:%S")

    def applyConfiguredCtWindowLevel(self):
        """Apply default CT window/level values from config.py to the loaded CT volume."""
        if not self.volNode:
            return False

        displayNode = self.volNode.GetDisplayNode()
        if not displayNode:
            self.volNode.CreateDefaultDisplayNodes()
            displayNode = self.volNode.GetDisplayNode()
        if not displayNode:
            return False

        width = float(getattr(_cfg, "DEFAULT_CT_WINDOW_WIDTH", 1200))
        level = float(getattr(_cfg, "DEFAULT_CT_WINDOW_LEVEL", 350))
        displayNode.SetAutoWindowLevel(False)
        displayNode.SetWindowLevel(width, level)
        print(f"[Display] Applied CT window/level from config: W={width}, L={level}")
        return True

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
        """Load all data for a subject with automatic profile detection."""
        import numpy as np
        
        try:
            detectedId = self.getIdFromFiles(folderPath)
            
            if not detectedId:
                slicer.util.errorDisplay("Could not find 'ct_scan_*.nii.gz' in folder")
                return False
            
            # Auto-detect dataset profile from folder contents
            profile, found_files = detect_profile_from_folder(folderPath, detectedId)
            
            if profile is None:
                slicer.util.errorDisplay(
                    "Could not determine dataset type.\n\n"
                    "Expected at minimum:\n"
                    "  - ct_scan_{id}.nii.gz\n"
                    "  - {id}_unified_mask_smoothed.nii.gz\n\n"
                    "Optional (selects a richer profile):\n"
                    "  - {id}_merged_mask.nii.gz  → Dual Mask profile\n"
                    "  - a .vtp/.vtk centerline file  → Mask+Centerline profile"
                )
                return False
            
            print(f"[LoadData] Detected profile: {profile.name} for {detectedId}")
            
            # Validate required files exist
            missing = [name for name in profile.required_attachments
                       if name not in found_files]
            if missing:
                slicer.util.errorDisplay(
                    f"Missing required files for '{profile.name}' profile:\n"
                    f"  {', '.join(missing)}"
                )
                return False
            
            # Reset state, then set new ID/dir/profile
            self.reset()
            self.rootDir = folderPath
            self.currentId = detectedId
            self.activeProfile = profile
            
            # Load using profile-aware method
            errors = self.loadDataWithProfile(profile, found_files)
            if errors:
                slicer.util.warningDisplay(
                    "Data loaded with warnings:\n\n" + "\n".join(errors),
                    "Partial Load"
                )
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

            # Binarize mask before opening the editor (CT_AND_SEG_MASK profile)
            if getattr(self.activeProfile, 'binarize_mask_before_refine', False):
                self._binarizeSegmentationNode(self.segNode)

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

    def _binarizeSegmentationNode(self, segNode):
        """Merge all segments into a single binary segment (label 0/1)."""
        try:
            tempLabel = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLLabelMapVolumeNode", "_temp_binarize"
            )
            slicer.modules.segmentations.logic().ExportAllSegmentsToLabelmapNode(
                segNode, tempLabel, slicer.vtkSegmentation.EXTENT_REFERENCE_GEOMETRY
            )
            array = slicer.util.arrayFromVolume(tempLabel)
            array[array > 0] = 1
            slicer.util.updateVolumeFromArray(tempLabel, array)
            segNode.GetSegmentation().RemoveAllSegments()
            slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                tempLabel, segNode
            )
            slicer.mrmlScene.RemoveNode(tempLabel)
            segNode.CreateClosedSurfaceRepresentation()
            print("[Binarize] Segmentation mask binarized to single binary segment")
        except Exception as e:
            print(f"[Binarize] WARNING — failed to binarize mask: {e}")

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
                    polyData = self._applyFlowExtensions(polyData)
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
            parameterNode.SetNodeReferenceID("CenterlineModel", self.centerlineNode.GetID())
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

    def _applyFlowExtensions(self, surfacePolyData):
        """Apply VMTK flow extensions to extend the surface at open boundaries.

        This ensures that when auto-detect endpoints runs during centerline
        extraction, the detected endpoints reach all the way to the ends of
        the aortic surface.  Falls back to the original surface if VMTK
        computational-geometry classes are unavailable or if the filter fails.
        """
        try:
            import vtkvmtkComputationalGeometryPython as vtkvmtkCG
        except ImportError:
            print("[FlowExtensions] VMTK computational geometry not available, skipping")
            return surfacePolyData

        try:
            # Compute boundary reference systems for open boundary edges
            boundaryRefSys = vtkvmtkCG.vtkvmtkBoundaryReferenceSystems()
            boundaryRefSys.SetInputData(surfacePolyData)
            boundaryRefSys.SetBoundaryRadiusArrayName('BoundaryRadius')
            boundaryRefSys.SetBoundaryNormalsArrayName('BoundaryNormals')
            boundaryRefSys.SetPoint1ArrayName('Point1')
            boundaryRefSys.SetPoint2ArrayName('Point2')
            boundaryRefSys.Update()

            refSysOutput = boundaryRefSys.GetOutput()
            nBoundaries = refSysOutput.GetNumberOfPoints()
            if nBoundaries == 0:
                print("[FlowExtensions] No open boundaries detected, skipping")
                return surfacePolyData

            print(f"[FlowExtensions] {nBoundaries} open boundary(ies) found, applying extensions")

            # Extend surface at each open boundary using boundary normals
            flowExt = vtkvmtkCG.vtkvmtkFlowExtensionsFilter()
            flowExt.SetInputData(surfacePolyData)
            flowExt.SetCenterlines(refSysOutput)
            flowExt.SetExtensionModeToUseNormalToBoundary()
            flowExt.SetInterpolationModeToLinear()
            flowExt.SetExtensionRatio(2.0)          # extension length = 2x boundary radius
            flowExt.SetTransitionRatio(0.25)         # smooth blend over 25% of extension
            flowExt.SetAdaptiveExtensionLength(1)    # scale length to each boundary size
            flowExt.SetAdaptiveExtensionRadius(1)    # scale radius to each boundary size
            flowExt.SetNumberOfBoundaryPoints(50)    # circumferential resolution of extension
            flowExt.Update()

            result = flowExt.GetOutput()
            if result and result.GetNumberOfPoints() > 0:
                print(
                    f"[FlowExtensions] Surface extended: "
                    f"{surfacePolyData.GetNumberOfPoints()} -> {result.GetNumberOfPoints()} points"
                )
                return result

            print("[FlowExtensions] Filter returned empty output, using original surface")
            return surfacePolyData

        except Exception as e:
            print(f"[FlowExtensions] WARNING — failed to apply: {e}")
            import traceback
            traceback.print_exc()
            return surfacePolyData

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

    def addZonePoint(self, worldPos, zoneName=None):
        """Add a zone point.
        
        Args:
            worldPos: 3D position (x, y, z)
            zoneName: Optional label for the zone point
        
        Returns:
            Label string, or None if max zones reached
        """
        zoneNode = self.createZoneNode()
        n = zoneNode.GetNumberOfControlPoints()
        
        if n >= 10:
            return None
        
        zoneNode.AddControlPoint(worldPos[0], worldPos[1], worldPos[2])
        
        label = zoneName if zoneName else f"Zone_{n+1}"
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
    
    def getAortaSurfacePolyData(self):
        """Extract and return a vtkPolyData closed surface from the refined segmentation.

        Tries self.segNode first (refined mask), falls back to self.refNode.
        Returns vtkPolyData, or None if no segmentation is available.
        """
        import vtk
        for candidate in (self.segNode, self.refNode):
            if candidate is None:
                continue
            seg = candidate.GetSegmentation()
            if seg is None or seg.GetNumberOfSegments() == 0:
                continue
            candidate.CreateClosedSurfaceRepresentation()
            segmentId = seg.GetNthSegmentID(0)
            polyData = vtk.vtkPolyData()
            candidate.GetClosedSurfaceRepresentation(segmentId, polyData)
            if polyData.GetNumberOfPoints() > 0:
                return polyData
        print("[getAortaSurfacePolyData] No valid surface found in segNode or refNode")
        return None

    def loadProcedureDataFromPaths(self, ct_path: str, unified_path: str, merged_path: str):
        """
        Legacy: Load procedure data from explicit file paths.
        Delegates to loadDataWithProfile with a Dual Mask profile.
        """
        profile = PROFILES[DatasetProfile.DUAL_MASK]
        self.activeProfile = profile
        file_paths = {
            "ct": ct_path,
            "seg_mask": unified_path,
            "ref_mask": merged_path,
        }
        self.loadDataWithProfile(profile, file_paths)

    def loadDataWithProfile(self, profile, file_paths: dict):
        """
        Load data into the scene based on a DatasetProfile.

        This is the single, profile-aware entry point used by both Orthanc
        and manual-folder loading paths.  Each section is wrapped in its
        own try/except so a failure in one file does not prevent the rest
        from loading.

        Args:
            profile:    DatasetProfile instance.
            file_paths: dict of {logical_name: local_file_path}

        Returns:
            list of error strings (empty if everything loaded successfully).
        """
        import numpy as np

        errors = []
        slicer.mrmlScene.Clear(0)

        # --- CT volume (always required; supports NIfTI file or DICOM folder) ---
        ct_path = file_paths.get("ct")
        if not ct_path or not os.path.exists(ct_path):
            raise RuntimeError(f"CT scan path missing or not found: {ct_path}")
        try:
            print(f"[Loading] CT from: {ct_path}")
            self.volNode = self._loadCtVolume(ct_path)
            if not self.volNode:
                raise RuntimeError("Failed to load CT volume")
        except Exception as e:
            raise RuntimeError(f"Failed to load CT volume from {ct_path}: {e}")

        # --- Segmentation mask ---
        seg_path = file_paths.get("seg_mask")
        if seg_path and os.path.exists(seg_path):
            try:
                seg_path = self._fixFileExtension(seg_path)
                file_size = os.path.getsize(seg_path)
                print(f"[Loading] Segmentation mask from: {seg_path} "
                      f"(size={file_size} bytes)")
                if file_size == 0:
                    raise RuntimeError("Downloaded mask file is empty (0 bytes)")

                # If the file is a .seg.nrrd or .nrrd, load as segmentation
                # directly — it cannot be read by a NIfTI reader.
                if seg_path.endswith('.seg.nrrd') or seg_path.endswith('.nrrd'):
                    self.segNode = self._loadSegmentationFromNrrd(seg_path)
                    if not self.segNode:
                        raise RuntimeError(
                            "Failed to load NRRD segmentation mask")
                else:
                    # NIfTI path: label-map → segmentation
                    unifiedLabelNode = self._loadNiftiAsLabelMap(seg_path)
                    if not unifiedLabelNode:
                        raise RuntimeError(
                            "All loading strategies failed for segmentation mask")

                    self.segNode = slicer.mrmlScene.AddNewNodeByClass(
                        "vtkMRMLSegmentationNode",
                        f"{self.currentId}_Segmentation"
                    )
                    slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                        unifiedLabelNode, self.segNode
                    )
                    slicer.mrmlScene.RemoveNode(unifiedLabelNode)

                self.segNode.CreateClosedSurfaceRepresentation()
                print("[Loading] Segmentation mask loaded successfully")
            except Exception as e:
                msg = f"Segmentation mask: {e}"
                print(f"[Loading] ERROR — {msg}")
                errors.append(msg)
        elif seg_path:
            msg = f"Segmentation mask file not found: {seg_path}"
            print(f"[Loading] ERROR — {msg}")
            errors.append(msg)

        # --- Reference mask (optional — present in Dual Mask profile) ---
        ref_path = file_paths.get("ref_mask")
        if ref_path and os.path.exists(ref_path):
            try:
                ref_path = self._fixFileExtension(ref_path)
                print(f"[Loading] Reference mask from: {ref_path}")
                mergedLabelNode = self._loadNiftiAsLabelMap(ref_path)
                if mergedLabelNode:
                    self.refNode = slicer.mrmlScene.AddNewNodeByClass(
                        "vtkMRMLSegmentationNode",
                        f"{self.currentId}_Merged"
                    )
                    slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                        mergedLabelNode, self.refNode
                    )
                    slicer.mrmlScene.RemoveNode(mergedLabelNode)
                    self.refNode.CreateClosedSurfaceRepresentation()
                    self.refNode.GetDisplayNode().SetVisibility(False)
                    print("[Loading] Reference mask loaded successfully")

                # Create binarized version for centerline extraction
                self._createBinarizedMask(ref_path)
            except Exception as e:
                msg = f"Reference mask: {e}"
                print(f"[Loading] ERROR — {msg}")
                errors.append(msg)

        # --- Pre-computed centerline (Mask+Centerline profile) ---
        centerline_path = file_paths.get("centerline")
        if centerline_path and os.path.exists(centerline_path):
            try:
                self.centerlineNode = self._loadCenterlineModel(centerline_path)
            except Exception as e:
                msg = f"Centerline: {e}"
                print(f"[Loading] ERROR — {msg}")
                errors.append(msg)

        # --- Set visibility ---
        if self.segNode and self.segNode.GetDisplayNode():
            self.segNode.GetDisplayNode().SetVisibility(True)

        # --- Setup views ---
        self._setupViews()

        self.workflowState["phase"] = 1
        self.hasUnsavedWork = False

        print(f"[Loading] Complete — profile: {profile.name}")
        if errors:
            print(f"[Loading] Errors: {errors}")
        return errors

    def _loadCenterlineModel(self, centerline_path):
        """
        Load a centerline model from disk, handling both .vtk and .vtp formats.

        Slicer uses the file extension to choose a reader:
        .vtk -> legacy VTK, .vtp -> VTK XML PolyData.
        If the file was downloaded with the wrong extension the first attempt
        fails, so this helper retries with the alternate extension.

        Returns:
            Loaded model node, or None.
        """
        print(f"[Loading] Pre-computed centerline from: {centerline_path}")
        node = slicer.util.loadModel(centerline_path)

        # If initial load failed, try with the alternate extension
        if node is None:
            alt_ext = ".vtp" if centerline_path.endswith(".vtk") else ".vtk"
            alt_path = centerline_path.rsplit(".", 1)[0] + alt_ext
            print(f"[Loading] loadModel failed for {os.path.basename(centerline_path)}, "
                  f"retrying as {alt_ext}...")
            try:
                import shutil
                shutil.copy2(centerline_path, alt_path)
                node = slicer.util.loadModel(alt_path)
            except Exception as e2:
                print(f"[Loading] Retry with {alt_ext} also failed: {e2}")

        if node is None:
            print(f"[Loading] WARNING: Could not load centerline from {centerline_path}")
            return None

        node.SetName(f"{self.currentId}_Centerline")
        displayNode = node.GetDisplayNode()
        if displayNode:
            displayNode.SetVisibility(True)
            displayNode.SetColor(1.0, 1.0, 0.0)  # yellow
            displayNode.SetLineWidth(3)

        polyData = node.GetPolyData()
        npts = polyData.GetNumberOfPoints() if polyData else 0
        print(f"[Loading] Centerline loaded ({npts} pts)")
        return node

    def _createBinarizedMask(self, merged_path):
        """Create binarized mask for centerline extraction from merged mask path."""
        if not os.path.exists(merged_path):
            return

        merged_path = self._fixFileExtension(merged_path)
        tempMergedBinary = self._loadNiftiAsLabelMap(merged_path)
        if not tempMergedBinary:
            print("[Loading] WARNING — could not load merged mask for binarization")
            return
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

    def _loadCtVolume(self, ct_path):
        """
        Load CT from either a file path (NIfTI/NRRD) or a DICOM folder.

        Returns:
            vtkMRMLScalarVolumeNode on success, or None.
        """
        if os.path.isdir(ct_path):
            return self._loadDicomFromFolder(ct_path)
        return slicer.util.loadVolume(ct_path)

    def _loadDicomFromFolder(self, dicom_folder):
        """Import and load a DICOM study from a folder into the MRML scene."""
        try:
            from DICOMLib import DICOMUtils

            loaded_node_ids = []
            with DICOMUtils.TemporaryDICOMDatabase() as db:
                DICOMUtils.importDicom(dicom_folder, db)
                patient_uids = db.patients()
                for patient_uid in patient_uids:
                    loaded_node_ids.extend(DICOMUtils.loadPatientByUID(patient_uid))

            for node_id in loaded_node_ids:
                node = slicer.mrmlScene.GetNodeByID(node_id)
                if node and node.IsA("vtkMRMLScalarVolumeNode"):
                    print(f"[Loading] DICOM CT loaded from folder: {dicom_folder}")
                    return node

            # Fallback: first .dcm file as generic volume load
            for root, _, files in os.walk(dicom_folder):
                for name in files:
                    if name.lower().endswith(".dcm"):
                        dicom_file = os.path.join(root, name)
                        node = slicer.util.loadVolume(dicom_file)
                        if node:
                            print(f"[Loading] DICOM CT loaded via file fallback: {dicom_file}")
                            return node
            return None
        except Exception as e:
            print(f"[Loading] Failed to load DICOM CT from folder '{dicom_folder}': {e}")
            return None

    # -----------------------------------------------------------------
    # File-format loading helpers
    # -----------------------------------------------------------------

    def _loadSegmentationFromNrrd(self, filepath):
        """
        Load a .seg.nrrd or .nrrd file directly as a segmentation node.

        .seg.nrrd files are native Slicer segmentations — they contain
        segment metadata (names, colors, label values) that
        ``loadSegmentation`` reads directly into a
        ``vtkMRMLSegmentationNode``.

        Returns:
            vtkMRMLSegmentationNode on success, or None.
        """
        try:
            print(f"[Loading]   Loading NRRD segmentation: {os.path.basename(filepath)}")
            segNode = slicer.util.loadSegmentation(filepath)
            if segNode:
                segNode.SetName(f"{self.currentId}_Segmentation")
                print(f"[Loading]   loadSegmentation succeeded "
                      f"({segNode.GetSegmentation().GetNumberOfSegments()} segments)")
                return segNode
        except Exception as e1:
            print(f"[Loading]   loadSegmentation failed: {e1}")

        # Fallback: try loading as label map and importing
        try:
            print("[Loading]   Fallback: loading NRRD as label volume...")
            labelNode = slicer.util.loadLabelVolume(filepath)
            if labelNode:
                segNode = slicer.mrmlScene.AddNewNodeByClass(
                    "vtkMRMLSegmentationNode",
                    f"{self.currentId}_Segmentation"
                )
                slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                    labelNode, segNode
                )
                slicer.mrmlScene.RemoveNode(labelNode)
                print("[Loading]   NRRD label-volume fallback succeeded")
                return segNode
        except Exception as e2:
            print(f"[Loading]   NRRD label-volume fallback failed: {e2}")

        return None

    @staticmethod
    def _fixFileExtension(filepath):
        """
        Ensure the file extension matches the actual content.

        Handles:
        - .nii.gz file that is actually NRRD  → rename to .nrrd/.seg.nrrd
        - .nii.gz file that is uncompressed NIfTI → rename to .nii
        - Already-correct files → returned unchanged
        """
        if not filepath.endswith('.nii.gz'):
            return filepath
        try:
            with open(filepath, 'rb') as f:
                header = f.read(512)
            if len(header) < 4:
                return filepath

            # Valid gzip — nothing to fix
            if header[0] == 0x1f and header[1] == 0x8b:
                return filepath

            # NRRD format
            if header[:4] == b'NRRD':
                is_seg = b'Segment' in header
                new_path = filepath.replace('.nii.gz',
                                            '.seg.nrrd' if is_seg else '.nrrd')
                os.rename(filepath, new_path)
                print(f"[Loading] Renamed {os.path.basename(filepath)} → "
                      f"{os.path.basename(new_path)} (NRRD detected)")
                return new_path

            # Uncompressed NIfTI
            import struct
            hdr_size = struct.unpack('<i', header[:4])[0]
            if hdr_size in (348, 540):
                new_path = filepath[:-3]  # strip .gz
                os.rename(filepath, new_path)
                print(f"[Loading] Renamed {os.path.basename(filepath)} → "
                      f"{os.path.basename(new_path)} (raw NIfTI)")
                return new_path

            return filepath
        except Exception as e:
            print(f"[Loading] _fixFileExtension warning: {e}")
            return filepath

    @staticmethod
    def _loadNiftiAsLabelMap(filepath):
        """
        Robustly load a NIfTI mask file as a vtkMRMLLabelMapVolumeNode.

        Tries, in order:
          1. slicer.util.loadLabelVolume
          2. slicer.util.loadVolume  → convert to label map
          3. SimpleITK direct read   → push into label map node

        Returns:
            vtkMRMLLabelMapVolumeNode on success, or None.
        """
        import numpy as np

        # --- Strategy 1: native label-volume reader ---
        try:
            node = slicer.util.loadLabelVolume(filepath)
            if node:
                print("[Loading]   Strategy 1 (loadLabelVolume) succeeded")
                return node
        except Exception as e1:
            print(f"[Loading]   Strategy 1 (loadLabelVolume) failed: {e1}")

        # --- Strategy 2: generic volume reader + conversion ---
        try:
            tmpVol = slicer.util.loadVolume(filepath)
            if tmpVol:
                labelNode = slicer.mrmlScene.AddNewNodeByClass(
                    "vtkMRMLLabelMapVolumeNode", "tmp_label_conv"
                )
                volLogic = slicer.modules.volumes.logic()
                volLogic.CreateLabelVolumeFromVolume(
                    slicer.mrmlScene, labelNode, tmpVol
                )
                slicer.mrmlScene.RemoveNode(tmpVol)
                print("[Loading]   Strategy 2 (loadVolume + convert) succeeded")
                return labelNode
        except Exception as e2:
            print(f"[Loading]   Strategy 2 (loadVolume + convert) failed: {e2}")

        # --- Strategy 3: SimpleITK direct read → push to MRML ---
        try:
            import SimpleITK as sitk
            import sitkUtils

            print(f"[Loading]   Strategy 3: reading with SimpleITK...")
            sitkImage = sitk.ReadImage(filepath)

            # Cast to integer type if needed (label maps require int)
            pixel_type = sitkImage.GetPixelID()
            print(f"[Loading]   SimpleITK pixel type: {pixel_type} "
                  f"({sitkImage.GetPixelIDTypeAsString()})")
            if pixel_type in (sitk.sitkFloat32, sitk.sitkFloat64):
                sitkImage = sitk.Cast(sitkImage, sitk.sitkInt16)

            # Push into a Slicer volume node
            tempName = "tmp_sitk_label"
            sitkUtils.PushVolumeToSlicer(sitkImage, name=tempName,
                                         className='vtkMRMLLabelMapVolumeNode')
            labelNode = slicer.util.getNode(tempName)
            if labelNode:
                print("[Loading]   Strategy 3 (SimpleITK) succeeded")
                return labelNode
        except Exception as e3:
            print(f"[Loading]   Strategy 3 (SimpleITK) failed: {e3}")

        print("[Loading]   All loading strategies exhausted — returning None")
        return None


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