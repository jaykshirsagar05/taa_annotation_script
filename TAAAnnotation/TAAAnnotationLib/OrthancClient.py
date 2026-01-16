"""
OrthancClient.py - REST API client for Orthanc PACS server integration.

Handles:
- Authentication with Orthanc server
- Query/retrieve studies and series
- Upload/download NIfTI attachments
- Annotation lifecycle metadata management
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
    REST client for Orthanc PACS server.
    
    Uses Orthanc's built-in HTTP Basic authentication and stores
    annotation metadata as attachments (JSON) and NIfTI files as
    binary attachments for MVP simplicity.
    
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
    
    def __init__(self, server_url: str = "http://localhost:8042"):
        """
        Initialize Orthanc client.
        
        Args:
            server_url: Base URL of Orthanc server (default: http://localhost:8042)
        """
        self.server_url = server_url.rstrip('/')
        self.auth: Optional[Tuple[str, str]] = None
        self.current_user: Optional[str] = None
        self.session = requests.Session()
        
    def login(self, username: str, password: str) -> Tuple[bool, str]:
        """
        Authenticate with Orthanc server using HTTP Basic Auth.
        
        Args:
            username: Orthanc username
            password: Orthanc password
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        self.auth = (username, password)
        self.session.auth = self.auth
        
        try:
            response = self.session.get(f"{self.server_url}/system", timeout=10)
            if response.status_code == 200:
                self.current_user = username
                system_info = response.json()
                return True, f"Connected to Orthanc {system_info.get('Version', 'unknown')}"
            elif response.status_code == 401:
                self.auth = None
                self.current_user = None
                return False, "Authentication failed: Invalid credentials"
            else:
                return False, f"Connection failed: HTTP {response.status_code}"
        except requests.exceptions.ConnectionError:
            return False, f"Cannot connect to Orthanc server at {self.server_url}"
        except requests.exceptions.Timeout:
            return False, "Connection timed out"
        except Exception as e:
            return False, f"Connection error: {str(e)}"
    
    def logout(self):
        """Clear authentication state."""
        self.auth = None
        self.current_user = None
        self.session.auth = None
        
    def is_authenticated(self) -> bool:
        """Check if client is authenticated."""
        return self.auth is not None and self.current_user is not None
    
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
        Query studies filtered by annotation status.
        
        Args:
            status: AnnotationStatus enum value
            
        Returns:
            List of studies matching the status
        """
        all_studies = self.get_all_studies()
        return [s for s in all_studies if s.get("annotation_status") == status.value]
    
    def get_worklist(self, role: str = "annotator") -> List[Dict[str, Any]]:
        """
        Get worklist based on user role.
        
        Args:
            role: "annotator" or "reviewer"
            
        Returns:
            List of studies for the worklist
        """
        all_studies = self.get_all_studies()
        
        if role == "annotator":
            # Annotators see: pending, in_progress (their own), rejected
            return [s for s in all_studies 
                    if s.get("annotation_status") in [
                        AnnotationStatus.PENDING.value,
                        AnnotationStatus.REJECTED.value
                    ] or (
                        s.get("annotation_status") == AnnotationStatus.IN_PROGRESS.value 
                        and s.get("annotator") == self.current_user
                    )]
        elif role == "reviewer":
            # Reviewers see: annotated, in_review (their own)
            return [s for s in all_studies 
                    if s.get("annotation_status") == AnnotationStatus.ANNOTATED.value
                    or (
                        s.get("annotation_status") == AnnotationStatus.IN_REVIEW.value
                        and s.get("reviewer") == self.current_user
                    )]
        else:
            return all_studies
    
    # -------------------------------------------------------------------------
    # Annotation Metadata Methods
    # -------------------------------------------------------------------------
    
    def get_annotation_metadata(self, study_id: str) -> Optional[Dict[str, Any]]:
        """
        Get annotation metadata for a study.
        
        Args:
            study_id: Orthanc study ID
            
        Returns:
            Metadata dictionary or None
        """
        try:
            response = self.session.get(
                f"{self.server_url}/studies/{study_id}/attachments/{self.ATTACHMENT_METADATA}/data"
            )
            if response.status_code == 200:
                return json.loads(response.content.decode('utf-8'))
            return None
        except Exception as e:
            print(f"[OrthancClient] Error fetching metadata for {study_id}: {e}")
            return None
    
    def set_annotation_metadata(self, study_id: str, metadata: Dict[str, Any]) -> bool:
        """
        Set/update annotation metadata for a study.
        
        Args:
            study_id: Orthanc study ID
            metadata: Metadata dictionary
            
        Returns:
            Success boolean
        """
        try:
            # Ensure required fields
            metadata["last_modified"] = datetime.now().isoformat()
            metadata["last_modified_by"] = self.current_user
            
            response = self.session.put(
                f"{self.server_url}/studies/{study_id}/attachments/{self.ATTACHMENT_METADATA}",
                data=json.dumps(metadata).encode('utf-8'),
                headers={"Content-Type": "application/json"}
            )
            return response.status_code in [200, 201]
        except Exception as e:
            print(f"[OrthancClient] Error setting metadata for {study_id}: {e}")
            return False
    
    def claim_study(self, study_id: str, role: str = "annotator") -> Tuple[bool, str]:
        """
        Claim a study for annotation or review.
        
        Args:
            study_id: Orthanc study ID
            role: "annotator" or "reviewer"
            
        Returns:
            Tuple of (success, message)
        """
        metadata = self.get_annotation_metadata(study_id) or {}
        current_status = metadata.get("status", AnnotationStatus.PENDING.value)
        
        if role == "annotator":
            if current_status not in [AnnotationStatus.PENDING.value, AnnotationStatus.REJECTED.value]:
                return False, f"Cannot claim: Study status is '{current_status}'"
            
            metadata["status"] = AnnotationStatus.IN_PROGRESS.value
            metadata["annotator"] = self.current_user
            metadata["annotation_started"] = datetime.now().isoformat()
            
        elif role == "reviewer":
            if current_status != AnnotationStatus.ANNOTATED.value:
                return False, f"Cannot claim for review: Study status is '{current_status}'"
            
            metadata["status"] = AnnotationStatus.IN_REVIEW.value
            metadata["reviewer"] = self.current_user
            metadata["review_started"] = datetime.now().isoformat()
        
        # Track history
        if "history" not in metadata:
            metadata["history"] = []
        metadata["history"].append({
            "action": f"claimed_by_{role}",
            "user": self.current_user,
            "timestamp": datetime.now().isoformat(),
            "previous_status": current_status
        })
        
        if self.set_annotation_metadata(study_id, metadata):
            return True, f"Study claimed successfully as {role}"
        return False, "Failed to update metadata"
    
    def release_study(self, study_id: str) -> Tuple[bool, str]:
        """
        Release a claimed study back to pending.
        
        Args:
            study_id: Orthanc study ID
            
        Returns:
            Tuple of (success, message)
        """
        metadata = self.get_annotation_metadata(study_id) or {}
        current_status = metadata.get("status")
        
        if current_status == AnnotationStatus.IN_PROGRESS.value:
            if metadata.get("annotator") != self.current_user:
                return False, "Cannot release: Study claimed by different user"
            metadata["status"] = AnnotationStatus.PENDING.value
            metadata["annotator"] = None
            
        elif current_status == AnnotationStatus.IN_REVIEW.value:
            if metadata.get("reviewer") != self.current_user:
                return False, "Cannot release: Study claimed by different reviewer"
            metadata["status"] = AnnotationStatus.ANNOTATED.value
            metadata["reviewer"] = None
        else:
            return False, f"Cannot release: Status is '{current_status}'"
        
        metadata["history"].append({
            "action": "released",
            "user": self.current_user,
            "timestamp": datetime.now().isoformat(),
            "previous_status": current_status
        })
        
        if self.set_annotation_metadata(study_id, metadata):
            return True, "Study released successfully"
        return False, "Failed to update metadata"
    
    # -------------------------------------------------------------------------
    # NIfTI Attachment Methods
    # -------------------------------------------------------------------------
    
    def upload_nifti(self, study_id: str, file_path: str, attachment_type: int) -> bool:
        """
        Upload a NIfTI file as an attachment to a study.
        
        Args:
            study_id: Orthanc study ID
            file_path: Local path to NIfTI file
            attachment_type: Attachment type ID (use class constants)
            
        Returns:
            Success boolean
        """
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
        """
        Download a NIfTI attachment from a study.
        
        Args:
            study_id: Orthanc study ID
            attachment_type: Attachment type ID
            output_path: Optional output path (uses temp file if not provided)
            
        Returns:
            Path to downloaded file or None
        """
        try:
            response = self.session.get(
                f"{self.server_url}/studies/{study_id}/attachments/{attachment_type}/data"
            )
            
            if response.status_code == 200:
                if output_path is None:
                    # Create temp file with appropriate extension
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
                return None  # Attachment doesn't exist
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
        """
        Check which standard attachments exist for a study.
        
        Returns:
            Dictionary of attachment names to existence boolean
        """
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
        """
        Submit completed annotation for a study.
        
        Args:
            study_id: Orthanc study ID
            files: Dictionary mapping attachment names to file paths
                   Keys: "refined_mask", "centerline", "zones", "endpoints"
            notes: Annotator notes
            
        Returns:
            Tuple of (success, message)
        """
        metadata = self.get_annotation_metadata(study_id) or {}
        
        if metadata.get("annotator") != self.current_user:
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
            "timestamp": datetime.now().isoformat(),
            "files_uploaded": uploaded
        })
        
        if self.set_annotation_metadata(study_id, metadata):
            return True, f"Annotation submitted successfully ({len(uploaded)} files uploaded)"
        return False, "Failed to update metadata"
    
    def approve_annotation(self, study_id: str, comments: str = "") -> Tuple[bool, str]:
        """
        Approve an annotation and promote to ground truth.
        
        Args:
            study_id: Orthanc study ID
            comments: Reviewer comments
            
        Returns:
            Tuple of (success, message)
        """
        metadata = self.get_annotation_metadata(study_id) or {}
        
        if metadata.get("reviewer") != self.current_user:
            return False, "Cannot approve: Study not claimed by you for review"
        
        metadata["status"] = AnnotationStatus.GROUND_TRUTH.value
        metadata["review_completed"] = datetime.now().isoformat()
        metadata["reviewer_comments"] = comments
        metadata["approved_by"] = self.current_user
        
        metadata["history"].append({
            "action": "approved_as_ground_truth",
            "user": self.current_user,
            "timestamp": datetime.now().isoformat(),
            "comments": comments
        })
        
        if self.set_annotation_metadata(study_id, metadata):
            return True, "Annotation approved as ground truth"
        return False, "Failed to update metadata"
    
    def reject_annotation(self, study_id: str, reason: str) -> Tuple[bool, str]:
        """
        Reject an annotation and send back to annotator.
        
        Args:
            study_id: Orthanc study ID
            reason: Rejection reason
            
        Returns:
            Tuple of (success, message)
        """
        metadata = self.get_annotation_metadata(study_id) or {}
        
        if metadata.get("reviewer") != self.current_user:
            return False, "Cannot reject: Study not claimed by you for review"
        
        if not reason:
            return False, "Rejection reason is required"
        
        metadata["status"] = AnnotationStatus.REJECTED.value
        metadata["review_completed"] = datetime.now().isoformat()
        metadata["rejection_reason"] = reason
        metadata["rejected_by"] = self.current_user
        metadata["reviewer"] = None  # Clear reviewer so annotator can reclaim
        
        metadata["history"].append({
            "action": "rejected",
            "user": self.current_user,
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
        """
        Upload initial NIfTI data for a study (admin/data prep function).
        
        Args:
            study_id: Orthanc study ID
            ct_path: Path to CT NIfTI file
            unified_mask_path: Path to unified mask NIfTI
            merged_mask_path: Path to merged mask NIfTI
            
        Returns:
            Tuple of (success, message)
        """
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
        
        # Initialize metadata
        metadata = {
            "status": AnnotationStatus.PENDING.value,
            "created": datetime.now().isoformat(),
            "created_by": self.current_user,
            "history": [{
                "action": "data_uploaded",
                "user": self.current_user,
                "timestamp": datetime.now().isoformat()
            }]
        }
        
        if self.set_annotation_metadata(study_id, metadata):
            return True, "Initial data uploaded successfully"
        return False, "Failed to set metadata"
    
    def get_statistics(self) -> Dict[str, int]:
        """
        Get annotation statistics across all studies.
        
        Returns:
            Dictionary of status counts
        """
        all_studies = self.get_all_studies()
        stats = {status.value: 0 for status in AnnotationStatus}
        
        for study in all_studies:
            status = study.get("annotation_status", AnnotationStatus.PENDING.value)
            if status in stats:
                stats[status] += 1
        
        stats["total"] = len(all_studies)
        return stats