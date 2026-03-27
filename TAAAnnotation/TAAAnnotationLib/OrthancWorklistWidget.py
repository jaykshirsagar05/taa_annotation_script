"""
OrthancWorklistWidget.py - UI components for Orthanc integration.

Provides:
- Login panel
- Worklist table with status filtering
- Study selection and claiming
"""

import qt
import slicer
from typing import Optional, Callable
from .OrthancClient import OrthancClient, AnnotationStatus
from . import config as _cfg


class OrthancLoginWidget(qt.QWidget):
    """Login panel for Orthanc authentication via AdminDashboard.

    When AdminDashboard is unreachable the widget automatically offers a
    *direct-login* mode that uses Orthanc basic-auth credentials and a
    locally-chosen role so the annotation workflow is never blocked.
    """
    
    def __init__(self, orthanc_client: OrthancClient, 
                 on_login_success: Optional[Callable] = None,
                 parent=None):
        super().__init__(parent)
        self.orthancClient = orthanc_client
        self.onLoginSuccess = on_login_success
        self._directMode = False  # True when user toggles "Direct Orthanc Login"
        self.setupUI()
        
    def setupUI(self):
        layout = qt.QVBoxLayout(self)

        # Credentials group
        credGroup = qt.QGroupBox("Credentials")
        credLayout = qt.QFormLayout(credGroup)

        self.usernameEdit = qt.QLineEdit()
        self.usernameEdit.setPlaceholderText("Username")
        credLayout.addRow("Username:", self.usernameEdit)

        self.passwordLabel = qt.QLabel("Password:")
        self.passwordEdit = qt.QLineEdit()
        self.passwordEdit.setPlaceholderText("Password")
        self.passwordEdit.setEchoMode(qt.QLineEdit.Password)
        credLayout.addRow(self.passwordLabel, self.passwordEdit)

        layout.addWidget(credGroup)

        # Role note (shown in normal mode)
        self.roleNote = qt.QLabel("Note: Your role is assigned by the administrator")
        self.roleNote.setStyleSheet("color: gray; font-style: italic; font-size: 11px;")
        layout.addWidget(self.roleNote)

        # --- Direct mode controls (hidden by default) ---
        self.directModeGroup = qt.QGroupBox("Role Selection  (Dashboard Offline)")
        self.directModeGroup.setStyleSheet(
            "QGroupBox { color: #856404; border: 1px solid #ffc107; "
            "border-radius: 4px; margin-top: 6px; padding-top: 14px; }"
            "QGroupBox::title { background-color: #fff3cd; padding: 2px 6px; }"
        )
        directLayout = qt.QFormLayout(self.directModeGroup)

        self.roleCombo = qt.QComboBox()
        self.roleCombo.addItems(["annotator", "reviewer", "admin"])
        directLayout.addRow("Role:", self.roleCombo)

        directHint = qt.QLabel(
            "AdminDashboard is unreachable. Enter your username, select your\n"
            "role, and log in. Orthanc credentials are loaded from config."
        )
        directHint.setStyleSheet("color: #856404; font-size: 11px;")
        directHint.setWordWrap(True)
        directLayout.addRow(directHint)

        self.directModeGroup.setVisible(False)
        layout.addWidget(self.directModeGroup)

        # Toggle link to switch to direct mode manually
        self.directModeToggle = qt.QPushButton("Dashboard down? Login directly to Orthanc →")
        self.directModeToggle.setFlat(True)
        self.directModeToggle.setStyleSheet(
            "color: #0366d6; text-decoration: underline; font-size: 11px; "
            "border: none; padding: 2px; text-align: left;"
        )
        self.directModeToggle.setCursor(qt.Qt.PointingHandCursor)
        self.directModeToggle.clicked.connect(self._toggleDirectMode)
        layout.addWidget(self.directModeToggle)

        # Login button
        self.loginButton = qt.QPushButton("Login")
        self.loginButton.clicked.connect(self.onLogin)
        layout.addWidget(self.loginButton)

        # Status label
        self.statusLabel = qt.QLabel("")
        self.statusLabel.setWordWrap(True)
        layout.addWidget(self.statusLabel)

        layout.addStretch()

        # Enable enter key to submit in dashboard mode
        self.passwordEdit.returnPressed.connect(self.onLogin)

    # ----- direct-mode helpers ------------------------------------------------

    def _toggleDirectMode(self):
        """Toggle between AdminDashboard and direct Orthanc login."""
        self._directMode = not self._directMode
        self.directModeGroup.setVisible(self._directMode)
        self.roleNote.setVisible(not self._directMode)
        # Password field is only needed for dashboard authentication
        self.passwordLabel.setVisible(not self._directMode)
        self.passwordEdit.setVisible(not self._directMode)
        self.statusLabel.setText("")
        self.loginButton.setEnabled(True)
        if self._directMode:
            self.directModeToggle.setText("← Back to AdminDashboard login")
            self.loginButton.setText("Login to Orthanc (Direct)")
        else:
            self.directModeToggle.setText("Dashboard down? Login directly to Orthanc →")
            self.loginButton.setText("Login")

    def _activateDirectMode(self):
        """Activate direct-login mode (called automatically on dashboard failure)."""
        if not self._directMode:
            self._directMode = True
            self.directModeGroup.setVisible(True)
            self.roleNote.setVisible(False)
            self.passwordLabel.setVisible(False)
            self.passwordEdit.setVisible(False)
            self.directModeToggle.setText("← Back to AdminDashboard login")
            self.loginButton.setText("Login to Orthanc (Direct)")

    # --------------------------------------------------------------------------

    def onLogin(self):
        """Handle login button click."""
        username = self.usernameEdit.text.strip()

        if self._directMode:
            # Direct mode: only username is required; Orthanc password from config
            if not username:
                self.statusLabel.setText("Please enter your username")
                self.statusLabel.setStyleSheet("color: orange;")
                return

            self.loginButton.setEnabled(False)
            self.statusLabel.setText("Connecting to Orthanc directly...")
            self.statusLabel.setStyleSheet("color: gray;")

            role = self.roleCombo.currentText
            success, message = self.orthancClient.login_direct(
                username, _cfg.ORTHANC_PASSWORD, role
            )

            if success:
                self.statusLabel.setText(f"✓ {message}")
                self.statusLabel.setStyleSheet("color: #28a745;")
                if self.onLoginSuccess:
                    self.onLoginSuccess(role)
            else:
                self.statusLabel.setText(f"✗ {message}")
                self.statusLabel.setStyleSheet("color: red;")
                self.loginButton.setEnabled(True)
        else:
            # Dashboard mode: both username and password are required
            password = self.passwordEdit.text
            if not username or not password:
                self.statusLabel.setText("Please enter username and password")
                self.statusLabel.setStyleSheet("color: orange;")
                return

            self.loginButton.setEnabled(False)
            self.statusLabel.setText("Connecting to AdminDashboard...")
            self.statusLabel.setStyleSheet("color: gray;")

            success, message = self.orthancClient.login(username, password)
            
            if success:
                role = self.orthancClient.get_role()
                self.statusLabel.setText(f"✓ {message}")
                self.statusLabel.setStyleSheet("color: green;")
                if self.onLoginSuccess:
                    self.onLoginSuccess(role)
            else:
                self.statusLabel.setText(f"✗ {message}")
                self.statusLabel.setStyleSheet("color: red;")
                self.loginButton.setEnabled(True)
                
                # If dashboard is unreachable, auto-switch to direct mode
                if "Cannot connect to AdminDashboard" in message or "timed out" in message.lower():
                    self._activateDirectMode()
                    self.statusLabel.setText(
                        f"✗ {message}\n\n"
                        "Switched to Direct Orthanc Login mode. "
                        "Select your role and try again."
                    )
                    self.statusLabel.setStyleSheet("color: #856404;")
    
    def getSelectedRole(self) -> str:
        """Get the authenticated user's role (from AdminDashboard or manual)."""
        return self.orthancClient.get_role() or "annotator"


