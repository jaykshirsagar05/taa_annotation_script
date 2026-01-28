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


class OrthancLoginWidget(qt.QWidget):
    """Login panel for Orthanc authentication via AdminDashboard."""
    
    def __init__(self, orthanc_client: OrthancClient, 
                 on_login_success: Optional[Callable] = None,
                 parent=None):
        super().__init__(parent)
        self.orthancClient = orthanc_client
        self.onLoginSuccess = on_login_success
        self.setupUI()
        
    def setupUI(self):
        layout = qt.QVBoxLayout(self)
        
        # Server settings group
        serverGroup = qt.QGroupBox("Server Connection")
        serverLayout = qt.QFormLayout(serverGroup)
        
        self.adminUrlEdit = qt.QLineEdit("http://localhost:8000")
        serverLayout.addRow("Admin Dashboard:", self.adminUrlEdit)
        
        self.orthancUrlEdit = qt.QLineEdit("http://localhost:8042")
        serverLayout.addRow("Orthanc Server:", self.orthancUrlEdit)
        
        layout.addWidget(serverGroup)
        
        # Credentials group
        credGroup = qt.QGroupBox("Credentials")
        credLayout = qt.QFormLayout(credGroup)
        
        self.usernameEdit = qt.QLineEdit()
        self.usernameEdit.setPlaceholderText("Username")
        credLayout.addRow("Username:", self.usernameEdit)
        
        self.passwordEdit = qt.QLineEdit()
        self.passwordEdit.setPlaceholderText("Password")
        self.passwordEdit.setEchoMode(qt.QLineEdit.Password)
        credLayout.addRow("Password:", self.passwordEdit)
        
        layout.addWidget(credGroup)
        
        # Note: Role is now determined by AdminDashboard, not selected locally
        roleNote = qt.QLabel("Note: Your role is assigned by the administrator")
        roleNote.setStyleSheet("color: gray; font-style: italic; font-size: 11px;")
        layout.addWidget(roleNote)
        
        # Login button
        self.loginButton = qt.QPushButton("Login")
        self.loginButton.clicked.connect(self.onLogin)
        layout.addWidget(self.loginButton)
        
        # Status label
        self.statusLabel = qt.QLabel("")
        self.statusLabel.setWordWrap(True)
        layout.addWidget(self.statusLabel)
        
        layout.addStretch()
        
        # Enable enter key to login
        self.passwordEdit.returnPressed.connect(self.onLogin)
        
    def onLogin(self):
        """Handle login button click."""
        admin_url = self.adminUrlEdit.text.strip()
        orthanc_url = self.orthancUrlEdit.text.strip()
        username = self.usernameEdit.text.strip()
        password = self.passwordEdit.text
        
        if not username or not password:
            self.statusLabel.setText("Please enter username and password")
            self.statusLabel.setStyleSheet("color: orange;")
            return
        
        self.loginButton.setEnabled(False)
        self.statusLabel.setText("Connecting to AdminDashboard...")
        self.statusLabel.setStyleSheet("color: gray;")
        
        # Update URLs
        self.orthancClient.admin_url = admin_url
        self.orthancClient.server_url = orthanc_url
        
        # Attempt login via AdminDashboard
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
    
    def getSelectedRole(self) -> str:
        """Get the authenticated user's role (from AdminDashboard)."""
        return self.orthancClient.get_role() or "annotator"


