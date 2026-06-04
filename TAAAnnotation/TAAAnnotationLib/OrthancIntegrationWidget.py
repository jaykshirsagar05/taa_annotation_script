"""
OrthancIntegrationWidget.py - Combined Orthanc integration UI.

Provides a unified widget that handles:
- Login panel
- Worklist display
- Submit/Review actions
"""

import os
import queue
import tempfile
import threading
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
    ctReadyForStaging = qt.Signal(str, str, dict)  # (series_id, ct_dir, series_info) - CT downloaded, load now
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
        
        # Clean up any existing worklist widget first
        if self.worklistWidget:
            try:
                self.worklistWidget.seriesSelected.disconnect()
            except (TypeError, RuntimeError):
                pass
            try:
                self.worklistWidget.logoutButton.clicked.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.worklistWidget.setParent(None)
            self.worklistWidget.deleteLater()
            self.worklistWidget = None
        
        # Clear worklist layout
        while self.worklistLayout.count():
            item = self.worklistLayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        # Create new worklist widget
        self.worklistWidget = OrthancWorklistWidget(self.orthancClient, role)
        self.worklistWidget.seriesSelected.connect(self._onSeriesSelected)
        self.worklistWidget.logoutButton.clicked.connect(self._onLogout)
        
        # Show offline-mode banner when AdminDashboard is unavailable
        if not self.orthancClient.dashboard_available:
            offlineBanner = qt.QFrame()
            offlineBanner.setStyleSheet(
                "background-color: #fff3cd; border: 1px solid #ffc107; "
                "border-radius: 4px; padding: 6px;"
            )
            bannerLayout = qt.QHBoxLayout(offlineBanner)
            bannerLayout.setContentsMargins(8, 4, 8, 4)
            bannerIcon = qt.QLabel("⚠️")
            bannerIcon.setStyleSheet("font-size: 16px;")
            bannerLayout.addWidget(bannerIcon)
            bannerText = qt.QLabel(
                "Operating in <b>Direct Orthanc</b> mode — AdminDashboard is offline. "
                "Worklist and status are read from Orthanc metadata directly."
            )
            bannerText.setWordWrap(True)
            bannerText.setStyleSheet("color: #856404; font-size: 11px;")
            bannerLayout.addWidget(bannerText, 1)
            self.worklistLayout.addWidget(offlineBanner)
        
        self.worklistLayout.addWidget(self.worklistWidget)
        
        # Configure action buttons based on role
        self._configureActionsForRole(role)
        
        # Show actions group
        self.actionsGroup.setVisible(True)
        
        # Switch to worklist page
        self.stackedWidget.setCurrentIndex(1)
        
        # Initial refresh
        self.worklistWidget.refreshWorklist()
        
        mode_label = "direct" if not self.orthancClient.dashboard_available else "dashboard"
        print(f"[Orthanc] Logged in as {self.orthancClient.current_user} with role: {role} ({mode_label} mode)")
        
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
        """Handle logout - defer to next event loop tick.
        
        Must NOT destroy worklistWidget synchronously here because this slot is
        called from logoutButton.clicked, which is owned by worklistWidget itself.
        Destroying the widget while its signal is still on the call stack crashes Qt.
        """
        qt.QTimer.singleShot(0, self._doLogout)

    def _doLogout(self):
        """Perform the actual logout and widget cleanup (deferred, safe to destroy widgets)."""
        self.orthancClient.logout()
        self.currentStudyId = None
        self.currentStudyInfo = None
        self.userRole = None

        # Safe to clean up now - logoutButton's signal has fully unwound
        if self.worklistWidget:
            try:
                self.worklistWidget.seriesSelected.disconnect()
            except (TypeError, RuntimeError):
                pass
            try:
                self.worklistWidget.logoutButton.clicked.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.worklistWidget.setParent(None)
            self.worklistWidget.deleteLater()
            self.worklistWidget = None

        # Clear remaining items in worklist layout (e.g. offline banner)
        while self.worklistLayout.count():
            item = self.worklistLayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Reset login widget
        self.loginWidget.loginButton.setEnabled(True)
        self.loginWidget.statusLabel.setText("")
        self.loginWidget.usernameEdit.setText("")
        self.loginWidget.passwordEdit.setText("")
        if self.loginWidget._directMode:
            self.loginWidget._toggleDirectMode()  # switch back to dashboard mode

        # Hide actions group and return to login page
        self.actionsGroup.setVisible(False)
        self.stackedWidget.setCurrentIndex(0)

        self.loggedOut.emit()
        
    def _onSeriesSelected(self, series_id: str, series_info: dict):
        """Handle CT series selection — profile-aware download with progress dialog.

        Downloads run in a background thread so the Slicer UI stays responsive.
        A Qt progress dialog shows per-instance progress for DICOM_NATIVE series
        (WADO-RS path) and a busy indicator for NIfTI attachment profiles.
        """
        self.currentStudyId = series_id
        self.currentStudyInfo = series_info

        try:
            slicer.util.showStatusMessage("Detecting dataset profile for series...")
            slicer.app.processEvents()

            profile = self.orthancClient.detect_dataset_profile_for_series(series_id)
            if profile is None:
                slicer.util.errorDisplay(
                    "Cannot determine dataset type for this series.\n\n"
                    "The series (or its parent study) must have at minimum a CT "
                    "scan and a segmentation mask attached."
                )
                return

            patient_id = series_info.get("patient_id", series_id)
            total_ct_instances = series_info.get("instance_count", 0)
            print(f"[Orthanc] Series {series_id} — profile: {profile.name}, "
                  f"patient: {patient_id}, instances: {total_ct_instances}")

            self.tempDir = tempfile.mkdtemp(prefix=f"orthanc_{patient_id}_")

            # ---- Progress dialog ----
            # DICOM_NATIVE: progress per CT instance (high granularity)
            # Other profiles: progress per attachment file (low granularity)
            is_dicom_native = profile.profile_type == "dicom_native"
            if is_dicom_native:
                progress_max = total_ct_instances if total_ct_instances > 0 else 0
            else:
                n_files = (len(profile.required_attachments)
                           + len(profile.optional_attachments))
                progress_max = n_files if n_files > 0 else 0
            progress = qt.QProgressDialog(
                f"Downloading {profile.name} from Orthanc…",
                None,          # no cancel button — downloads cannot safely abort mid-stream
                0, progress_max,
                self,
            )
            progress.setWindowTitle(f"Loading {patient_id}")
            progress.setWindowModality(qt.Qt.WindowModal)
            progress.setMinimumDuration(0)
            if progress_max == 0:
                progress.setMaximum(0)
            progress.setValue(0)
            progress.show()
            slicer.app.processEvents()

            # ---- Background download ----
            msg_queue = queue.Queue()

            def _progress_cb(n: int):
                msg_queue.put(('progress', n))

            def _ct_ready_cb(ct_dir: str):
                msg_queue.put(('ct_ready', ct_dir))

            def _do_download():
                try:
                    paths = self.orthancClient.download_files_for_series(
                        series_id, patient_id, profile, self.tempDir,
                        progress_callback=_progress_cb,
                        ct_ready_callback=_ct_ready_cb,
                    )
                    msg_queue.put(('done', paths))
                except Exception as exc:
                    msg_queue.put(('error', exc))

            dl_thread = threading.Thread(target=_do_download, daemon=True)
            dl_thread.start()

            # Spin the Qt event loop while the download runs so the UI stays
            # alive and the progress dialog updates.
            paths = None
            download_error = None
            while dl_thread.is_alive() or not msg_queue.empty():
                slicer.app.processEvents()
                # Drain all queued messages in one pass
                while True:
                    try:
                        msg = msg_queue.get_nowait()
                    except queue.Empty:
                        break
                    if msg[0] == 'progress':
                        n = msg[1]
                        if progress_max > 0:
                            progress.setValue(min(n, progress_max))
                        if is_dicom_native:
                            label = (f"Downloading {profile.name}…  "
                                     + (f"{n}/{total_ct_instances} slices"
                                        if total_ct_instances > 0 else f"{n} slices"))
                        else:
                            label = (f"Downloading {profile.name}…  "
                                     f"file {n}/{progress_max}")
                        progress.setLabelText(label)
                    elif msg[0] == 'ct_ready':
                        # CT download complete — start loading CT into scene
                        # immediately while SEG continues downloading.
                        ct_dir = msg[1]
                        if is_dicom_native:
                            ct_label = (f"CT downloaded ({total_ct_instances} slices) — "
                                        f"loading CT volume…")
                        else:
                            ct_label = "CT downloaded — loading CT volume…"
                        progress.setLabelText(ct_label)
                        if progress_max > 0:
                            progress.setValue(progress_max)
                        slicer.app.processEvents()
                        self.ctReadyForStaging.emit(series_id, ct_dir, dict(series_info))
                    elif msg[0] == 'done':
                        paths = msg[1]
                    elif msg[0] == 'error':
                        download_error = msg[1]
                qt.QThread.msleep(40)

            progress.close()
            slicer.app.processEvents()

            if download_error is not None:
                raise download_error

            if paths is None:
                slicer.util.errorDisplay("Download did not complete.")
                return

            # ---- Validate required files ----
            missing = [name for name in profile.required_attachments
                       if not paths.get(name)]
            if missing:
                labels = {
                    "ct": "CT scan",
                    "seg_mask": "segmentation mask",
                    "ref_mask": "reference (merged) mask",
                    "centerline": "pre-computed centerline",
                }
                missing_labels = [labels.get(m, m) for m in missing]
                slicer.util.errorDisplay(
                    f"Missing required files for '{profile.name}' profile:\n"
                    f"  {', '.join(missing_labels)}"
                )
                return

            self._configureActionsForLoadedSeries(series_info)

            # Enrich series_info with local file paths and profile
            series_info['_file_paths'] = paths
            series_info['_profile']    = profile
            series_info['_temp_dir']   = self.tempDir
            # Legacy keys expected by TAAAnnotationWidget.onOrthancStudyLoaded
            series_info['_ct_path']       = paths.get("ct")
            series_info['_unified_path']  = paths.get("seg_mask")
            series_info['_merged_path']   = paths.get("ref_mask")

            self.studyLoaded.emit(series_id, series_info)

            slicer.util.showStatusMessage(
                f"Loaded {patient_id} series from Orthanc [{profile.name}]", 3000
            )

        except Exception as e:
            slicer.util.errorDisplay(f"Failed to load series: {str(e)}")
            import traceback
            traceback.print_exc()
            
    def _onSubmit(self):
        """Submit annotation to Orthanc."""
        self.annotationSubmitted.emit(self.currentStudyId)

    def _configureActionsForLoadedSeries(self, series_info: dict):
        """Enable the actions that match the loaded series and current role."""
        self.btnSubmit.setEnabled(False)
        self.btnApprove.setEnabled(False)
        self.btnReject.setEnabled(False)

        status = (series_info or {}).get("annotation_status", "")

        if self.userRole == "annotator":
            self.btnSubmit.setEnabled(True)
        elif self.userRole == "admin":
            if status == AnnotationStatus.IN_PROGRESS.value:
                self.btnSubmit.setEnabled(True)
            elif status in (AnnotationStatus.ANNOTATED.value,
                            AnnotationStatus.IN_REVIEW.value):
                self.btnApprove.setEnabled(True)
                self.btnReject.setEnabled(True)
        else:
            self.btnApprove.setEnabled(True)
            self.btnReject.setEnabled(True)
        
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