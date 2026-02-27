"""
OrthancIntegrationWidget.py - Combined Orthanc integration UI.

Provides a unified widget that handles:
- Login panel
- Worklist display
- Submit/Review actions
"""

import os
import tempfile
import qt
import slicer
from typing import Optional, Callable
from .OrthancClient import OrthancClient, AnnotationStatus
from .OrthancWorklistWidget import OrthancLoginWidget, OrthancWorklistWidget


class OrthancIntegrationWidget(qt.QWidget):
    """
    Combined widget for Orthanc integration.
    
    Shows login initially, then worklist after authentication.
    Provides action buttons for annotation submission and review.
    """
    
    # Signals
    studyLoaded = qt.Signal(str, dict)  # (study_id, study_info) - emitted when data is loaded
    annotationSubmitted = qt.Signal(str)  # study_id
    annotationApproved = qt.Signal(str)  # study_id
    annotationRejected = qt.Signal(str, str)  # (study_id, reason)
    loggedOut = qt.Signal()
    
    def __init__(self, orthanc_client: OrthancClient, parent=None):
        super().__init__(parent)
        self.orthancClient = orthanc_client
        self.userRole = None
        self.currentStudyId = None
        self.currentStudyInfo = None
        self.tempDir = None
        
        # Child widgets
        self.loginWidget = None
        self.worklistWidget = None
        
        self._setupUI()
        
    def _setupUI(self):
        layout = qt.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # --- Header ---
        headerFrame = qt.QFrame()
        headerFrame.setStyleSheet("background-color: #6f42c1; padding: 8px; border-radius: 5px;")
        headerLayout = qt.QHBoxLayout(headerFrame)
        
        orthancIcon = qt.QLabel("🏥")
        orthancIcon.setStyleSheet("font-size: 18px;")
        headerLayout.addWidget(orthancIcon)
        
        orthancTitle = qt.QLabel("Orthanc Server")
        orthancTitle.setStyleSheet("color: white; font-weight: bold; font-size: 14px;")
        headerLayout.addWidget(orthancTitle)
        headerLayout.addStretch()
        
        layout.addWidget(headerFrame)
        
        # --- Stacked widget for login/worklist ---
        self.stackedWidget = qt.QStackedWidget()
        
        # Page 0: Login
        self.loginWidget = OrthancLoginWidget(
            self.orthancClient,
            on_login_success=self._onLoginSuccess
        )
        self.stackedWidget.addWidget(self.loginWidget)
        
        # Page 1: Worklist container (placeholder - populated on login)
        self.worklistContainer = qt.QWidget()
        self.worklistLayout = qt.QVBoxLayout(self.worklistContainer)
        self.worklistLayout.setContentsMargins(0, 0, 0, 0)
        self.stackedWidget.addWidget(self.worklistContainer)
        
        layout.addWidget(self.stackedWidget)
        
        # --- Orthanc Actions Group (hidden until study loaded) ---
        self.actionsGroup = qt.QGroupBox("Orthanc Actions")
        actionsLayout = qt.QVBoxLayout(self.actionsGroup)
        
        # Submit button (annotators)
        self.btnSubmit = qt.QPushButton("📤 Submit Annotation to Orthanc")
        self.btnSubmit.setStyleSheet(
            "background-color: #6f42c1; color: white; font-weight: bold; padding: 10px;"
        )
        self.btnSubmit.setEnabled(False)
        self.btnSubmit.setToolTip("Submit completed annotation to Orthanc server for review")
        self.btnSubmit.clicked.connect(self._onSubmit)
        actionsLayout.addWidget(self.btnSubmit)
        
        # Review buttons (reviewers)
        reviewLayout = qt.QHBoxLayout()
        
        self.btnApprove = qt.QPushButton("✅ Approve as Ground Truth")
        self.btnApprove.setStyleSheet(
            "background-color: #28a745; color: white; font-weight: bold; padding: 8px;"
        )
        self.btnApprove.setEnabled(False)
        self.btnApprove.setToolTip("Approve this annotation as final ground truth")
        self.btnApprove.clicked.connect(self._onApprove)
        reviewLayout.addWidget(self.btnApprove)
        
        self.btnReject = qt.QPushButton("❌ Reject")
        self.btnReject.setStyleSheet(
            "background-color: #dc3545; color: white; font-weight: bold; padding: 8px;"
        )
        self.btnReject.setEnabled(False)
        self.btnReject.setToolTip("Reject and return to annotator with feedback")
        self.btnReject.clicked.connect(self._onReject)
        reviewLayout.addWidget(self.btnReject)
        
        actionsLayout.addLayout(reviewLayout)
        
        # Review comments
        self.reviewCommentsEdit = qt.QPlainTextEdit()
        self.reviewCommentsEdit.setPlaceholderText("Review comments (required for rejection)...")
        self.reviewCommentsEdit.setMaximumHeight(60)
        self.reviewCommentsEdit.setVisible(False)
        actionsLayout.addWidget(self.reviewCommentsEdit)
        
        self.actionsGroup.setVisible(False)
        layout.addWidget(self.actionsGroup)
        
        # Start on login page
        self.stackedWidget.setCurrentIndex(0)
        
    def _onLoginSuccess(self, role: str):
        """Handle successful login."""
        self.userRole = role
        
        # Create worklist widget
        if self.worklistWidget:
            self.worklistWidget.setParent(None)
            self.worklistWidget.deleteLater()
            
        self.worklistWidget = OrthancWorklistWidget(self.orthancClient, role)
        self.worklistWidget.studySelected.connect(self._onStudySelected)
        self.worklistWidget.logoutButton.clicked.connect(self._onLogout)
        
        # Clear and add to container
        while self.worklistLayout.count():
            item = self.worklistLayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.worklistLayout.addWidget(self.worklistWidget)
        
        # Configure action buttons based on role
        self._configureActionsForRole(role)
        
        # Show actions group
        self.actionsGroup.setVisible(True)
        
        # Switch to worklist page
        self.stackedWidget.setCurrentIndex(1)
        
        # Initial refresh
        self.worklistWidget.refreshWorklist()
        
        print(f"[Orthanc] Logged in as {self.orthancClient.current_user} with role: {role}")
        
    def _configureActionsForRole(self, role: str):
        """Configure action buttons based on role."""
        if role == "annotator":
            self.btnSubmit.setVisible(True)
            self.btnApprove.setVisible(False)
            self.btnReject.setVisible(False)
            self.reviewCommentsEdit.setVisible(False)
        elif role == "admin":
            # Admin can do both submit and review
            self.btnSubmit.setVisible(True)
            self.btnApprove.setVisible(True)
            self.btnReject.setVisible(True)
            self.reviewCommentsEdit.setVisible(True)
        else:  # reviewer
            self.btnSubmit.setVisible(False)
            self.btnApprove.setVisible(True)
            self.btnReject.setVisible(True)
            self.reviewCommentsEdit.setVisible(True)
            
    def _onLogout(self):
        """Handle logout."""
        self.orthancClient.logout()
        self.currentStudyId = None
        self.currentStudyInfo = None
        self.userRole = None
        
        # Reset login widget
        self.loginWidget.loginButton.setEnabled(True)
        self.loginWidget.statusLabel.setText("")
        
        # Hide actions
        self.actionsGroup.setVisible(False)
        
        # Switch to login page
        self.stackedWidget.setCurrentIndex(0)
        
        self.loggedOut.emit()
        
    def _onStudySelected(self, study_id: str, study_info: dict):
        """Handle study selection from worklist."""
        self.currentStudyId = study_id
        self.currentStudyInfo = study_info

        try:
            slicer.util.showStatusMessage("Downloading data from Orthanc...")
            slicer.app.processEvents()

            # Create temp directory
            self.tempDir = tempfile.mkdtemp(prefix=f"orthanc_{study_info['patient_id']}_")

            # Download files
            ct_path = self.orthancClient.download_nifti(
                study_id,
                OrthancClient.ATTACHMENT_CT_NIFTI,
                os.path.join(self.tempDir, f"ct_scan_{study_info['patient_id']}.nii.gz")
            )

            unified_path = self.orthancClient.download_nifti(
                study_id,
                OrthancClient.ATTACHMENT_UNIFIED_MASK,
                os.path.join(self.tempDir, f"{study_info['patient_id']}_unified_mask.seg.nrrd")
            )

            merged_path = self.orthancClient.download_nifti(
                study_id,
                OrthancClient.ATTACHMENT_MERGED_MASK,
                os.path.join(self.tempDir, f"{study_info['patient_id']}_merged.nii.gz")
            )

            # Download pre-computed centerline (.vtp)
            centerline_path = self.orthancClient.download_nifti(
                study_id,
                OrthancClient.ATTACHMENT_CENTERLINE,
                os.path.join(self.tempDir, f"{study_info['patient_id']}_Centerline.vtp")
            )

            if not all([ct_path, unified_path]):
                missing = []
                if not ct_path: missing.append("CT scan")
                if not unified_path: missing.append("unified mask")
                slicer.util.errorDisplay(f"Missing required files: {', '.join(missing)}")
                return

            # Enable action buttons based on role
            if self.userRole in ("annotator", "admin"):
                self.btnSubmit.setEnabled(True)
            if self.userRole in ("reviewer", "admin"):
                self.btnApprove.setEnabled(True)
                self.btnReject.setEnabled(True)

            # Emit signal with paths
            study_info['_ct_path'] = ct_path
            study_info['_unified_path'] = unified_path
            study_info['_merged_path'] = merged_path
            study_info['_centerline_path'] = centerline_path
            study_info['_temp_dir'] = self.tempDir

            self.studyLoaded.emit(study_id, study_info)

            slicer.util.showStatusMessage(f"Loaded {study_info['patient_id']} from Orthanc", 3000)

        except Exception as e:
            slicer.util.errorDisplay(f"Failed to load study: {str(e)}")
            import traceback
            traceback.print_exc()
            
    def _onSubmit(self):
        """Submit annotation to Orthanc."""
        print("[Button] Submit button clicked")
        if not self.currentStudyId:
            slicer.util.errorDisplay("No study loaded")
            print("[Button] ERROR: No study loaded")
            return
        print(f"[Button] Emitting annotationSubmitted signal with study_id: {self.currentStudyId}")
        self.annotationSubmitted.emit(self.currentStudyId)
        print("[Button] Signal emitted")
        
    def _onApprove(self):
        """Approve annotation."""
        self.annotationApproved.emit(self.currentStudyId)
        
    def _onReject(self):
        """Reject annotation."""
        reason = self.reviewCommentsEdit.toPlainText().strip()
        if not reason:
            slicer.util.errorDisplay(
                "Please provide a reason for rejection in the comments field."
            )
            self.reviewCommentsEdit.setFocus()
            return
        self.annotationRejected.emit(self.currentStudyId, reason)
        
    # --- Public Methods ---
    
    def refreshWorklist(self):
        """Refresh the worklist."""
        if self.worklistWidget:
            self.worklistWidget.refreshWorklist()
            
    def resetForNextStudy(self):
        """Reset UI for next study."""
        self.currentStudyId = None
        self.currentStudyInfo = None
        
        # Reset buttons
        self.btnSubmit.setEnabled(False)
        self.btnSubmit.setText("📤 Submit Annotation to Orthanc")
        self.btnSubmit.setStyleSheet(
            "background-color: #6f42c1; color: white; font-weight: bold; padding: 10px;"
        )
        self.btnApprove.setEnabled(False)
        self.btnReject.setEnabled(False)
        self.reviewCommentsEdit.setPlainText("")
        
    def markSubmitted(self):
        """Mark submission as complete."""
        self.btnSubmit.setEnabled(False)
        self.btnSubmit.setText("✓ Submitted")
        self.btnSubmit.setStyleSheet("background-color: #6c757d; color: white; padding: 10px;")
        
    def disableReviewButtons(self):
        """Disable review buttons after action."""
        self.btnApprove.setEnabled(False)
        self.btnReject.setEnabled(False)
        
    def isLoggedIn(self) -> bool:
        """Check if user is logged in."""
        return self.userRole is not None
        
    def getRole(self) -> Optional[str]:
        """Get current user role."""
        return self.userRole
        
    def getCurrentStudyId(self) -> Optional[str]:
        """Get current study ID."""
        return self.currentStudyId
        
    def getReviewComments(self) -> str:
        """Get review comments."""
        return self.reviewCommentsEdit.toPlainText().strip()