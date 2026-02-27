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
        self.parent.dependencies = ["SegmentEditor"]
        self.parent.contributors = ["University of Ottawa Heart Institute (Canada)"]
        self.parent.helpText = """
        TAA (Thoracic Aortic Aneurysm) Annotation Protocol Module.
        <br><br>
        This module provides a guided workflow for:
        <ul>
        <li>Loading CT scans, segmentation masks, and pre-computed centerlines from Orthanc PACS or local files</li>
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
        centerline_path = study_info.get('_centerline_path')
        self.orthancTempDir = study_info.get('_temp_dir')

        # Load using logic
        self.logic.currentId = study_info['patient_id']
        self.logic.rootDir = self.orthancTempDir
        self.logic.loadProcedureDataFromPaths(ct_path, unified_path, merged_path, centerline_path)

        # Update UI
        self.workflowWidget.setCurrentId(study_info['patient_id'], "from Orthanc")
        self.workflowWidget.markDone(1, "Data Loaded (Orthanc)")
        self.workflowWidget.updateUIState(self.logic.workflowState.get("phase", 0))

        # Auto-select centerline in combo box if loaded
        if self.logic.centerlineNode:
            self.workflowWidget.centerlineCombo.setCurrentNode(self.logic.centerlineNode)

        # Set Orthanc mode (disable local export)
        self.workflowWidget.setOrthancMode(True)

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

        # Download centerline (if not already loaded)
        if not self.logic.centerlineNode:
            centerline_path = self.orthancClient.download_nifti(
                study_id,
                OrthancClient.ATTACHMENT_CENTERLINE,
                os.path.join(temp_dir, f"{self.logic.currentId}_Centerline.vtp")
            )
            if centerline_path:
                self.logic.centerlineNode = slicer.util.loadModel(centerline_path)

    def onSubmitToOrthanc(self, study_id: str):
        """Handle annotation submission to Orthanc."""
        print(f"[onSubmitToOrthanc] Called with study_id: {study_id}")
        
        if not study_id:
            slicer.util.errorDisplay("No Orthanc study loaded")
            print("[onSubmitToOrthanc] ERROR: study_id is empty")
            return

        # Validate data is loaded
        if not self.logic.currentId:
            slicer.util.errorDisplay("No study data loaded")
            print("[onSubmitToOrthanc] ERROR: logic.currentId is empty")
            return

        if not self.logic.segNode:
            slicer.util.errorDisplay("No segmentation loaded")
            print("[onSubmitToOrthanc] ERROR: segNode is None")
            return

        # Validate zones
        zoneCount = self.logic.zoneNode.GetNumberOfControlPoints() if self.logic.zoneNode else 0
        print(f"[onSubmitToOrthanc] Zone count: {zoneCount}")
        
        if zoneCount < 10:
            if not slicer.util.confirmYesNoDisplay(
                f"Only {zoneCount}/10 zone landmarks placed. Submit anyway?",
                "Incomplete Zones"
            ):
                print("[onSubmitToOrthanc] User cancelled submission due to incomplete zones")
                return

        print("[onSubmitToOrthanc] Starting submission process...")
        
        # Show progress
        progressDialog = slicer.util.createProgressDialog(labelText="Submitting...", maximum=6)
        progressDialog.setWindowModality(qt.Qt.WindowModal)

        try:
            progressDialog.setValue(1)
            progressDialog.setLabelText("Preparing files...")
            slicer.app.processEvents()

            export_dir = tempfile.mkdtemp(prefix="orthanc_submit_")
            print(f"[onSubmitToOrthanc] Created temp export dir: {export_dir}")
            files = {}

            # Save segmentation
            if self.logic.segNode:
                path = os.path.join(export_dir, f"{self.logic.currentId}_refined_mask.seg.nrrd")
                slicer.util.saveNode(self.logic.segNode, path)
                if os.path.exists(path):
                    files["refined_mask"] = path
                    print(f"[onSubmitToOrthanc] Saved refined mask: {path}")
                else:
                    print(f"[onSubmitToOrthanc] WARNING: Failed to save refined mask to {path}")

            progressDialog.setValue(2)
            progressDialog.setLabelText("Saving centerline...")
            slicer.app.processEvents()

            # Save centerline as .vtp
            if self.logic.centerlineNode:
                path = os.path.join(export_dir, f"{self.logic.currentId}_Centerline.vtp")
                slicer.util.saveNode(self.logic.centerlineNode, path)
                if os.path.exists(path):
                    files["centerline"] = path
                    print(f"[onSubmitToOrthanc] Saved centerline: {path}")
                else:
                    print(f"[onSubmitToOrthanc] WARNING: Failed to save centerline to {path}")
            else:
                print("[onSubmitToOrthanc] WARNING: No centerline node found in logic")

            progressDialog.setValue(3)
            progressDialog.setLabelText("Saving endpoints...")
            slicer.app.processEvents()

            # Save endpoints
            if self.logic.endpointNode:
                path = os.path.join(export_dir, f"{self.logic.currentId}_Endpoints.fcsv")
                slicer.util.saveNode(self.logic.endpointNode, path)
                if os.path.exists(path):
                    files["endpoints"] = path
                    print(f"[onSubmitToOrthanc] Saved endpoints: {path}")
                else:
                    print(f"[onSubmitToOrthanc] WARNING: Failed to save endpoints to {path}")

            progressDialog.setValue(4)
            progressDialog.setLabelText("Saving zones...")
            slicer.app.processEvents()

            # Save zones - REQUIRED
            if self.logic.zoneNode and self.logic.zoneNode.GetNumberOfControlPoints() > 0:
                path = os.path.join(export_dir, f"{self.logic.currentId}_Zones.fcsv")
                slicer.util.saveNode(self.logic.zoneNode, path)
                if os.path.exists(path):
                    files["zones"] = path
                    print(f"[onSubmitToOrthanc] Saved zones: {path}")
                else:
                    print(f"[onSubmitToOrthanc] WARNING: Failed to save zones to {path}")
            else:
                print("[onSubmitToOrthanc] WARNING: No zones to save")

            progressDialog.setValue(5)
            progressDialog.setLabelText("Getting notes...")
            slicer.app.processEvents()

            notes = self.workflowWidget.getNotesText()
            print(f"[onSubmitToOrthanc] Notes length: {len(notes)}")

            progressDialog.setLabelText("Uploading to Orthanc...")
            progressDialog.setValue(5)
            slicer.app.processEvents()

            print(f"[onSubmitToOrthanc] Calling submit_annotation with {len(files)} files")
            success, message = self.orthancClient.submit_annotation(study_id, files, notes)
            print(f"[onSubmitToOrthanc] Submit result: success={success}, message={message}")

            progressDialog.close()

            if success:
                print("[onSubmitToOrthanc] Submission successful!")
                slicer.util.infoDisplay(f"✓ Annotation submitted!\n\n{message}")
                self.logic.hasUnsavedWork = False
                self.orthancWidget.markSubmitted()
                self.orthancWidget.refreshWorklist()
                # Reset for next study after successful submission
                print("[onSubmitToOrthanc] Resetting for next study...")
                self.resetForNextStudy()
                print("[onSubmitToOrthanc] Reset complete")
            else:
                print(f"[onSubmitToOrthanc] Submission failed: {message}")
                slicer.util.errorDisplay(f"Failed: {message}")

        except Exception as e:
            progressDialog.close()
            error_msg = f"Error: {str(e)}"
            print(f"[onSubmitToOrthanc] Exception: {error_msg}")
            import traceback
            traceback.print_exc()
            slicer.util.errorDisplay(error_msg)

    def _uploadAnnotationFiles(self, study_id: str):
        """Save annotation data to temp files and upload to Orthanc.

        Returns:
            Tuple of (success: bool, message: str)
        """
        export_dir = tempfile.mkdtemp(prefix="orthanc_upload_")
        uploaded = []

        # Save and upload segmentation
        if self.logic.segNode:
            path = os.path.join(export_dir, f"{self.logic.currentId}_refined_mask.seg.nrrd")
            slicer.util.saveNode(self.logic.segNode, path)
            if os.path.exists(path):
                if self.orthancClient.upload_nifti(study_id, path, OrthancClient.ATTACHMENT_REFINED_MASK):
                    uploaded.append("refined_mask")
                    print(f"[Upload] Uploaded refined mask")
                else:
                    return False, "Failed to upload refined mask"

        # Save and upload centerline
        if self.logic.centerlineNode:
            path = os.path.join(export_dir, f"{self.logic.currentId}_Centerline.vtp")
            slicer.util.saveNode(self.logic.centerlineNode, path)
            if os.path.exists(path):
                if self.orthancClient.upload_nifti(study_id, path, OrthancClient.ATTACHMENT_CENTERLINE):
                    uploaded.append("centerline")
                    print(f"[Upload] Uploaded centerline")
                else:
                    return False, "Failed to upload centerline"

        # Save and upload endpoints
        if self.logic.endpointNode:
            path = os.path.join(export_dir, f"{self.logic.currentId}_Endpoints.fcsv")
            slicer.util.saveNode(self.logic.endpointNode, path)
            if os.path.exists(path):
                if self.orthancClient.upload_nifti(study_id, path, OrthancClient.ATTACHMENT_ENDPOINTS):
                    uploaded.append("endpoints")
                    print(f"[Upload] Uploaded endpoints")
                else:
                    return False, "Failed to upload endpoints"

        # Save and upload zones
        if self.logic.zoneNode and self.logic.zoneNode.GetNumberOfControlPoints() > 0:
            path = os.path.join(export_dir, f"{self.logic.currentId}_Zones.fcsv")
            slicer.util.saveNode(self.logic.zoneNode, path)
            if os.path.exists(path):
                if self.orthancClient.upload_nifti(study_id, path, OrthancClient.ATTACHMENT_ZONES):
                    uploaded.append("zones")
                    print(f"[Upload] Uploaded zones ({self.logic.zoneNode.GetNumberOfControlPoints()} points)")
                else:
                    return False, "Failed to upload zones"

        # Upload notes
        notes = self.workflowWidget.getNotesText()
        if notes:
            try:
                self.orthancClient.session.put(
                    f"{self.orthancClient.server_url}/studies/{study_id}/attachments/{OrthancClient.ATTACHMENT_NOTES}",
                    data=notes.encode('utf-8'),
                    headers={"Content-Type": "text/plain"}
                )
                uploaded.append("notes")
            except Exception as e:
                print(f"[Upload] Warning: Failed to upload notes: {e}")

        return True, f"{len(uploaded)} files uploaded: {', '.join(uploaded)}"

    def onApproveAnnotation(self, study_id: str):
        """Handle annotation approval. For admin with local data, uploads files first."""
        print(f"[onApproveAnnotation] Called with study_id: {study_id}")

        if not slicer.util.confirmYesNoDisplay(
            "Approve this annotation as ground truth?",
            "Confirm Approval"
        ):
            print("[onApproveAnnotation] User cancelled approval")
            return

        try:
            # If there's local annotation data loaded (admin acting as
            # annotator+reviewer), upload annotation files before approving
            if self.logic.currentId:
                print("[onApproveAnnotation] Uploading annotation files before approval...")
                upload_ok, upload_msg = self._uploadAnnotationFiles(study_id)
                if not upload_ok:
                    slicer.util.errorDisplay(f"Failed to upload annotation files: {upload_msg}")
                    return
                print(f"[onApproveAnnotation] {upload_msg}")

            comments = self.orthancWidget.getReviewComments()
            print(f"[onApproveAnnotation] Approving with comments: {comments[:50]}...")
            success, message = self.orthancClient.approve_annotation(study_id, comments)
            print(f"[onApproveAnnotation] Result: success={success}, message={message}")

            if success:
                print("[onApproveAnnotation] Approval successful!")
                slicer.util.infoDisplay(f"✓ {message}")
                self.orthancWidget.disableReviewButtons()
                self.orthancWidget.refreshWorklist()
                print("[onApproveAnnotation] Resetting for next study...")
                self.resetForNextStudy()
                print("[onApproveAnnotation] Reset complete")
            else:
                print(f"[onApproveAnnotation] Approval failed: {message}")
                slicer.util.errorDisplay(f"Failed: {message}")
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            print(f"[onApproveAnnotation] Exception: {error_msg}")
            import traceback
            traceback.print_exc()
            slicer.util.errorDisplay(error_msg)

    def onRejectAnnotation(self, study_id: str, reason: str):
        """Handle annotation rejection."""
        print(f"[onRejectAnnotation] Called with study_id: {study_id}")
        
        if not slicer.util.confirmYesNoDisplay(
            f"Reject this annotation?\n\nReason: {reason[:100]}...",
            "Confirm Rejection"
        ):
            print("[onRejectAnnotation] User cancelled rejection")
            return

        try:
            print(f"[onRejectAnnotation] Rejecting with reason: {reason[:50]}...")
            success, message = self.orthancClient.reject_annotation(study_id, reason)
            print(f"[onRejectAnnotation] Result: success={success}, message={message}")

            if success:
                print("[onRejectAnnotation] Rejection successful!")
                slicer.util.infoDisplay(f"✓ {message}")
                self.orthancWidget.disableReviewButtons()
                self.orthancWidget.refreshWorklist()
                print("[onRejectAnnotation] Resetting for next study...")
                self.resetForNextStudy()
                print("[onRejectAnnotation] Reset complete")
            else:
                print(f"[onRejectAnnotation] Rejection failed: {message}")
                slicer.util.errorDisplay(f"Failed: {message}")
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            print(f"[onRejectAnnotation] Exception: {error_msg}")
            import traceback
            traceback.print_exc()
            slicer.util.errorDisplay(error_msg)

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
        print("[resetForNextStudy] Starting reset...")
        try:
            print("[resetForNextStudy] Disabling centerline picker...")
            if self.centerlinePicker:
                self.centerlinePicker.disable()
            
            print("[resetForNextStudy] Clearing MRML scene...")
            slicer.mrmlScene.Clear(0)
            
            print("[resetForNextStudy] Resetting logic...")
            self.logic.reset()
            
            print("[resetForNextStudy] Resetting workflow UI...")
            self.workflowWidget.resetUI()
            
            print("[resetForNextStudy] Disabling Orthanc mode...")
            self.workflowWidget.setOrthancMode(False)
            
            print("[resetForNextStudy] Updating UI state...")
            self.workflowWidget.updateUIState(0)
            
            print("[resetForNextStudy] Resetting Orthanc widget...")
            self.orthancWidget.resetForNextStudy()
            
            print("[resetForNextStudy] Setting status message...")
            self.workflowWidget.setStatus("Ready for next study")
            
            print("[resetForNextStudy] Reset complete!")
        except Exception as e:
            print(f"[resetForNextStudy] ERROR during reset: {str(e)}")
            import traceback
            traceback.print_exc()
            slicer.util.errorDisplay(f"Error during reset: {str(e)}")

    # --- Workflow Handlers ---
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
        """Load all data for a subject from local filesystem."""
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
            for path, name in [(volPath, "CT scan"), (segPath, "unified segmentation")]:
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

            # Load merged segmentation (optional)
            if os.path.exists(refPath):
                tempMerged = slicer.util.loadLabelVolume(refPath)
                self.refNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", f"{self.currentId}_merged")
                slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(tempMerged, self.refNode)
                slicer.mrmlScene.RemoveNode(tempMerged)
                self.refNode.CreateClosedSurfaceRepresentation()
                self.refNode.GetDisplayNode().SetVisibility(False)

            # Set visibility
            self.segNode.GetDisplayNode().SetVisibility(True)

            # Try to load a local centerline file if present
            import glob
            centerline_patterns = [
                os.path.join(folderPath, f"{self.currentId}_Centerline.vtp"),
                os.path.join(folderPath, f"{self.currentId}_centerline.vtp"),
            ]
            for cl_path in centerline_patterns:
                if os.path.exists(cl_path):
                    self.centerlineNode = slicer.util.loadModel(cl_path)
                    print(f"[Loading] Centerline loaded from: {cl_path}")
                    break

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

    def loadProcedureDataFromPaths(self, ct_path: str, unified_path: str,
                                    merged_path: str, centerline_path: str = None):
        """
        Load procedure data from explicit file paths (for Orthanc integration).

        Args:
            ct_path: Path to CT NIfTI file
            unified_path: Path to unified mask NIfTI file
            merged_path: Path to merged mask NIfTI file
            centerline_path: Path to pre-computed centerline .vtp file (optional)
        """
        slicer.mrmlScene.Clear(0)

        # Load CT volume
        print(f"[Loading] CT from: {ct_path}")
        self.volNode = slicer.util.loadVolume(ct_path)
        if not self.volNode:
            raise RuntimeError(f"Failed to load CT volume from {ct_path}")

        # Load unified segmentation (for editing)
        print(f"[Loading] Unified mask from: {unified_path}")
        self.segNode = slicer.util.loadSegmentation(unified_path)
        if not self.segNode:
            raise RuntimeError(f"Failed to load unified mask from {unified_path}")
        self.segNode.SetName(f"{self.currentId}_Segmentation")
        self.segNode.CreateClosedSurfaceRepresentation()

        # Load merged segmentation (reference, optional)
        if merged_path and os.path.exists(merged_path):
            print(f"[Loading] Merged mask from: {merged_path}")
            mergedLabelNode = slicer.util.loadLabelVolume(merged_path)
            if mergedLabelNode:
                self.refNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode",
                                                                        f"{self.currentId}_Merged")
                slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                    mergedLabelNode, self.refNode)
                slicer.mrmlScene.RemoveNode(mergedLabelNode)
                self.refNode.CreateClosedSurfaceRepresentation()
                self.refNode.GetDisplayNode().SetVisibility(False)

        # Load pre-computed centerline if provided
        if centerline_path and os.path.exists(centerline_path):
            print(f"[Loading] Centerline from: {centerline_path}")
            self.centerlineNode = slicer.util.loadModel(centerline_path)
            if self.centerlineNode:
                self.centerlineNode.SetName(f"{self.currentId}_Centerline")
                print(f"[Loading] Centerline loaded successfully")
            else:
                print(f"[Loading] WARNING: Failed to load centerline from {centerline_path}")

        # Setup views
        self._setupViews()

        self.workflowState["phase"] = 1
        self.hasUnsavedWork = False

        print(f"[Loading] Complete - ready for zone landmark placement")

    def _setupViews(self):
        """Configure 4-up view and background volume."""
        if self.volNode:
            slicer.app.layoutManager().sliceWidget('Red').sliceLogic().GetSliceCompositeNode().SetBackgroundVolumeID(self.volNode.GetID())
        slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)

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
        if isinstance(pointId, int) and pointId >= 0:
            label += f"_P{pointId}"
        elif isinstance(pointId, str):
            label = pointId
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