class OrthancWorklistWidget(qt.QWidget):
    """Worklist widget showing available studies."""
    
    # Signal emitted when a CT series is selected for annotation
    seriesSelected = qt.Signal(str, dict)  # (series_id, series_info)
    
    def __init__(self, orthanc_client: OrthancClient, role: str = "annotator", parent=None):
        super().__init__(parent)
        self.orthancClient = orthanc_client
        self.role = role
        self.currentSeries = []
        self.setupUI()
        
    def setupUI(self):
        layout = qt.QVBoxLayout(self)
        
        # Header with user info and logout
        headerLayout = qt.QHBoxLayout()
        
        self.userLabel = qt.QLabel(f"Logged in as: {self.orthancClient.current_user} ({self.role})")
        self.userLabel.setStyleSheet("font-weight: bold;")
        headerLayout.addWidget(self.userLabel)
        
        headerLayout.addStretch()
        
        self.logoutButton = qt.QPushButton("Logout")
        self.logoutButton.setMaximumWidth(80)
        headerLayout.addWidget(self.logoutButton)
        
        layout.addLayout(headerLayout)
        
        # Filter options
        filterLayout = qt.QHBoxLayout()
        
        filterLayout.addWidget(qt.QLabel("Filter:"))
        
        self.filterCombo = qt.QComboBox()
        if self.role == "annotator":
            self.filterCombo.addItems(["My Worklist", "All Pending", "My In Progress", "Rejected"])
        elif self.role == "admin":
            self.filterCombo.addItems(["All Series", "Pending", "In Progress", "Annotated", "In Review", "Ground Truth"])
        else:  # reviewer
            self.filterCombo.addItems(["My Worklist", "Awaiting Review", "My In Review", "Ground Truth"])
        self.filterCombo.currentIndexChanged.connect(self.refreshWorklist)
        filterLayout.addWidget(self.filterCombo)
        
        filterLayout.addStretch()
        
        self.refreshButton = qt.QPushButton("Refresh")
        self.refreshButton.clicked.connect(self.refreshWorklist)
        filterLayout.addWidget(self.refreshButton)
        
        layout.addLayout(filterLayout)
        
        # Statistics bar
        self.statsLabel = qt.QLabel("")
        self.statsLabel.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.statsLabel)
        
        # Worklist table
        self.worklistTable = qt.QTableWidget()
        self.worklistTable.setColumnCount(8)
        self.worklistTable.setHorizontalHeaderLabels([
            "Series Description", "Patient ID", "Date",
            "Series #", "Slices", "Status", "Annotator", "Reviewer"
        ])
        self.worklistTable.setSelectionBehavior(qt.QAbstractItemView.SelectRows)
        self.worklistTable.setSelectionMode(qt.QAbstractItemView.SingleSelection)
        self.worklistTable.horizontalHeader().setStretchLastSection(True)
        self.worklistTable.setAlternatingRowColors(True)
        self.worklistTable.doubleClicked.connect(self.onSeriesDoubleClicked)
        
        layout.addWidget(self.worklistTable)
        
        # Action buttons
        actionLayout = qt.QHBoxLayout()
        
        self.claimButton = qt.QPushButton("Claim Selected")
        self.claimButton.clicked.connect(self.onClaimStudy)
        self.claimButton.setEnabled(False)
        actionLayout.addWidget(self.claimButton)
        
        self.loadButton = qt.QPushButton("Load && Start")
        self.loadButton.clicked.connect(self.onLoadStudy)
        self.loadButton.setEnabled(False)
        actionLayout.addWidget(self.loadButton)
        
        actionLayout.addStretch()
        
        self.releaseButton = qt.QPushButton("Release")
        self.releaseButton.clicked.connect(self.onReleaseStudy)
        self.releaseButton.setEnabled(False)
        actionLayout.addWidget(self.releaseButton)
        
        layout.addLayout(actionLayout)
        
        # Connect selection change
        self.worklistTable.itemSelectionChanged.connect(self.onSelectionChanged)
        
    def refreshWorklist(self):
        """Refresh the worklist from Orthanc."""
        self.worklistTable.setRowCount(0)
        self.currentSeries = []
        
        filter_idx = self.filterCombo.currentIndex
        if callable(filter_idx):
            filter_idx = filter_idx()
        
        try:
            if self.role == "annotator":
                if filter_idx == 0:   # My Worklist
                    series_list = self.orthancClient.get_series_worklist("annotator")
                elif filter_idx == 1: # All Pending
                    series_list = self.orthancClient.query_series_by_status(AnnotationStatus.PENDING)
                elif filter_idx == 2: # My In Progress
                    series_list = [s for s in self.orthancClient.get_all_ct_series()
                                   if s.get("annotation_status") == AnnotationStatus.IN_PROGRESS.value
                                   and s.get("annotator") == self.orthancClient.current_user]
                else:                 # Rejected
                    series_list = self.orthancClient.query_series_by_status(AnnotationStatus.REJECTED)
            elif self.role == "admin":
                if filter_idx == 0:   # All Series
                    series_list = self.orthancClient.get_all_ct_series()
                elif filter_idx == 1: # Pending
                    series_list = self.orthancClient.query_series_by_status(AnnotationStatus.PENDING)
                elif filter_idx == 2: # In Progress
                    series_list = self.orthancClient.query_series_by_status(AnnotationStatus.IN_PROGRESS)
                elif filter_idx == 3: # Annotated
                    series_list = self.orthancClient.query_series_by_status(AnnotationStatus.ANNOTATED)
                elif filter_idx == 4: # In Review
                    series_list = self.orthancClient.query_series_by_status(AnnotationStatus.IN_REVIEW)
                else:                 # Ground Truth
                    series_list = self.orthancClient.query_series_by_status(AnnotationStatus.GROUND_TRUTH)
            else:  # reviewer
                if filter_idx == 0:   # My Worklist
                    series_list = self.orthancClient.get_series_worklist("reviewer")
                elif filter_idx == 1: # Awaiting Review
                    series_list = self.orthancClient.query_series_by_status(AnnotationStatus.ANNOTATED)
                elif filter_idx == 2: # My In Review
                    series_list = [s for s in self.orthancClient.get_all_ct_series()
                                   if s.get("annotation_status") == AnnotationStatus.IN_REVIEW.value
                                   and s.get("reviewer") == self.orthancClient.current_user]
                else:                 # Ground Truth
                    series_list = self.orthancClient.query_series_by_status(AnnotationStatus.GROUND_TRUTH)

            self.currentSeries = series_list
            self.populateTable(series_list)

            stats = self.orthancClient.get_statistics_for_series()
            self.statsLabel.setText(
                f"CT Series — Total: {stats['total']} | "
                f"Pending: {stats.get('pending', 0)} | "
                f"Annotated: {stats.get('annotated', 0)} | "
                f"Ground Truth: {stats.get('ground_truth', 0)}"
            )
            
        except Exception as e:
            slicer.util.errorDisplay(f"Failed to refresh worklist: {str(e)}")
    
    def populateTable(self, series_list):
        """Populate the table with CT series data (one row per annotatable series)."""
        self.worklistTable.setRowCount(len(series_list))

        status_colors = {
            "pending":      "#FFF3CD",
            "in_progress":  "#CCE5FF",
            "annotated":    "#D4EDDA",
            "in_review":    "#E2D5F1",
            "reviewed":     "#C3E6CB",
            "rejected":     "#F8D7DA",
            "ground_truth": "#28A745",
        }
        STATUS_COL = 5  # matches header: Series Desc | PID | Date | Series# | Slices | Status | Ann | Rev

        for row, series in enumerate(series_list):
            status = series.get("annotation_status", "")
            bg_color = status_colors.get(status, "#FFFFFF")
            series_id = series.get("orthanc_series_id", "")

            desc = (series.get("series_description", "")
                    or f"Series {series.get('series_number', row + 1)}")
            items = [
                desc,
                series.get("patient_id", ""),
                series.get("study_date", ""),
                str(series.get("series_number", "")),
                str(series.get("instance_count", "")),
                status,
                series.get("annotator", "") or "-",
                series.get("reviewer", "") or "-",
            ]

            for col, text in enumerate(items):
                item = qt.QTableWidgetItem(str(text))
                item.setData(qt.Qt.UserRole, series_id)
                if col == STATUS_COL:
                    item.setBackground(qt.QColor(bg_color))
                    if status == "ground_truth":
                        item.setForeground(qt.QColor("#FFFFFF"))
                self.worklistTable.setItem(row, col, item)
        
        self.worklistTable.resizeColumnsToContents()
    
    def onSelectionChanged(self):
        """Handle table selection change."""
        selected = self.worklistTable.selectedItems()
        if not selected:
            self.claimButton.setEnabled(False)
            self.loadButton.setEnabled(False)
            self.releaseButton.setEnabled(False)
            return
        
        row = selected[0].row()
        if row >= len(self.currentSeries):
            return
            
        series = self.currentSeries[row]
        status   = series.get("annotation_status", "")
        annotator = series.get("annotator")
        reviewer  = series.get("reviewer")
        current_user = self.orthancClient.current_user
        
        if self.role == "annotator":
            # Can claim if pending or rejected
            self.claimButton.setEnabled(status in ["pending", "rejected"])
            # Can load if in_progress and claimed by self
            self.loadButton.setEnabled(status == "in_progress" and annotator == current_user)
            # Can release if in_progress and claimed by self
            self.releaseButton.setEnabled(status == "in_progress" and annotator == current_user)
        elif self.role == "admin":
            # Admin can claim any study, load any in-progress/in-review study
            self.claimButton.setEnabled(status in ["pending", "rejected", "annotated"])
            self.loadButton.setEnabled(status in ["in_progress", "in_review"])
            self.releaseButton.setEnabled(status in ["in_progress", "in_review"])
        else:  # reviewer
            # Can claim if annotated
            self.claimButton.setEnabled(status == "annotated")
            # Can load if in_review and claimed by self
            self.loadButton.setEnabled(status == "in_review" and reviewer == current_user)
            # Can release if in_review and claimed by self
            self.releaseButton.setEnabled(status == "in_review" and reviewer == current_user)
    
    def getSelectedSeries(self):
        """Get the currently selected series info."""
        selected = self.worklistTable.selectedItems()
        if not selected:
            return None, None
        row = selected[0].row()
        if row >= len(self.currentSeries):
            return None, None
        series = self.currentSeries[row]
        return series.get("orthanc_series_id"), series

    def onClaimStudy(self):
        """Claim the selected series."""
        series_id, _ = self.getSelectedSeries()
        if not series_id:
            return
        success, message = self.orthancClient.claim_series(series_id, self.role)
        if success:
            slicer.util.infoDisplay(message)
            self.refreshWorklist()
        else:
            slicer.util.errorDisplay(message)

    def onReleaseStudy(self):
        """Release the selected series."""
        series_id, _ = self.getSelectedSeries()
        if not series_id:
            return
        confirm = slicer.util.confirmYesNoDisplay(
            "Are you sure you want to release this series? Any unsaved work will be lost.",
            "Confirm Release"
        )
        if confirm:
            success, message = self.orthancClient.release_series(series_id)
            if success:
                slicer.util.infoDisplay(message)
                self.refreshWorklist()
            else:
                slicer.util.errorDisplay(message)

    def onLoadStudy(self):
        """Load the selected series for annotation/review."""
        series_id, series_info = self.getSelectedSeries()
        if series_id and series_info:
            self.seriesSelected.emit(series_id, series_info)

    def onSeriesDoubleClicked(self, index):
        """Handle double-click: auto-claim if needed then load the series."""
        series_id, series_info = self.getSelectedSeries()
        if not series_id:
            return
        status    = series_info.get("annotation_status", "")
        annotator = series_info.get("annotator")
        reviewer  = series_info.get("reviewer")
        current_user = self.orthancClient.current_user

        # Already claimed by this user — load directly
        if (self.role == "annotator" and status == "in_progress"
                and annotator == current_user):
            self.seriesSelected.emit(series_id, series_info)
        elif (self.role == "reviewer" and status == "in_review"
              and reviewer == current_user):
            self.seriesSelected.emit(series_id, series_info)
        elif ((self.role == "annotator" and status in ("pending", "rejected"))
              or (self.role == "reviewer" and status == "annotated")):
            success, message = self.orthancClient.claim_series(series_id, self.role)
            if success:
                self.refreshWorklist()
                updated_info = self.orthancClient.get_series_info(series_id)
                if updated_info:
                    self.seriesSelected.emit(series_id, updated_info)
            else:
                slicer.util.errorDisplay(message)