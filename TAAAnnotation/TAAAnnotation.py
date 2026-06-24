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
from TAAAnnotationLib.DataLoader import DataLoader
from TAAAnnotationLib.RefinementLogic import RefinementLogic
from TAAAnnotationLib.ZoneManager import ZoneManager
import tempfile


class TAAAnnotation(ScriptedLoadableModule):
    """Main module class - defines metadata and help text"""

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = "TAA Annotation"
        # Ensure Python deps then check for updates — deferred so Slicer finishes
        # loading the module before any pip/network activity starts.
        qt.QTimer.singleShot(1000, self._ensureDependenciesThenUpdate)

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

    def _ensureDependenciesThenUpdate(self):
        """Install missing Python packages, then run the update check."""
        try:
            from TAAAnnotationLib.DependencyInstaller import ensureDependencies
            ensureDependencies()
        except Exception as e:
            print(f"[TAAAnnotation] Dependency install error: {e}")
        self._checkForUpdates()

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
        self._stagedCtDir = None  # set when CT is loaded in Phase 1; cleared in Phase 2

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
        self.orthancWidget.ctReadyForStaging.connect(self.onCtReadyForStaging)
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
    def onCtReadyForStaging(self, series_id: str, ct_dir: str, series_info: dict):
        """Phase 1 of staged DICOM load: CT downloaded — index + load CT immediately."""
        try:
            patient_id = series_info.get('patient_id', series_id)
            dicom_native_profile = PROFILES[DatasetProfile.DICOM_NATIVE]

            # Reset scene and logic state before loading new data.
            self.centerlinePicker.cleanup()
            self.logic.reset()
            self.logic.currentId = patient_id
            self.logic.rootDir = os.path.dirname(ct_dir)
            self.logic.activeProfile = dicom_native_profile

            slicer.util.showStatusMessage(f"Loading CT for {patient_id}…")
            slicer.app.processEvents()

            vol_node = self.logic._loadDicomNativeCtOnly(ct_dir)
            if vol_node:
                self.logic.volNode = vol_node
                self.logic.applyConfiguredCtWindowLevel()
                self._stagedCtDir = ct_dir
                self.workflowWidget.setCurrentId(patient_id, "loading segmentation…")
                self.workflowWidget.setStatus(
                    f"CT loaded for {patient_id} — segmentation downloading…"
                )
                slicer.util.showStatusMessage(
                    f"CT loaded — segmentation downloading…", 10000
                )
                print(f"[Orthanc] Staged Phase 1 complete: CT visible for {patient_id}")
            else:
                print(f"[Orthanc] Staged Phase 1 failed: CT not loaded for {patient_id}")
                self._stagedCtDir = None
        except Exception as e:
            print(f"[Orthanc] onCtReadyForStaging error: {e}")
            import traceback
            traceback.print_exc()
            self._stagedCtDir = None

    def _completeStagedDicomLoad(self, series_id: str, study_info: dict):
        """Phase 2 of staged DICOM load: CT is in scene — add SEG and finalize."""
        try:
            profile    = study_info.get('_profile')
            file_paths = study_info.get('_file_paths', {})
            self.orthancTempDir = study_info.get('_temp_dir')
            ct_dir = self._stagedCtDir
            self._stagedCtDir = None

            errors = []
            seg_path = file_paths.get('seg_mask')

            if seg_path and os.path.exists(seg_path):
                slicer.util.showStatusMessage("Loading segmentation mask…")
                slicer.app.processEvents()
                seg_node = self.logic._loadDicomSeg(seg_path, ct_dicom_dir=ct_dir)
                if seg_node:
                    self.logic.segNode = seg_node
                    seg_node.CreateClosedSurfaceRepresentation()
                    if seg_node.GetDisplayNode():
                        seg_node.GetDisplayNode().SetVisibility(True)
                    print("[Orthanc] Staged Phase 2: segmentation loaded")
                else:
                    errors.append("DICOM SEG segmentation could not be loaded")
            elif seg_path:
                errors.append(f"Segmentation file not found: {seg_path}")

            # Optional pre-computed centerline
            centerline_path = file_paths.get('centerline')
            if centerline_path and os.path.exists(centerline_path):
                try:
                    self.logic.centerlineNode = self.logic._loadCenterlineModel(centerline_path)
                except Exception as e:
                    errors.append(f"Centerline: {e}")

            self.logic.workflowState["phase"] = 1
            self.logic.hasUnsavedWork = False

            if profile:
                self.workflowWidget.setProfile(profile)
            self.workflowWidget.setCurrentId(
                study_info.get('patient_id', series_id),
                f"from Orthanc — {profile.name if profile else 'DICOM Native'}"
            )
            self.workflowWidget.markDone(1, "Data Loaded (Orthanc)")
            if profile and profile.has_precalculated_centerline and self.logic.centerlineNode:
                self.workflowWidget.markDone(3, "Centerline Pre-loaded")
            self.workflowWidget.updateUIState(self.logic.workflowState.get("phase", 0))

            if errors:
                slicer.util.warningDisplay(
                    "Some files could not be loaded:\n\n" + "\n".join(errors),
                    "Partial Load"
                )
            if self.orthancWidget.getRole() == "reviewer":
                self._loadExistingAnnotations(series_id, self.orthancTempDir)
        except Exception as e:
            slicer.util.errorDisplay(f"Failed to complete staged load:\n{str(e)}")
            import traceback
            traceback.print_exc()

    def onOrthancStudyLoaded(self, study_id: str, study_info: dict):
        """Handle study loaded from Orthanc (profile-aware)."""
        try:
            # DICOM_NATIVE staged path: CT already loaded in onCtReadyForStaging.
            # Only add SEG and finalize.
            if self._stagedCtDir is not None:
                self._completeStagedDicomLoad(study_id, study_info)
                return

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
            
            # loadDataWithProfile() is synchronous and blocks the main thread for
            # 1-2 minutes. We can't avoid the freeze without threading (MRML ops
            # must run on the main thread), but we can signal activity to the user.
            slicer.util.showStatusMessage(
                f"Loading {study_info['patient_id']} — "
                "CT → seg import → 3D surface (may take 1-2 min)…"
            )
            qt.QApplication.setOverrideCursor(qt.Qt.WaitCursor)
            slicer.app.processEvents()  # flush status + cursor before blocking

            # Load using logic
            self.logic.currentId = study_info['patient_id']
            self.logic.rootDir = self.orthancTempDir
            self.logic.activeProfile = profile
            try:
                errors = self.logic.loadDataWithProfile(profile, file_paths)
            finally:
                qt.QApplication.restoreOverrideCursor()
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
    """Orchestrates workflow state; delegates I/O and processing to sub-modules."""

    def __init__(self):
        ScriptedLoadableModuleLogic.__init__(self)
        # Sub-modules are created after reset() initialises the shared state
        # that they read from / write to via self._logic references.
        self._loader = DataLoader(self)
        self._refiner = RefinementLogic(self)
        self._zones = ZoneManager(self)
        self.reset()

    def reset(self, clearScene=True):
        """Reset all state.

        Args:
            clearScene: If True, clears the MRML scene.  Set to False when
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
        self.activeProfile = None
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
        """Apply CT window/level values from config.py to the loaded CT volume."""
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
        """Extract patient ID from the CT scan filename."""
        import glob
        searchPattern = os.path.join(folderPath, "ct_scan_*.nii.gz")
        foundFiles = glob.glob(searchPattern)
        if not foundFiles:
            return None
        filename = os.path.basename(foundFiles[0])
        return filename.replace("ct_scan_", "").replace(".nii.gz", "")

    # --- Loading (delegated to DataLoader) ---

    def loadData(self, folderPath):
        return self._loader.loadData(folderPath)

    def loadDataWithProfile(self, profile, file_paths: dict):
        return self._loader.loadDataWithProfile(profile, file_paths)

    def loadProcedureDataFromPaths(self, ct_path: str, unified_path: str, merged_path: str):
        """Legacy entry point — delegates to loadDataWithProfile with Dual Mask profile."""
        profile = PROFILES[DatasetProfile.DUAL_MASK]
        self.activeProfile = profile
        return self._loader.loadDataWithProfile(profile, {
            "ct": ct_path,
            "seg_mask": unified_path,
            "ref_mask": merged_path,
        })

    def _loadDicomNativeCtOnly(self, ct_dir: str):
        return self._loader._loadDicomNativeCtOnly(ct_dir)

    def _loadDicomSeg(self, path: str, ct_dicom_dir: str = None):
        return self._loader._loadDicomSeg(path, ct_dicom_dir)

    def _loadCenterlineModel(self, centerline_path):
        return self._loader._loadCenterlineModel(centerline_path)

    # --- Refinement & VMTK (delegated to RefinementLogic) ---

    def setupRefinement(self):
        return self._refiner.setupRefinement()

    def _binarizeSegmentationNode(self, segNode):
        return self._refiner._binarizeSegmentationNode(segNode)

    def setupVMTK(self):
        return self._refiner.setupVMTK()

    def getAortaSurfacePolyData(self):
        return self._refiner.getAortaSurfacePolyData()

    # --- Zone landmarks (delegated to ZoneManager) ---

    def createZoneNode(self):
        return self._zones.createZoneNode()

    def addZonePoint(self, worldPos, zoneName=None):
        return self._zones.addZonePoint(worldPos, zoneName)

    def undoLastZonePoint(self):
        return self._zones.undoLastZonePoint()

    def deleteZonePoint(self, index):
        return self._zones.deleteZonePoint(index)

    def renameZonePoint(self, index, newName):
        return self._zones.renameZonePoint(index, newName)

    def jumpToZonePoint(self, index):
        return self._zones.jumpToZonePoint(index)


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