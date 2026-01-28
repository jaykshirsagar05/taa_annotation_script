"""
OrthancClient.py - REST API client for Orthanc PACS server integration.

Handles:
- Authentication via AdminDashboard (centralized user management)
- Query/retrieve studies and series from Orthanc
- Upload/download NIfTI attachments
- Annotation lifecycle metadata management

Architecture:
- Slicer client authenticates with AdminDashboard to get user role/token
- Orthanc access uses credentials configured on server side
- User role determines available workflow actions
"""

import requests
import json
import tempfile
import os
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum


class AnnotationStatus(Enum):
    """Annotation lifecycle states stored as Orthanc metadata."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    ANNOTATED = "annotated"
    IN_REVIEW = "in_review"
    REVIEWED = "reviewed"
    REJECTED = "rejected"
    GROUND_TRUTH = "ground_truth"


class OrthancClient:
    """
    REST client for Orthanc PACS server with AdminDashboard authentication.
    
    Authentication Flow:
        1. Client calls login() with AdminDashboard credentials
        2. AdminDashboard validates user and returns JWT token + role
        3. Client uses token for subsequent API calls
        4. Orthanc access is proxied through AdminDashboard or uses shared creds
    
    Attachment Types Used:
        - 1024: annotation_metadata.json (custom metadata)
        - 1025: ct_scan.nii.gz
        - 1026: unified_mask_smoothed.nii.gz
        - 1027: merged_mask.nii.gz
        - 1028: refined_mask.seg.nrrd
        - 1029: centerline.vtk
        - 1030: zones.fcsv
        - 1031: endpoints.fcsv
        - 1032: notes.txt
    """
    
    # Custom attachment type IDs (Orthanc allows 1024-65535 for user-defined)
    ATTACHMENT_METADATA = 1024
    ATTACHMENT_CT_NIFTI = 1025
    ATTACHMENT_UNIFIED_MASK = 1026
    ATTACHMENT_MERGED_MASK = 1027
    ATTACHMENT_REFINED_MASK = 1028
    ATTACHMENT_CENTERLINE = 1029
    ATTACHMENT_ZONES = 1030
    ATTACHMENT_ENDPOINTS = 1031
    ATTACHMENT_NOTES = 1032
    
    def __init__(self, orthanc_url: str = "http://localhost:8042",
                 admin_dashboard_url: str = "http://localhost:7777"):
        """
        Initialize Orthanc client with AdminDashboard integration.
        
        Args:
            orthanc_url: Base URL of Orthanc server
            admin_dashboard_url: Base URL of AdminDashboard API
        """
        self.server_url = orthanc_url.rstrip('/')
        self.admin_url = admin_dashboard_url.rstrip('/')
        
        # Authentication state
        self.auth_token: Optional[str] = None
        self.current_user: Optional[str] = None
        self.user_role: Optional[str] = None
        self.user_id: Optional[int] = None
        self.assigned_series: List[str] = []
        
        # Orthanc basic auth (obtained from AdminDashboard or config)
        self.orthanc_auth: Optional[Tuple[str, str]] = None
        
        self.session = requests.Session()
        
    def login(self, username: str, password: str) -> Tuple[bool, str]:
        """
        Authenticate with AdminDashboard to get user role and token.
        
        The AdminDashboard validates credentials against its user database
        and returns the user's role, which determines available actions.
        
        Args:
            username: AdminDashboard username
            password: AdminDashboard password
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            # Authenticate with AdminDashboard
            response = requests.post(
                f"{self.admin_url}/auth/api/login",
                json={"username": username, "password": password},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    self.auth_token = data["token"]
                    self.current_user = data["user"]["username"]
                    self.user_role = data["user"]["role"]
                    self.user_id = data["user"]["id"]
                    self.assigned_series = data["user"].get("assigned_series_uids", [])
                    
                    # Now verify Orthanc connectivity
                    orthanc_ok, orthanc_msg = self._verify_orthanc_connection()
                    if orthanc_ok:
                        return True, f"Logged in as {self.current_user} ({self.user_role})"
                    else:
                        return True, f"Logged in but Orthanc unavailable: {orthanc_msg}"
                else:
                    return False, data.get("error", "Authentication failed")
            elif response.status_code == 401:
                return False, "Invalid credentials"
            else:
                return False, f"Server error: HTTP {response.status_code}"
                
        except requests.exceptions.ConnectionError:
            return False, f"Cannot connect to AdminDashboard at {self.admin_url}"
        except requests.exceptions.Timeout:
            return False, "Connection timed out"
        except Exception as e:
            return False, f"Login error: {str(e)}"
    
    def _verify_orthanc_connection(self) -> Tuple[bool, str]:
        """
        Verify connectivity to Orthanc server.
        
        Orthanc credentials are typically configured on the server side.
        This method tests if Orthanc is reachable.
        """
        try:
            # Try to connect to Orthanc (may need auth from config)
            response = self.session.get(f"{self.server_url}/system", timeout=10)
            if response.status_code == 200:
                system_info = response.json()
                return True, f"Orthanc {system_info.get('Version', 'unknown')}"
            elif response.status_code == 401:
                # Need to get Orthanc credentials - could be from AdminDashboard config
                return False, "Orthanc requires authentication"
            else:
                return False, f"Orthanc HTTP {response.status_code}"
        except Exception as e:
            return False, str(e)
    
    def set_orthanc_credentials(self, username: str, password: str):
        """
        Set Orthanc server credentials for direct access.
        
        In production, these may be obtained from AdminDashboard config
        or be the same as user credentials.
        """
        self.orthanc_auth = (username, password)
        self.session.auth = self.orthanc_auth
        
    def logout(self):
        """Clear authentication state."""
        self.auth_token = None
        self.current_user = None
        self.user_role = None
        self.user_id = None
        self.assigned_series = []
        self.orthanc_auth = None
        self.session.auth = None
        
    def is_authenticated(self) -> bool:
        """Check if client is authenticated with AdminDashboard."""
        return self.auth_token is not None and self.current_user is not None
    
    def get_role(self) -> Optional[str]:
        """Get the authenticated user's role from AdminDashboard."""
        return self.user_role
    
    def validate_token(self) -> Tuple[bool, str]:
        """Validate the current token with AdminDashboard."""
        if not self.auth_token:
            return False, "No token"
        
        try:
            response = requests.post(
                f"{self.admin_url}/auth/api/validate-token",
                json={"token": self.auth_token},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("valid"):
                    # Update user info in case it changed
                    user = data["user"]
                    self.user_role = user["role"]
                    self.assigned_series = user.get("assigned_series_uids", [])
                    return True, "Token valid"
                return False, data.get("error", "Invalid token")
            return False, f"Server error: {response.status_code}"
        except Exception as e:
            return False, str(e)
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authorization headers for API requests."""
        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers
    
    # -------------------------------------------------------------------------
    # Study/Series Query Methods
    # -------------------------------------------------------------------------
    
    def get_all_studies(self) -> List[Dict[str, Any]]:
        """
        Get all studies from Orthanc.
        
        Returns:
            List of study info dictionaries
        """
        try:
            response = self.session.get(f"{self.server_url}/studies")
            response.raise_for_status()
            study_ids = response.json()
            
            studies = []
            for study_id in study_ids:
                study_info = self.get_study_info(study_id)
                if study_info:
                    studies.append(study_info)
            return studies
        except Exception as e:
            print(f"[OrthancClient] Error fetching studies: {e}")
            return []
    
    def get_study_info(self, study_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a study.
        
        Args:
            study_id: Orthanc study ID
            
        Returns:
            Study info dictionary or None
        """
        try:
            response = self.session.get(f"{self.server_url}/studies/{study_id}")
            response.raise_for_status()
            study_data = response.json()
            
            # Get annotation metadata if exists
            metadata = self.get_annotation_metadata(study_id)
            
            return {
                "orthanc_id": study_id,
                "patient_id": study_data.get("PatientMainDicomTags", {}).get("PatientID", "Unknown"),
                "patient_name": study_data.get("PatientMainDicomTags", {}).get("PatientName", "Unknown"),
                "study_date": study_data.get("MainDicomTags", {}).get("StudyDate", ""),
                "study_description": study_data.get("MainDicomTags", {}).get("StudyDescription", ""),
                "modality": self._get_study_modality(study_data),
                "series_count": len(study_data.get("Series", [])),
                "annotation_status": metadata.get("status", AnnotationStatus.PENDING.value) if metadata else AnnotationStatus.PENDING.value,
                "annotator": metadata.get("annotator") if metadata else None,
                "reviewer": metadata.get("reviewer") if metadata else None,
                "annotation_metadata": metadata
            }
        except Exception as e:
            print(f"[OrthancClient] Error fetching study {study_id}: {e}")
            return None
    
    def _get_study_modality(self, study_data: Dict) -> str:
        """Extract modality from study data."""
        series_list = study_data.get("Series", [])
        if series_list:
            try:
                series_resp = self.session.get(f"{self.server_url}/series/{series_list[0]}")
                if series_resp.status_code == 200:
                    return series_resp.json().get("MainDicomTags", {}).get("Modality", "Unknown")
            except:
                pass
        return "Unknown"
    
    def query_studies_by_status(self, status: AnnotationStatus) -> List[Dict[str, Any]]:
        """
        Query studies filtered by annotation status via AdminDashboard.
        
        Args:
            status: AnnotationStatus enum value
            
        Returns:
            List of studies matching the status
        """
        try:
            response = requests.get(
                f"{self.admin_url}/studies/api/by-status/{status.value}",
                headers=self._get_auth_headers(),
                timeout=30
            )
            if response.status_code == 200:
                return response.json()
            else:
                print(f"[OrthancClient] Failed to query by status: HTTP {response.status_code}")
                return []
        except Exception as e:
            print(f"[OrthancClient] Error querying studies by status: {e}")
            # Fallback to direct Orthanc query
            all_studies = self.get_all_studies()
            return [s for s in all_studies if s.get("annotation_status") == status.value]
    
    def get_worklist(self, role: str = None) -> List[Dict[str, Any]]:
        """
        Get worklist based on user role from AdminDashboard.
        
        Uses the authenticated user's role from AdminDashboard database,
        not a locally selected role.
        
        Args:
            role: Override role (optional, defaults to authenticated role)
            
        Returns:
            List of studies for the worklist
        """
        effective_role = role or self.user_role
        
        try:
            params = {"role": effective_role} if effective_role else {}
            response = requests.get(
                f"{self.admin_url}/studies/api/worklist",
                headers=self._get_auth_headers(),
                params=params,
                timeout=30
            )
            if response.status_code == 200:
                return response.json()
            else:
                print(f"[OrthancClient] Failed to get worklist: HTTP {response.status_code}")
                # Fallback to local filtering
                return self._get_worklist_fallback(effective_role)
        except Exception as e:
            print(f"[OrthancClient] Error fetching worklist: {e}")
            return self._get_worklist_fallback(effective_role)
    
    def _get_worklist_fallback(self, role: str) -> List[Dict[str, Any]]:
        """Fallback worklist logic when AdminDashboard is unavailable."""
        if not role:
            print("[OrthancClient] Warning: No role set, returning all studies")
            return self.get_all_studies()
        
        all_studies = self.get_all_studies()
        
        if role == "annotator":
            return [s for s in all_studies 
                    if s.get("annotation_status") == AnnotationStatus.PENDING.value
                    or (s.get("annotation_status") == AnnotationStatus.REJECTED.value
                        and s.get("annotator") == self.current_user)
                    or (s.get("annotation_status") == AnnotationStatus.IN_PROGRESS.value 
                        and s.get("annotator") == self.current_user)]
        elif role == "reviewer":
            return [s for s in all_studies 
                    if s.get("annotation_status") == AnnotationStatus.ANNOTATED.value
                    or (s.get("annotation_status") == AnnotationStatus.IN_REVIEW.value
                        and s.get("reviewer") == self.current_user)]
        else:
            return all_studies
    
    def claim_study(self, study_id: str, role: str = None) -> Tuple[bool, str]:
        """
        Claim a study for annotation or review via AdminDashboard.
        
        Args:
            study_id: Orthanc study ID
            role: Override role (optional, defaults to authenticated role)
            
        Returns:
            Tuple of (success, message)
        """
        effective_role = role or self.user_role
        if not effective_role:
            return False, "No role assigned - please contact admin"
        
        try:
            response = requests.post(
                f"{self.admin_url}/studies/api/{study_id}/claim",
                headers=self._get_auth_headers(),
                json={"role": effective_role},
                timeout=30
            )
            data = response.json()
            
            if response.status_code == 200 and data.get("success"):
                return True, data.get("message", "Study claimed successfully")
            else:
                return False, data.get("error", f"Claim failed: HTTP {response.status_code}")
        except Exception as e:
            print(f"[OrthancClient] Error claiming study via AdminDashboard: {e}")
            # Fallback to direct Orthanc update
            return self._claim_study_fallback(study_id, effective_role)
    
    def _claim_study_fallback(self, study_id: str, role: str) -> Tuple[bool, str]:
        """Fallback claim logic when AdminDashboard is unavailable."""
        if role not in ["annotator", "reviewer", "admin"]:
            return False, f"Invalid role: {role}"
        
        metadata = self.get_annotation_metadata(study_id) or {}
        current_status = metadata.get("status", AnnotationStatus.PENDING.value)
        
        if role == "annotator" or (role == "admin" and current_status == AnnotationStatus.PENDING.value):
            if current_status not in [AnnotationStatus.PENDING.value, AnnotationStatus.REJECTED.value]:
                return False, f"Cannot claim: Study status is '{current_status}'"
            
            metadata["status"] = AnnotationStatus.IN_PROGRESS.value
            metadata["annotator"] = self.current_user
            metadata["annotation_started"] = datetime.now().isoformat()
            
        elif role == "reviewer" or (role == "admin" and current_status == AnnotationStatus.ANNOTATED.value):
            if current_status != AnnotationStatus.ANNOTATED.value:
                return False, f"Cannot claim for review: Study status is '{current_status}'"
            
            metadata["status"] = AnnotationStatus.IN_REVIEW.value
            metadata["reviewer"] = self.current_user
            metadata["review_started"] = datetime.now().isoformat()
        else:
            return False, f"Role '{role}' cannot claim study with status '{current_status}'"
        
        if "history" not in metadata:
            metadata["history"] = []
        metadata["history"].append({
            "action": f"claimed_by_{role}",
            "user": self.current_user,
            "user_id": self.user_id,
            "timestamp": datetime.now().isoformat(),
            "previous_status": current_status
        })
        
        if self.set_annotation_metadata(study_id, metadata):
            return True, f"Study claimed successfully as {role}"
        return False, "Failed to update metadata"
    
    def release_study(self, study_id: str) -> Tuple[bool, str]:
        """Release a claimed study back to previous state via AdminDashboard."""
        try:
            response = requests.post(
                f"{self.admin_url}/studies/api/{study_id}/release",
                headers=self._get_auth_headers(),
                timeout=30
            )
            data = response.json()
            
            if response.status_code == 200 and data.get("success"):
                return True, data.get("message", "Study released successfully")
            else:
                return False, data.get("error", f"Release failed: HTTP {response.status_code}")
        except Exception as e:
            print(f"[OrthancClient] Error releasing study via AdminDashboard: {e}")
            # Fallback to direct Orthanc update
            return self._release_study_fallback(study_id)
    
    def _release_study_fallback(self, study_id: str) -> Tuple[bool, str]:
        """Fallback release logic when AdminDashboard is unavailable."""
        metadata = self.get_annotation_metadata(study_id) or {}
        current_status = metadata.get("status")
        
        if current_status == AnnotationStatus.IN_PROGRESS.value:
            if metadata.get("annotator") != self.current_user and self.user_role != "admin":
                return False, "Cannot release: Study claimed by different user"
            metadata["status"] = AnnotationStatus.PENDING.value
            metadata["annotator"] = None
            
        elif current_status == AnnotationStatus.IN_REVIEW.value:
            if metadata.get("reviewer") != self.current_user and self.user_role != "admin":
                return False, "Cannot release: Study claimed by different reviewer"
            metadata["status"] = AnnotationStatus.ANNOTATED.value
            metadata["reviewer"] = None
        else:
            return False, f"Cannot release: Status is '{current_status}'"
        
        if "history" not in metadata:
            metadata["history"] = []
        metadata["history"].append({
            "action": "released",
            "user": self.current_user,
            "user_id": self.user_id,
            "timestamp": datetime.now().isoformat(),
            "previous_status": current_status
        })
        
        if self.set_annotation_metadata(study_id, metadata):
            return True, "Study released successfully"
        return False, "Failed to update metadata"
    
    # -------------------------------------------------------------------------
    # NIfTI Attachment Methods (unchanged)
    # -------------------------------------------------------------------------
    
    def upload_nifti(self, study_id: str, file_path: str, attachment_type: int) -> bool:
        """Upload a NIfTI file as an attachment to a study."""
        try:
            with open(file_path, 'rb') as f:
                data = f.read()
            
            response = self.session.put(
                f"{self.server_url}/studies/{study_id}/attachments/{attachment_type}",
                data=data,
                headers={"Content-Type": "application/octet-stream"}
            )
            return response.status_code in [200, 201]
        except Exception as e:
            print(f"[OrthancClient] Error uploading NIfTI: {e}")
            return False
    
    def download_nifti(self, study_id: str, attachment_type: int, 
                       output_path: Optional[str] = None) -> Optional[str]:
        """Download a NIfTI attachment from a study."""
        try:
            response = self.session.get(
                f"{self.server_url}/studies/{study_id}/attachments/{attachment_type}/data"
            )
            
            if response.status_code == 200:
                if output_path is None:
                    suffix = ".nii.gz" if attachment_type in [
                        self.ATTACHMENT_CT_NIFTI, 
                        self.ATTACHMENT_UNIFIED_MASK,
                        self.ATTACHMENT_MERGED_MASK
                    ] else self._get_extension_for_type(attachment_type)
                    
                    fd, output_path = tempfile.mkstemp(suffix=suffix)
                    os.close(fd)
                
                with open(output_path, 'wb') as f:
                    f.write(response.content)
                return output_path
            elif response.status_code == 404:
                return None
            else:
                print(f"[OrthancClient] Download failed: HTTP {response.status_code}")
                return None
        except Exception as e:
            print(f"[OrthancClient] Error downloading attachment: {e}")
            return None
    
    def _get_extension_for_type(self, attachment_type: int) -> str:
        """Get file extension for attachment type."""
        extensions = {
            self.ATTACHMENT_REFINED_MASK: ".seg.nrrd",
            self.ATTACHMENT_CENTERLINE: ".vtk",
            self.ATTACHMENT_ZONES: ".fcsv",
            self.ATTACHMENT_ENDPOINTS: ".fcsv",
            self.ATTACHMENT_NOTES: ".txt",
            self.ATTACHMENT_METADATA: ".json"
        }
        return extensions.get(attachment_type, ".bin")
    
    def has_attachment(self, study_id: str, attachment_type: int) -> bool:
        """Check if an attachment exists for a study."""
        try:
            response = self.session.get(
                f"{self.server_url}/studies/{study_id}/attachments/{attachment_type}"
            )
            return response.status_code == 200
        except:
            return False
    
    def get_study_attachments(self, study_id: str) -> Dict[str, bool]:
        """Check which standard attachments exist for a study."""
        return {
            "ct_nifti": self.has_attachment(study_id, self.ATTACHMENT_CT_NIFTI),
            "unified_mask": self.has_attachment(study_id, self.ATTACHMENT_UNIFIED_MASK),
            "merged_mask": self.has_attachment(study_id, self.ATTACHMENT_MERGED_MASK),
            "refined_mask": self.has_attachment(study_id, self.ATTACHMENT_REFINED_MASK),
            "centerline": self.has_attachment(study_id, self.ATTACHMENT_CENTERLINE),
            "zones": self.has_attachment(study_id, self.ATTACHMENT_ZONES),
            "endpoints": self.has_attachment(study_id, self.ATTACHMENT_ENDPOINTS),
            "notes": self.has_attachment(study_id, self.ATTACHMENT_NOTES),
            "metadata": self.has_attachment(study_id, self.ATTACHMENT_METADATA)
        }
    
    # -------------------------------------------------------------------------
    # Annotation Workflow Methods
    # -------------------------------------------------------------------------
    
    def submit_annotation(self, study_id: str, files: Dict[str, str], 
                          notes: str = "") -> Tuple[bool, str]:
        """Submit completed annotation for a study."""
        # Validate role
        if self.user_role not in ["annotator", "admin"]:
            return False, f"Role '{self.user_role}' cannot submit annotations"
        
        metadata = self.get_annotation_metadata(study_id) or {}
        
        if metadata.get("annotator") != self.current_user and self.user_role != "admin":
            return False, "Cannot submit: Study not claimed by you"
        
        # Upload files
        file_mapping = {
            "refined_mask": self.ATTACHMENT_REFINED_MASK,
            "centerline": self.ATTACHMENT_CENTERLINE,
            "zones": self.ATTACHMENT_ZONES,
            "endpoints": self.ATTACHMENT_ENDPOINTS
        }
        
        uploaded = []
        for name, path in files.items():
            if path and os.path.exists(path):
                attachment_type = file_mapping.get(name)
                if attachment_type and self.upload_nifti(study_id, path, attachment_type):
                    uploaded.append(name)
                else:
                    return False, f"Failed to upload {name}"
        
        # Upload notes if provided
        if notes:
            try:
                self.session.put(
                    f"{self.server_url}/studies/{study_id}/attachments/{self.ATTACHMENT_NOTES}",
                    data=notes.encode('utf-8'),
                    headers={"Content-Type": "text/plain"}
                )
            except Exception as e:
                print(f"[OrthancClient] Warning: Failed to upload notes: {e}")
        
        # Update metadata
        metadata["status"] = AnnotationStatus.ANNOTATED.value
        metadata["annotation_completed"] = datetime.now().isoformat()
        metadata["uploaded_files"] = uploaded
        metadata["has_notes"] = bool(notes)
        
        metadata["history"].append({
            "action": "annotation_submitted",
            "user": self.current_user,
            "user_id": self.user_id,
            "timestamp": datetime.now().isoformat(),
            "files_uploaded": uploaded
        })
        
        if self.set_annotation_metadata(study_id, metadata):
            return True, f"Annotation submitted successfully ({len(uploaded)} files uploaded)"
        return False, "Failed to update metadata"
    
    def approve_annotation(self, study_id: str, comments: str = "") -> Tuple[bool, str]:
        """Approve an annotation and promote to ground truth."""
        # Validate role
        if self.user_role not in ["reviewer", "admin"]:
            return False, f"Role '{self.user_role}' cannot approve annotations"
        
        metadata = self.get_annotation_metadata(study_id) or {}
        
        if metadata.get("reviewer") != self.current_user and self.user_role != "admin":
            return False, "Cannot approve: Study not claimed by you for review"
        
        metadata["status"] = AnnotationStatus.GROUND_TRUTH.value
        metadata["review_completed"] = datetime.now().isoformat()
        metadata["reviewer_comments"] = comments
        metadata["approved_by"] = self.current_user
        
        metadata["history"].append({
            "action": "approved_as_ground_truth",
            "user": self.current_user,
            "user_id": self.user_id,
            "timestamp": datetime.now().isoformat(),
            "comments": comments
        })
        
        if self.set_annotation_metadata(study_id, metadata):
            return True, "Annotation approved as ground truth"
        return False, "Failed to update metadata"
    
    def reject_annotation(self, study_id: str, reason: str) -> Tuple[bool, str]:
        """Reject an annotation and send back to annotator."""
        # Validate role
        if self.user_role not in ["reviewer", "admin"]:
            return False, f"Role '{self.user_role}' cannot reject annotations"
        
        metadata = self.get_annotation_metadata(study_id) or {}
        
        if metadata.get("reviewer") != self.current_user and self.user_role != "admin":
            return False, "Cannot reject: Study not claimed by you for review"
        
        if not reason:
            return False, "Rejection reason is required"
        
        metadata["status"] = AnnotationStatus.REJECTED.value
        metadata["review_completed"] = datetime.now().isoformat()
        metadata["rejection_reason"] = reason
        metadata["rejected_by"] = self.current_user
        metadata["reviewer"] = None
        
        metadata["history"].append({
            "action": "rejected",
            "user": self.current_user,
            "user_id": self.user_id,
            "timestamp": datetime.now().isoformat(),
            "reason": reason
        })
        
        if self.set_annotation_metadata(study_id, metadata):
            return True, "Annotation rejected and returned to annotator"
        return False, "Failed to update metadata"
    
    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------
    
    def upload_initial_data(self, study_id: str, ct_path: str, 
                            unified_mask_path: str, merged_mask_path: str) -> Tuple[bool, str]:
        """Upload initial NIfTI data for a study (admin function)."""
        # Validate role
        if self.user_role != "admin":
            return False, "Only admins can upload initial data"
        
        uploads = [
            (ct_path, self.ATTACHMENT_CT_NIFTI, "CT"),
            (unified_mask_path, self.ATTACHMENT_UNIFIED_MASK, "unified mask"),
            (merged_mask_path, self.ATTACHMENT_MERGED_MASK, "merged mask")
        ]
        
        for path, attachment_type, name in uploads:
            if not os.path.exists(path):
                return False, f"File not found: {path}"
            if not self.upload_nifti(study_id, path, attachment_type):
                return False, f"Failed to upload {name}"
        
        metadata = {
            "status": AnnotationStatus.PENDING.value,
            "created": None,
            "created_by": self.current_user,
            "history": [{
                "action": "data_uploaded",
                "user": self.current_user,
                "user_id": self.user_id,
                "timestamp": datetime.now().isoformat()
            }]
        }
        
        if self.set_annotation_metadata(study_id, metadata):
            return True, "Initial data uploaded successfully"
        return False, "Failed to set metadata"
    
    def get_statistics(self) -> Dict[str, int]:
        """Get annotation statistics across all studies."""
        all_studies = self.get_all_studies()
        stats = {status.value: 0 for status in AnnotationStatus}
        
        for study in all_studies:
            status = study.get("annotation_status", AnnotationStatus.PENDING.value)
            if status in stats:
                stats[status] += 1
        
        stats["total"] = len(all_studies)
        return stats

    # -------------------------------------------------------------------------
    # Annotation Metadata Methods
    # -------------------------------------------------------------------------
    
    def get_annotation_metadata(self, study_id: str) -> Optional[Dict[str, Any]]:
        """
        Get annotation metadata for a study.
        
        Args:
            study_id: Orthanc study ID
            
        Returns:
            Metadata dictionary or None if not found
        """
        try:
            response = self.session.get(
                f"{self.server_url}/studies/{study_id}/attachments/{self.ATTACHMENT_METADATA}/data"
            )
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                # No metadata exists yet - return default
                return {
                    "status": AnnotationStatus.PENDING.value,
                    "created": None,
                    "annotator": None,
                    "reviewer": None,
                    "history": []
                }
            else:
                print(f"[OrthancClient] Failed to get metadata: HTTP {response.status_code}")
                return None
        except Exception as e:
            print(f"[OrthancClient] Error getting annotation metadata: {e}")
            return None
    
    def set_annotation_metadata(self, study_id: str, metadata: Dict[str, Any]) -> bool:
        """
        Set annotation metadata for a study.
        
        Args:
            study_id: Orthanc study ID
            metadata: Metadata dictionary to store
            
        Returns:
            True if successful, False otherwise
        """
        try:
            import json
            metadata["last_modified"] = datetime.now().isoformat()
            
            response = self.session.put(
                f"{self.server_url}/studies/{study_id}/attachments/{self.ATTACHMENT_METADATA}",
                data=json.dumps(metadata),
                headers={"Content-Type": "application/json"}
            )
            return response.status_code in [200, 201]
        except Exception as e:
            print(f"[OrthancClient] Error setting annotation metadata: {e}")
            return False