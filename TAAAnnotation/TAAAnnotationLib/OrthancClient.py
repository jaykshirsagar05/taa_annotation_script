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
        
        # Dashboard availability flag
        # When False, all AdminDashboard API calls are skipped and
        # Orthanc-direct fallback paths are used instead.
        self.dashboard_available: bool = True
        
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

    def login_direct(self, username: str, password: str,
                     role: str = "annotator") -> Tuple[bool, str]:
        """
        Authenticate directly with Orthanc using static credentials.

        Use this when AdminDashboard is unreachable. The user picks their
        role manually. All subsequent AdminDashboard API calls are skipped
        and Orthanc-direct fallback paths are used instead.

        Args:
            username: Orthanc basic-auth username
            password: Orthanc basic-auth password
            role: User-selected role (annotator / reviewer / admin)

        Returns:
            Tuple of (success: bool, message: str)
        """
        if role not in ("annotator", "reviewer", "admin"):
            return False, f"Invalid role: {role}"

        # Set Orthanc basic-auth so all subsequent requests use it
        self.set_orthanc_credentials(username, password)

        # Verify Orthanc is reachable with these credentials
        orthanc_ok, orthanc_msg = self._verify_orthanc_connection()
        if not orthanc_ok:
            # Clear creds on failure
            self.orthanc_auth = None
            self.session.auth = None
            return False, f"Orthanc authentication failed: {orthanc_msg}"

        # Populate authentication state without AdminDashboard
        self.current_user = username
        self.user_role = role
        self.user_id = None
        self.auth_token = None  # No JWT in direct mode
        self.assigned_series = []
        self.dashboard_available = False

        print(f"[OrthancClient] Direct login as '{username}' with role '{role}' "
              f"(AdminDashboard offline)")
        return True, (f"Logged in directly to Orthanc as {username} ({role}) "
                      f"— AdminDashboard offline")
    
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
        # Close all connections in the session to avoid stale connections
        try:
            self.session.close()
        except Exception:
            pass  # Session may already be closed
        
        # Create a fresh session for next login
        self.session = requests.Session()
        
        # Clear auth state
        self.auth_token = None
        self.current_user = None
        self.user_role = None
        self.user_id = None
        self.assigned_series = []
        self.orthanc_auth = None
        self.session.auth = None
        self.dashboard_available = True  # Reset for next login attempt
        
    def is_authenticated(self) -> bool:
        """Check if client is authenticated with AdminDashboard."""
        return self.auth_token is not None and self.current_user is not None
    
    def get_role(self) -> Optional[str]:
        """Get the authenticated user's role from AdminDashboard."""
        return self.user_role
    
    def validate_token(self) -> Tuple[bool, str]:
        """Validate the current token with AdminDashboard."""
        if not self.dashboard_available:
            # In direct mode there is no token; treat session as valid
            # as long as Orthanc is reachable.
            ok, msg = self._verify_orthanc_connection()
            return ok, msg if ok else f"Orthanc unreachable: {msg}"

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
        Falls back to direct Orthanc query when dashboard is unavailable.
        
        Args:
            status: AnnotationStatus enum value
            
        Returns:
            List of studies matching the status
        """
        if not self.dashboard_available:
            # Skip AdminDashboard entirely — query Orthanc directly
            all_studies = self.get_all_studies()
            return [s for s in all_studies if s.get("annotation_status") == status.value]

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
        
        if not self.dashboard_available:
            # Skip AdminDashboard — use Orthanc-direct fallback
            return self._get_worklist_fallback(effective_role)

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
        
        if not self.dashboard_available:
            return self._claim_study_fallback(study_id, effective_role)

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
        if not self.dashboard_available:
            return self._release_study_fallback(study_id)

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
        """Download a NIfTI attachment from a study.

        Uses ``stream=True`` so the raw bytes are never touched by
        ``requests``' automatic ``Content-Encoding`` decompression.
        This prevents the library from silently stripping the gzip
        layer of ``.nii.gz`` files when the server (or a proxy)
        returns a ``Content-Encoding: gzip`` header.
        """
        try:
            response = self.session.get(
                f"{self.server_url}/studies/{study_id}/attachments/{attachment_type}/data",
                stream=True,
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
                
                # Read the raw, undecoded bytes to avoid Content-Encoding
                # decompression that would corrupt .nii.gz files.
                # decode_content=False prevents urllib3 from stripping gzip.
                raw_data = response.raw.read(decode_content=False)
                with open(output_path, 'wb') as f:
                    f.write(raw_data)
                
                # Validate and fix NIfTI gzip files
                output_path = self._validate_nifti_file(output_path)
                
                return output_path
            elif response.status_code == 404:
                return None
            else:
                print(f"[OrthancClient] Download failed: HTTP {response.status_code}")
                return None
        except Exception as e:
            print(f"[OrthancClient] Error downloading attachment: {e}")
            return None

    @staticmethod
    def _validate_nifti_file(filepath: str) -> str:
        """
        Sniff the first bytes of a downloaded file and rename it so
        the extension matches the actual format.

        Handles three mismatch scenarios:
        1. NRRD / seg.nrrd data saved as .nii.gz  (upload script
           stored a .seg.nrrd mask under attachment 1026)
        2. Raw (uncompressed) NIfTI saved as .nii.gz  (HTTP layer
           stripped the gzip Content-Encoding)
        3. Unknown non-gzip data saved as .nii.gz

        Returns:
            The (possibly updated) file path.
        """
        if not filepath.endswith('.nii.gz'):
            return filepath

        try:
            with open(filepath, 'rb') as f:
                header = f.read(512)

            if len(header) < 4:
                print(f"[OrthancClient] WARNING — downloaded file is too small: {filepath}")
                return filepath

            # 1. Properly gzip-compressed — nothing to do.
            if header[0] == 0x1f and header[1] == 0x8b:
                return filepath

            # 2. NRRD format — starts with 'NRRD' magic bytes.
            #    The upload script may have stored a .seg.nrrd mask
            #    under the unified-mask attachment slot.
            if header[:4] == b'NRRD':
                # Determine if it is a segmentation NRRD (.seg.nrrd)
                # by looking for "Segment" keys in the header.
                is_seg_nrrd = b'Segment' in header
                if is_seg_nrrd:
                    new_path = filepath.replace('.nii.gz', '.seg.nrrd')
                else:
                    new_path = filepath.replace('.nii.gz', '.nrrd')
                os.rename(filepath, new_path)
                print(f"[OrthancClient] File is NRRD (not NIfTI) — renamed to "
                      f"{os.path.basename(new_path)}")
                return new_path

            # 3. Raw (uncompressed) NIfTI — header size 348 (NIfTI-1)
            #    or 540 (NIfTI-2) as little-endian int32 at offset 0.
            import struct
            hdr_size = struct.unpack('<i', header[:4])[0]
            if hdr_size in (348, 540):
                new_path = filepath[:-3]  # strip trailing .gz
                os.rename(filepath, new_path)
                print(f"[OrthancClient] File was raw NIfTI despite .nii.gz extension "
                      f"— renamed to {os.path.basename(new_path)}")
                return new_path

            # 4. Unknown format — try re-gzipping in case it helps.
            import gzip
            with open(filepath, 'rb') as f_in:
                raw = f_in.read()
            with gzip.open(filepath, 'wb') as f_out:
                f_out.write(raw)
            print(f"[OrthancClient] Re-gzipped file (was not gzip): "
                  f"{os.path.basename(filepath)}")
            return filepath

        except Exception as e:
            print(f"[OrthancClient] WARNING — file validation failed for "
                  f"{filepath}: {e}")
            return filepath
    
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

    def detect_dataset_profile(self, study_id: str):
        """
        Auto-detect the dataset profile for a study based on which
        attachments are present on Orthanc.

        Returns:
            DatasetProfile instance, or None if no profile matches.
        """
        from .DatasetProfile import detect_profile_from_attachments
        attachments = self.get_study_attachments(study_id)
        return detect_profile_from_attachments(attachments)

    def download_study_files(self, study_id: str, patient_id: str, profile, temp_dir: str) -> Dict[str, Optional[str]]:
        """
        Download all files required by a dataset profile.

        Args:
            study_id:   Orthanc study identifier.
            patient_id: Patient ID (used for local filenames).
            profile:    DatasetProfile instance describing what to download.
            temp_dir:   Local directory for downloaded files.

        Returns:
            dict of {logical_name: local_path_or_None}
        """
        from .DatasetProfile import download_filename

        paths: Dict[str, Optional[str]] = {}

        # Download required attachments
        for logical_name, att_id in profile.required_attachments.items():
            fname = download_filename(logical_name, patient_id)
            local_path = self.download_nifti(
                study_id, att_id,
                os.path.join(temp_dir, fname)
            )
            # For centerline files: detect actual format and rename if needed
            if logical_name == "centerline" and local_path:
                local_path = self._detect_and_fix_centerline_ext(local_path)
            paths[logical_name] = local_path

        # Download optional attachments (no error on missing)
        for logical_name, att_id in profile.optional_attachments.items():
            fname = download_filename(logical_name, patient_id)
            local_path = self.download_nifti(
                study_id, att_id,
                os.path.join(temp_dir, fname)
            )
            paths[logical_name] = local_path

        return paths

    @staticmethod
    def _detect_and_fix_centerline_ext(filepath: str) -> str:
        """
        Sniff the first bytes of a downloaded centerline file and rename
        it to '.vtp' if its content is VTK XML (VTP) rather than VTK legacy.

        Slicer picks the reader based on extension: .vtk → legacy reader,
        .vtp → XML PolyData reader.  Getting this wrong causes a silent load
        failure, so we fix it here at download time.

        Returns:
            The (possibly updated) file path.
        """
        try:
            with open(filepath, 'rb') as f:
                header = f.read(256)

            is_xml = (header.lstrip()[:5] == b'<?xml'
                      or b'<VTKFile' in header
                      or b'type="PolyData"' in header)

            if is_xml and not filepath.endswith('.vtp'):
                new_path = filepath.rsplit('.', 1)[0] + '.vtp'
                os.rename(filepath, new_path)
                print(f"[OrthancClient] Centerline is VTP format — renamed to {os.path.basename(new_path)}")
                return new_path

            is_legacy = header[:1] == b'#' or header.lstrip()[:13] == b'# vtk DataFil'
            if is_legacy and not filepath.endswith('.vtk'):
                new_path = filepath.rsplit('.', 1)[0] + '.vtk'
                os.rename(filepath, new_path)
                print(f"[OrthancClient] Centerline is VTK legacy format — renamed to {os.path.basename(new_path)}")
                return new_path
        except Exception as e:
            print(f"[OrthancClient] Warning: could not detect centerline format: {e}")

        return filepath
    
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
        
        if "history" not in metadata:
            metadata["history"] = []
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
        
        if "history" not in metadata:
            metadata["history"] = []
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
        
        if "history" not in metadata:
            metadata["history"] = []
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