class OrthancWorklistWidget(qt.QWidget):
    """Worklist widget showing available studies."""
    
    # Signal emitted when a study is selected for annotation
    studySelected = qt.Signal(str, dict)  # (study_id, study_info)
    
    def __init__(self, orthanc_client: OrthancClient, role: str = "annotator", parent=None):
        super().__init__(parent)
        self.orthancClient = orthanc_client
        self.role = role
        self.currentStudies = []
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
            self.filterCombo.addItems(["All Studies", "Pending", "In Progress", "Annotated", "In Review", "Ground Truth"])
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
        self.worklistTable.setColumnCount(6)
        self.worklistTable.setHorizontalHeaderLabels([
            "Patient ID", "Patient Name", "Study Date", "Status", "Annotator", "Reviewer"
        ])
        self.worklistTable.setSelectionBehavior(qt.QAbstractItemView.SelectRows)
        self.worklistTable.setSelectionMode(qt.QAbstractItemView.SingleSelection)
        self.worklistTable.horizontalHeader().setStretchLastSection(True)
        self.worklistTable.setAlternatingRowColors(True)
        self.worklistTable.doubleClicked.connect(self.onStudyDoubleClicked)
        
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
        self.currentStudies = []
        
        filter_idx = self.filterCombo.currentIndex
        if callable(filter_idx):
            filter_idx = filter_idx()
        
        try:
            if self.role == "annotator":
                if filter_idx == 0:  # My Worklist
                    studies = self.orthancClient.get_worklist("annotator")
                elif filter_idx == 1:  # All Pending
                    studies = self.orthancClient.query_studies_by_status(AnnotationStatus.PENDING)
                elif filter_idx == 2:  # My In Progress
                    studies = [s for s in self.orthancClient.get_all_studies()
                              if s.get("annotation_status") == AnnotationStatus.IN_PROGRESS.value
                              and s.get("annotator") == self.orthancClient.current_user]
                else:  # Rejected
                    studies = self.orthancClient.query_studies_by_status(AnnotationStatus.REJECTED)
            elif self.role == "admin":
                # Admin can see all studies with different filters
                if filter_idx == 0:  # All Studies
                    studies = self.orthancClient.get_all_studies()
                elif filter_idx == 1:  # Pending
                    studies = self.orthancClient.query_studies_by_status(AnnotationStatus.PENDING)
                elif filter_idx == 2:  # In Progress
                    studies = self.orthancClient.query_studies_by_status(AnnotationStatus.IN_PROGRESS)
                elif filter_idx == 3:  # Annotated
                    studies = self.orthancClient.query_studies_by_status(AnnotationStatus.ANNOTATED)
                elif filter_idx == 4:  # In Review
                    studies = self.orthancClient.query_studies_by_status(AnnotationStatus.IN_REVIEW)
                else:  # Ground Truth
                    studies = self.orthancClient.query_studies_by_status(AnnotationStatus.GROUND_TRUTH)
            else:  # reviewer
                if filter_idx == 0:  # My Worklist
                    studies = self.orthancClient.get_worklist("reviewer")
                elif filter_idx == 1:  # Awaiting Review
                    studies = self.orthancClient.query_studies_by_status(AnnotationStatus.ANNOTATED)
                elif filter_idx == 2:  # My In Review
                    studies = [s for s in self.orthancClient.get_all_studies()
                              if s.get("annotation_status") == AnnotationStatus.IN_REVIEW.value
                              and s.get("reviewer") == self.orthancClient.current_user]
                else:  # Ground Truth
                    studies = self.orthancClient.query_studies_by_status(AnnotationStatus.GROUND_TRUTH)
            
            self.currentStudies = studies
            self.populateTable(studies)
            
            # Update statistics
            stats = self.orthancClient.get_statistics()
            self.statsLabel.setText(
                f"Total: {stats['total']} | Pending: {stats.get('pending', 0)} | "
                f"Annotated: {stats.get('annotated', 0)} | Ground Truth: {stats.get('ground_truth', 0)}"
            )
            
        except Exception as e:
            slicer.util.errorDisplay(f"Failed to refresh worklist: {str(e)}")
    
    def populateTable(self, studies):
        """Populate the table with study data."""
        self.worklistTable.setRowCount(len(studies))
        
        status_colors = {
            "pending": "#FFF3CD",
            "in_progress": "#CCE5FF",
            "annotated": "#D4EDDA",
            "in_review": "#E2D5F1",
            "reviewed": "#C3E6CB",
            "rejected": "#F8D7DA",
            "ground_truth": "#28A745"
        }
        
        for row, study in enumerate(studies):
            items = [
                study.get("patient_id", ""),
                study.get("patient_name", ""),
                study.get("study_date", ""),
                study.get("annotation_status", ""),
                study.get("annotator", "") or "-",
                study.get("reviewer", "") or "-"
            ]
            
            status = study.get("annotation_status", "")
            bg_color = status_colors.get(status, "#FFFFFF")
            
            for col, text in enumerate(items):
                item = qt.QTableWidgetItem(str(text))
                item.setData(qt.Qt.UserRole, study.get("orthanc_id"))
                
                if col == 3:  # Status column
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
        if row >= len(self.currentStudies):
            return
            
        study = self.currentStudies[row]
        status = study.get("annotation_status", "")
        annotator = study.get("annotator")
        reviewer = study.get("reviewer")
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
    
    def getSelectedStudy(self):
        """Get the currently selected study info."""
        selected = self.worklistTable.selectedItems()
        if not selected:
            return None, None
        
        row = selected[0].row()
        if row >= len(self.currentStudies):
            return None, None
        
        study = self.currentStudies[row]
        return study.get("orthanc_id"), study
    
    def onClaimStudy(self):
        """Claim the selected study."""
        study_id, study_info = self.getSelectedStudy()
        if not study_id:
            return
        
        success, message = self.orthancClient.claim_study(study_id, self.role)
        if success:
            slicer.util.infoDisplay(message)
            self.refreshWorklist()
        else:
            slicer.util.errorDisplay(message)
    
    def onReleaseStudy(self):
        """Release the selected study."""
        study_id, study_info = self.getSelectedStudy()
        if not study_id:
            return
        
        confirm = slicer.util.confirmYesNoDisplay(
            "Are you sure you want to release this study? Any unsaved work will be lost.",
            "Confirm Release"
        )
        if confirm:
            success, message = self.orthancClient.release_study(study_id)
            if success:
                slicer.util.infoDisplay(message)
                self.refreshWorklist()
            else:
                slicer.util.errorDisplay(message)
    
    def onLoadStudy(self):
        """Load the selected study for annotation/review."""
        study_id, study_info = self.getSelectedStudy()
        if study_id and study_info:
            self.studySelected.emit(study_id, study_info)
    
    def onStudyDoubleClicked(self, index):
        """Handle double-click on a study row."""
        study_id, study_info = self.getSelectedStudy()
        if not study_id:
            return
        
        status = study_info.get("annotation_status", "")
        annotator = study_info.get("annotator")
        reviewer = study_info.get("reviewer")
        current_user = self.orthancClient.current_user
        
        # If already claimed by user, load directly
        if self.role == "annotator" and status == "in_progress" and annotator == current_user:
            self.studySelected.emit(study_id, study_info)
        elif self.role == "reviewer" and status == "in_review" and reviewer == current_user:
            self.studySelected.emit(study_id, study_info)
        # Otherwise, try to claim first
        elif (self.role == "annotator" and status in ["pending", "rejected"]) or \
             (self.role == "reviewer" and status == "annotated"):
            success, message = self.orthancClient.claim_study(study_id, self.role)
            if success:
                self.refreshWorklist()
                # Re-fetch study info and load
                updated_info = self.orthancClient.get_study_info(study_id)
                if updated_info:
                    self.studySelected.emit(study_id, updated_info)
            else:
                slicer.util.errorDisplay(message)