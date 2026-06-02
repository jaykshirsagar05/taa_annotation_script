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
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum

from . import config as _cfg


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
    
    def __init__(self, orthanc_url: str = None, admin_dashboard_url: str = None):
        """
        Initialize Orthanc client with AdminDashboard integration.
        URLs and Orthanc credentials are read from config.py; the parameters
        here exist only for programmatic overrides.
        """
        self.server_url = (orthanc_url or _cfg.ORTHANC_URL).rstrip('/')
        self.admin_url = (admin_dashboard_url or _cfg.ADMIN_DASHBOARD_URL).rstrip('/')
        
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

        # DICOMweb availability cache — None means not yet probed.
        # Reset on logout/credential change so a reconnect re-probes.
        self._dicomweb_available: Optional[bool] = None

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

                    # Set Orthanc basic-auth using the service account from config.
                    # Dashboard user credentials are separate from Orthanc credentials.
                    self.set_orthanc_credentials(_cfg.ORTHANC_USERNAME, _cfg.ORTHANC_PASSWORD)

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
        self.dashboard_available = True   # Reset for next login attempt
        self._dicomweb_available = None   # Re-probe on next connection
        
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
                
                # Stream in chunks to avoid loading large files (e.g. CT NIfTI)
                # entirely into memory. decode_content=False prevents urllib3
                # from stripping the gzip layer of .nii.gz files.
                with open(output_path, 'wb') as f:
                    for chunk in response.raw.stream(1 << 20, decode_content=False):
                        f.write(chunk)
                
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
            "ct_dicom": self._study_has_ct_dicom(study_id),
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

        def _fetch_one(logical_name: str, att_id: int, required: bool) -> tuple:
            if logical_name == "ct":
                local_path = self._download_ct_with_fallback(study_id, patient_id, temp_dir)
            else:
                fname = download_filename(logical_name, patient_id)
                local_path = self.download_nifti(
                    study_id, att_id,
                    os.path.join(temp_dir, fname)
                )
            if logical_name == "centerline" and local_path:
                local_path = self._detect_and_fix_centerline_ext(local_path)
            return logical_name, local_path

        tasks = {
            **{name: (att_id, True)  for name, att_id in profile.required_attachments.items()},
            **{name: (att_id, False) for name, att_id in profile.optional_attachments.items()},
        }

        paths: Dict[str, Optional[str]] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_fetch_one, name, att_id, req): name
                for name, (att_id, req) in tasks.items()
            }
            for future in as_completed(futures):
                logical_name, local_path = future.result()
                paths[logical_name] = local_path

        return paths

    def _download_ct_with_fallback(self, study_id: str, patient_id: str, temp_dir: str) -> Optional[str]:
        """
        Download CT data for a study.

        Priority order:
            1) CT NIfTI attachment (type 1025)
            2) DICOM CT series downloaded from the Orthanc study

        Returns:
            Local file path (NIfTI) or folder path (DICOM directory), or None.
        """
        from .DatasetProfile import download_filename

        ct_fname = download_filename("ct", patient_id)
        nifti_path = self.download_nifti(
            study_id,
            self.ATTACHMENT_CT_NIFTI,
            os.path.join(temp_dir, ct_fname),
        )
        if nifti_path:
            print(f"[OrthancClient] CT downloaded as NIfTI attachment: {os.path.basename(nifti_path)}")
            return nifti_path

        dicom_dir = os.path.join(temp_dir, f"{patient_id}_ct_dicom")
        if self._download_ct_dicom_series(study_id, dicom_dir):
            print(f"[OrthancClient] CT downloaded as DICOM series: {dicom_dir}")
            return dicom_dir

        print("[OrthancClient] CT download failed (neither NIfTI attachment nor DICOM CT series available)")
        return None

    def _study_has_ct_dicom(self, study_id: str) -> bool:
        """Return True when the study contains at least one CT series with instances."""
        try:
            response = self.session.get(f"{self.server_url}/studies/{study_id}")
            if response.status_code != 200:
                return False
            series_ids = response.json().get("Series", [])
            for series_id in series_ids:
                series_resp = self.session.get(f"{self.server_url}/series/{series_id}")
                if series_resp.status_code != 200:
                    continue
                series = series_resp.json()
                modality = series.get("MainDicomTags", {}).get("Modality", "")
                instances = series.get("Instances", [])
                if modality == "CT" and len(instances) > 0:
                    return True
        except Exception as e:
            print(f"[OrthancClient] Error checking CT DICOM availability: {e}")
        return False

    def _download_ct_dicom_series(self, study_id: str, output_dir: str) -> bool:
        """
        Download the CT DICOM series from a study into output_dir.

        Selects the CT series with the most instances, then streams each
        instance individually in parallel — no ZIP archive is buffered in RAM.
        """
        try:
            response = self.session.get(f"{self.server_url}/studies/{study_id}")
            if response.status_code != 200:
                return False

            series_ids = response.json().get("Series", [])
            best_series_id = None
            best_count = -1
            for series_id in series_ids:
                series_resp = self.session.get(f"{self.server_url}/series/{series_id}")
                if series_resp.status_code != 200:
                    continue
                series = series_resp.json()
                modality = series.get("MainDicomTags", {}).get("Modality", "")
                instances = series.get("Instances", [])
                if modality != "CT":
                    continue
                count = len(instances)
                if count > best_count:
                    best_series_id = series_id
                    best_count = count

            if not best_series_id:
                return False

            return self._stream_series_instances_to_dir(best_series_id, output_dir)
        except Exception as e:
            print(f"[OrthancClient] Error downloading CT DICOM series: {e}")
            return False

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
            # Explicitly ignore non-annotation payloads (e.g., CT volume);
            # CT is source data and should not be re-uploaded at submission time.
            if name not in file_mapping:
                continue
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

    # =========================================================================
    # Series-Level Annotation Workflow
    # =========================================================================
    # Each CT series in Orthanc is treated as one annotatable unit.
    # Annotations (masks, centerline, zones) are stored as attachments on the
    # series using the same type IDs as the study-level workflow.
    # For initial segmentation masks that were uploaded at the study level
    # (the existing workflow), downloads fall back transparently to the
    # parent study.
    # =========================================================================

    def get_all_ct_series(self) -> List[Dict[str, Any]]:
        """
        Return info dicts for every CT-modality series in Orthanc.

        Each item is a complete annotation work-item that can be shown as a
        row in the worklist table.
        """
        try:
            response = self.session.get(f"{self.server_url}/series")
            response.raise_for_status()
            all_ids = response.json()
        except Exception as e:
            print(f"[OrthancClient] Error listing series: {e}")
            return []

        ct_series = []
        for series_id in all_ids:
            info = self.get_series_info(series_id)
            if info and info.get("modality") == "CT":
                ct_series.append(info)
        return ct_series

    def get_series_info(self, series_id: str) -> Optional[Dict[str, Any]]:
        """
        Get full display info for a series, including patient data from the
        parent study and current annotation status from series-level metadata.

        Returns:
            Dict suitable for a worklist row, or None on error.
        """
        try:
            response = self.session.get(f"{self.server_url}/series/{series_id}")
            response.raise_for_status()
            series_data = response.json()
        except Exception as e:
            print(f"[OrthancClient] Error fetching series {series_id}: {e}")
            return None

        parent_study_id = series_data.get("ParentStudy", "")
        tags = series_data.get("MainDicomTags", {})

        # Patient info lives on the parent study
        patient_id = patient_name = study_date = ""
        try:
            study_resp = self.session.get(f"{self.server_url}/studies/{parent_study_id}")
            if study_resp.status_code == 200:
                sd = study_resp.json()
                patient_id  = sd.get("PatientMainDicomTags", {}).get("PatientID", "")
                patient_name = sd.get("PatientMainDicomTags", {}).get("PatientName", "")
                study_date   = sd.get("MainDicomTags", {}).get("StudyDate", "")
        except Exception:
            pass

        metadata = self.get_series_annotation_metadata(series_id)
        return {
            "orthanc_series_id": series_id,
            "orthanc_study_id":  parent_study_id,
            "patient_id":        patient_id,
            "patient_name":      patient_name,
            "study_date":        study_date,
            "series_description": tags.get("SeriesDescription", ""),
            "series_number":     tags.get("SeriesNumber", ""),
            "modality":          tags.get("Modality", "Unknown"),
            "instance_count":    len(series_data.get("Instances", [])),
            "annotation_status": (metadata.get("status", AnnotationStatus.PENDING.value)
                                  if metadata else AnnotationStatus.PENDING.value),
            "annotator":  metadata.get("annotator")  if metadata else None,
            "reviewer":   metadata.get("reviewer")   if metadata else None,
            "annotation_metadata": metadata,
        }

    def get_series_annotation_metadata(self, series_id: str) -> Optional[Dict[str, Any]]:
        """Read annotation metadata stored at the series level."""
        try:
            response = self.session.get(
                f"{self.server_url}/series/{series_id}"
                f"/attachments/{self.ATTACHMENT_METADATA}/data"
            )
            if response.status_code == 200:
                return response.json()
            if response.status_code == 404:
                return {
                    "status": AnnotationStatus.PENDING.value,
                    "created": None, "annotator": None,
                    "reviewer": None, "history": [],
                }
            return None
        except Exception as e:
            print(f"[OrthancClient] Error getting series metadata {series_id}: {e}")
            return None

    def set_series_annotation_metadata(self, series_id: str,
                                        metadata: Dict[str, Any]) -> bool:
        """Write annotation metadata to the series level."""
        try:
            import json as _json
            metadata["last_modified"] = datetime.now().isoformat()
            response = self.session.put(
                f"{self.server_url}/series/{series_id}"
                f"/attachments/{self.ATTACHMENT_METADATA}",
                data=_json.dumps(metadata),
                headers={"Content-Type": "application/json"},
            )
            return response.status_code in (200, 201)
        except Exception as e:
            print(f"[OrthancClient] Error setting series metadata {series_id}: {e}")
            return False

    def has_attachment_on_series(self, series_id: str, attachment_type: int) -> bool:
        """Return True when the attachment exists on a series."""
        try:
            return self.session.get(
                f"{self.server_url}/series/{series_id}/attachments/{attachment_type}"
            ).status_code == 200
        except Exception:
            return False

    def get_series_attachments_info(self, series_id: str,
                                     parent_study_id: str = "") -> Dict[str, bool]:
        """
        Check which annotation attachments exist for a CT series.

        Looks at series-level attachments first.  For the initial masks
        (unified and merged) it also falls back to the parent study so that
        masks uploaded at study level (the existing workflow) are still found.

        Args:
            series_id:       Orthanc series ID.
            parent_study_id: Parent study ID; resolved automatically when empty.

        Returns:
            Dict of {logical_name: bool}.
        """
        if not parent_study_id:
            try:
                r = self.session.get(f"{self.server_url}/series/{series_id}")
                if r.status_code == 200:
                    parent_study_id = r.json().get("ParentStudy", "")
            except Exception:
                pass

        def _has(url: str) -> bool:
            try:
                return self.session.get(url).status_code == 200
            except Exception:
                return False

        def _series_or_study(att_id: int) -> bool:
            if _has(f"{self.server_url}/series/{series_id}/attachments/{att_id}"):
                return True
            if parent_study_id:
                return _has(
                    f"{self.server_url}/studies/{parent_study_id}/attachments/{att_id}"
                )
            return False

        # Determine instance count and check for DICOM SEG in parent study
        ct_dicom = False
        seg_dicom = False
        try:
            r = self.session.get(f"{self.server_url}/series/{series_id}")
            if r.status_code == 200:
                ct_dicom = len(r.json().get("Instances", [])) > 0
        except Exception:
            pass

        if parent_study_id:
            seg_dicom = self.get_dicom_seg_series_id(parent_study_id) is not None

        return {
            "ct_dicom":    ct_dicom,
            "seg_dicom":   seg_dicom,
            "ct_nifti":    _series_or_study(self.ATTACHMENT_CT_NIFTI),
            "unified_mask": _series_or_study(self.ATTACHMENT_UNIFIED_MASK),
            "merged_mask": _series_or_study(self.ATTACHMENT_MERGED_MASK),
            "refined_mask": _has(
                f"{self.server_url}/series/{series_id}/attachments/{self.ATTACHMENT_REFINED_MASK}"
            ),
            "centerline": _has(
                f"{self.server_url}/series/{series_id}/attachments/{self.ATTACHMENT_CENTERLINE}"
            ),
            "zones": _has(
                f"{self.server_url}/series/{series_id}/attachments/{self.ATTACHMENT_ZONES}"
            ),
            "endpoints": _has(
                f"{self.server_url}/series/{series_id}/attachments/{self.ATTACHMENT_ENDPOINTS}"
            ),
            "notes": _has(
                f"{self.server_url}/series/{series_id}/attachments/{self.ATTACHMENT_NOTES}"
            ),
            "metadata": _has(
                f"{self.server_url}/series/{series_id}/attachments/{self.ATTACHMENT_METADATA}"
            ),
        }

    def detect_dataset_profile_for_series(self, series_id: str):
        """
        Auto-detect the dataset profile for a CT series based on the
        attachments available on the series (and its parent study).

        Returns:
            DatasetProfile instance, or None if no profile matches.
        """
        from .DatasetProfile import detect_profile_from_attachments
        info = self.get_series_info(series_id)
        parent_study_id = info.get("orthanc_study_id", "") if info else ""
        attachments = self.get_series_attachments_info(series_id, parent_study_id)
        return detect_profile_from_attachments(attachments)

    def download_nifti_from_series(self, series_id: str, attachment_type: int,
                                    output_path: Optional[str] = None) -> Optional[str]:
        """
        Download an attachment from a series, with transparent fallback to the
        parent study for attachments that were uploaded at study level.
        """
        url = (f"{self.server_url}/series/{series_id}"
               f"/attachments/{attachment_type}/data")
        try:
            resp = self.session.get(url, stream=True)
            if resp.status_code == 200:
                if output_path is None:
                    suffix = self._get_extension_for_type(attachment_type)
                    fd, output_path = tempfile.mkstemp(suffix=suffix)
                    os.close(fd)
                with open(output_path, "wb") as f:
                    for chunk in resp.raw.stream(1 << 20, decode_content=False):
                        f.write(chunk)
                return self._validate_nifti_file(output_path)
            if resp.status_code != 404:
                print(f"[OrthancClient] Series download HTTP {resp.status_code} "
                      f"(att {attachment_type})")
        except Exception as e:
            print(f"[OrthancClient] Series download error: {e}")

        # Fallback: parent study level
        try:
            r = self.session.get(f"{self.server_url}/series/{series_id}")
            if r.status_code == 200:
                parent_study_id = r.json().get("ParentStudy", "")
                if parent_study_id:
                    return self.download_nifti(parent_study_id, attachment_type,
                                               output_path)
        except Exception as e:
            print(f"[OrthancClient] Study-level download fallback error: {e}")
        return None

    def upload_nifti_to_series(self, series_id: str, file_path: str,
                                attachment_type: int) -> bool:
        """Upload a file as an attachment on a series."""
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            response = self.session.put(
                f"{self.server_url}/series/{series_id}/attachments/{attachment_type}",
                data=data,
                headers={"Content-Type": "application/octet-stream"},
            )
            return response.status_code in (200, 201)
        except Exception as e:
            print(f"[OrthancClient] Error uploading to series: {e}")
            return False

    def _download_dicom_series(self, series_id: str, output_dir: str) -> bool:
        """Stream all instances of a DICOM series into output_dir in parallel."""
        return self._stream_series_instances_to_dir(series_id, output_dir)

    def download_files_for_series(self, series_id: str, patient_id: str,
                                   profile, temp_dir: str,
                                   progress_callback=None,
                                   ct_ready_callback=None) -> Dict[str, Optional[str]]:
        """Download all files required by a DatasetProfile for a CT series.

        For the DICOM_NATIVE profile, CT instances and DICOM SEG are streamed
        in parallel from the native DICOM series (no NIfTI attachments used).
        All other profiles fetch Orthanc attachments, also in parallel.

        ``progress_callback``, when provided, is forwarded to the DICOM_NATIVE
        CT streamer and called as ``progress_callback(n)`` after each instance
        is written to disk.  For other profiles it has no effect.

        Returns:
            dict of {logical_name: local_path_or_None}
        """
        from .DatasetProfile import DatasetProfile as DP, download_filename

        if profile.profile_type == DP.DICOM_NATIVE:
            return self._download_dicom_native_profile(
                series_id, patient_id, profile, temp_dir,
                progress_callback=progress_callback,
                ct_ready_callback=ct_ready_callback,
            )

        # --- All non-native profiles: fetch attachments in parallel ---
        paths: Dict[str, Optional[str]] = {}

        def _fetch_one(logical_name: str, att_id: int) -> tuple:
            if logical_name == "ct":
                # NIfTI attachment first, then DICOM series fallback
                ct_fname = download_filename("ct", patient_id)
                local = self.download_nifti_from_series(
                    series_id, self.ATTACHMENT_CT_NIFTI,
                    os.path.join(temp_dir, ct_fname),
                )
                if local:
                    print(f"[OrthancClient] CT from NIfTI attachment: "
                          f"{os.path.basename(local)}")
                else:
                    dicom_dir = os.path.join(temp_dir, f"{patient_id}_ct_dicom")
                    if self._stream_series_instances_to_dir(series_id, dicom_dir):
                        local = dicom_dir
                        print(f"[OrthancClient] CT from DICOM series: {dicom_dir}")
                    else:
                        print("[OrthancClient] CT download failed for series")
                return logical_name, local

            if att_id is None:
                return logical_name, None

            fname = download_filename(logical_name, patient_id)
            local = self.download_nifti_from_series(
                series_id, att_id, os.path.join(temp_dir, fname)
            )
            if logical_name == "centerline" and local:
                local = self._detect_and_fix_centerline_ext(local)
            return logical_name, local

        tasks = {
            **{name: att_id for name, att_id in profile.required_attachments.items()},
            **{name: att_id for name, att_id in profile.optional_attachments.items()},
        }

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_fetch_one, name, att_id): name
                for name, att_id in tasks.items()
            }
            for future in as_completed(futures):
                logical_name, local_path = future.result()
                paths[logical_name] = local_path

        return paths

    def query_series_by_status(self, status: AnnotationStatus) -> List[Dict[str, Any]]:
        """Filter the CT series list by annotation status."""
        return [s for s in self.get_all_ct_series()
                if s.get("annotation_status") == status.value]

    def get_series_worklist(self, role: str = None) -> List[Dict[str, Any]]:
        """
        Return the annotatable CT series relevant for the given role.

        annotator → pending, rejected, own in-progress
        reviewer  → annotated, own in-review
        admin/None → all CT series
        """
        effective_role = role or self.user_role
        all_series = self.get_all_ct_series()
        if not effective_role:
            return all_series
        if effective_role == "annotator":
            return [s for s in all_series if (
                s.get("annotation_status") in (
                    AnnotationStatus.PENDING.value,
                    AnnotationStatus.REJECTED.value,
                )
                or (s.get("annotation_status") == AnnotationStatus.IN_PROGRESS.value
                    and s.get("annotator") == self.current_user)
            )]
        if effective_role == "reviewer":
            return [s for s in all_series if (
                s.get("annotation_status") == AnnotationStatus.ANNOTATED.value
                or (s.get("annotation_status") == AnnotationStatus.IN_REVIEW.value
                    and s.get("reviewer") == self.current_user)
            )]
        return all_series

    def claim_series(self, series_id: str, role: str = None) -> Tuple[bool, str]:
        """Claim a CT series for annotation or review."""
        effective_role = role or self.user_role
        if not effective_role:
            return False, "No role assigned — please contact admin"
        if not self.dashboard_available:
            return self._claim_series_fallback(series_id, effective_role)
        try:
            response = requests.post(
                f"{self.admin_url}/series/api/{series_id}/claim",
                headers=self._get_auth_headers(),
                json={"role": effective_role},
                timeout=30,
            )
            data = response.json()
            if response.status_code == 200 and data.get("success"):
                return True, data.get("message", "Series claimed successfully")
            # AdminDashboard /series endpoint may not be implemented yet — fall back
            return self._claim_series_fallback(series_id, effective_role)
        except Exception as e:
            print(f"[OrthancClient] Error claiming series via AdminDashboard: {e}")
            return self._claim_series_fallback(series_id, effective_role)

    def _claim_series_fallback(self, series_id: str, role: str) -> Tuple[bool, str]:
        """Claim a series directly via Orthanc series-level metadata."""
        metadata = self.get_series_annotation_metadata(series_id) or {}
        current_status = metadata.get("status", AnnotationStatus.PENDING.value)

        if role in ("annotator", "admin") and current_status in (
            AnnotationStatus.PENDING.value, AnnotationStatus.REJECTED.value
        ):
            metadata["status"] = AnnotationStatus.IN_PROGRESS.value
            metadata["annotator"] = self.current_user
            metadata["annotation_started"] = datetime.now().isoformat()
        elif role in ("reviewer", "admin") and current_status == AnnotationStatus.ANNOTATED.value:
            metadata["status"] = AnnotationStatus.IN_REVIEW.value
            metadata["reviewer"] = self.current_user
            metadata["review_started"] = datetime.now().isoformat()
        else:
            return False, f"Cannot claim: series status is '{current_status}'"

        metadata.setdefault("history", []).append({
            "action": f"claimed_by_{role}",
            "user": self.current_user,
            "user_id": self.user_id,
            "timestamp": datetime.now().isoformat(),
            "previous_status": current_status,
        })
        if self.set_series_annotation_metadata(series_id, metadata):
            return True, f"Series claimed successfully as {role}"
        return False, "Failed to update metadata"

    def release_series(self, series_id: str) -> Tuple[bool, str]:
        """Release a claimed series back to its previous status."""
        if not self.dashboard_available:
            return self._release_series_fallback(series_id)
        try:
            response = requests.post(
                f"{self.admin_url}/series/api/{series_id}/release",
                headers=self._get_auth_headers(),
                timeout=30,
            )
            data = response.json()
            if response.status_code == 200 and data.get("success"):
                return True, data.get("message", "Series released")
            return self._release_series_fallback(series_id)
        except Exception as e:
            print(f"[OrthancClient] Error releasing series via AdminDashboard: {e}")
            return self._release_series_fallback(series_id)

    def _release_series_fallback(self, series_id: str) -> Tuple[bool, str]:
        metadata = self.get_series_annotation_metadata(series_id) or {}
        current_status = metadata.get("status")
        if current_status == AnnotationStatus.IN_PROGRESS.value:
            if metadata.get("annotator") != self.current_user and self.user_role != "admin":
                return False, "Cannot release: claimed by a different user"
            metadata["status"] = AnnotationStatus.PENDING.value
            metadata["annotator"] = None
        elif current_status == AnnotationStatus.IN_REVIEW.value:
            if metadata.get("reviewer") != self.current_user and self.user_role != "admin":
                return False, "Cannot release: claimed by a different reviewer"
            metadata["status"] = AnnotationStatus.ANNOTATED.value
            metadata["reviewer"] = None
        else:
            return False, f"Cannot release: status is '{current_status}'"
        metadata.setdefault("history", []).append({
            "action": "released",
            "user": self.current_user,
            "user_id": self.user_id,
            "timestamp": datetime.now().isoformat(),
            "previous_status": current_status,
        })
        if self.set_series_annotation_metadata(series_id, metadata):
            return True, "Series released successfully"
        return False, "Failed to update metadata"

    # -------------------------------------------------------------------------
    # DICOM-native streaming helpers (CT instance streaming + DICOM SEG)
    # -------------------------------------------------------------------------

    def _stream_series_instances_to_dir(self, series_id: str, output_dir: str,
                                         max_workers: int = 8,
                                         progress_callback=None) -> bool:
        """Download all DICOM instances in a series to output_dir.

        Tries three paths in order of preference:

        1. WADO-RS multipart streaming (fastest perceived load — instances land
           on disk as the HTTP response arrives, no ZIP buffering needed).
           Only attempted when Orthanc's DICOMweb plugin is available.
        2. Single ZIP archive download (/series/{id}/archive).
        3. Per-instance parallel download (/instances/{id}/file).

        ``progress_callback``, when provided, is forwarded to the WADO-RS path
        and called as ``progress_callback(n)`` after each instance is written.

        Returns True when at least one .dcm file was written.
        """
        import shutil

        os.makedirs(output_dir, exist_ok=True)

        # --- Primary: WADO-RS multipart streaming ---
        if self.check_dicomweb_available():
            uids = self._get_dicom_uids_for_series(series_id)
            if uids:
                study_uid, series_uid = uids
                n = self._wado_rs_stream_series(
                    study_uid, series_uid, output_dir, progress_callback
                )
                if n > 0:
                    return True
                print("[OrthancClient] WADO-RS returned 0 instances — "
                      "falling back to ZIP archive")

        archive_tmp = os.path.join(output_dir, "_series_archive.zip")

        # --- Secondary: single ZIP archive download ---
        try:
            r = self.session.get(
                f"{self.server_url}/series/{series_id}/archive",
                stream=True,
                timeout=(15, 600),
            )
            if r.status_code == 200:
                with open(archive_tmp, "wb") as fz:
                    for chunk in r.raw.stream(1 << 20, decode_content=False):
                        fz.write(chunk)

                dcm_count = 0
                with zipfile.ZipFile(archive_tmp) as zf:
                    for member in zf.infolist():
                        basename = os.path.basename(member.filename)
                        if not basename:
                            continue
                        out_name = (basename if basename.lower().endswith(".dcm")
                                    else basename + ".dcm")
                        with zf.open(member) as src, \
                                open(os.path.join(output_dir, out_name), "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        dcm_count += 1

                os.remove(archive_tmp)
                if dcm_count > 0:
                    print(f"[OrthancClient] ZIP archive: extracted {dcm_count} instances "
                          f"→ {os.path.basename(output_dir)}")
                    return True
        except Exception as e:
            print(f"[OrthancClient] ZIP archive failed ({e}), falling back to per-instance")
            if os.path.exists(archive_tmp):
                try:
                    os.remove(archive_tmp)
                except OSError:
                    pass

        # --- Fallback: per-instance parallel download ---
        try:
            resp = self.session.get(f"{self.server_url}/series/{series_id}")
            if resp.status_code != 200:
                return False
            instance_ids = resp.json().get("Instances", [])
            if not instance_ids:
                return False

            def _fetch(iid: str) -> bool:
                r = self.session.get(
                    f"{self.server_url}/instances/{iid}/file",
                    stream=True,
                )
                if r.status_code != 200:
                    print(f"[OrthancClient] Instance {iid} HTTP {r.status_code}")
                    return False
                out = os.path.join(output_dir, f"{iid}.dcm")
                with open(out, "wb") as f:
                    for chunk in r.raw.stream(1 << 20, decode_content=False):
                        f.write(chunk)
                return True

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                results = list(pool.map(_fetch, instance_ids))

            ok = any(results)
            if ok:
                print(f"[OrthancClient] Per-instance: {sum(results)}/{len(instance_ids)} "
                      f"instances → {os.path.basename(output_dir)}")
            return ok
        except Exception as e:
            print(f"[OrthancClient] Error streaming series {series_id}: {e}")
            return False

    # -------------------------------------------------------------------------
    # DICOMweb / WADO-RS helpers
    # -------------------------------------------------------------------------

    def check_dicomweb_available(self) -> bool:
        """Return True when Orthanc's DICOMweb plugin is enabled and reachable.

        The result is cached per-connection and reset on logout / credential
        change so a new login always re-probes.
        """
        if self._dicomweb_available is not None:
            return self._dicomweb_available
        try:
            r = self.session.get(
                f"{self.server_url}/dicom-web/studies",
                headers={"Accept": "application/dicom+json"},
                timeout=5,
            )
            self._dicomweb_available = r.status_code in (200, 204)
        except Exception:
            self._dicomweb_available = False
        if self._dicomweb_available:
            print("[OrthancClient] DICOMweb plugin available — WADO-RS enabled")
        else:
            print("[OrthancClient] DICOMweb plugin not available — WADO-RS disabled")
        return self._dicomweb_available

    def _get_dicom_uids_for_series(self, orthanc_series_id: str) -> Optional[Tuple[str, str]]:
        """Resolve (StudyInstanceUID, SeriesInstanceUID) from an Orthanc series ID.

        Returns:
            Tuple (study_uid, series_uid) or None on failure.
        """
        try:
            r = self.session.get(f"{self.server_url}/series/{orthanc_series_id}")
            if r.status_code != 200:
                return None
            data = r.json()
            series_uid = data.get("MainDicomTags", {}).get("SeriesInstanceUID", "")
            parent = data.get("ParentStudy", "")
            if not series_uid or not parent:
                return None
            r2 = self.session.get(f"{self.server_url}/studies/{parent}")
            if r2.status_code != 200:
                return None
            study_uid = r2.json().get("MainDicomTags", {}).get("StudyInstanceUID", "")
            return (study_uid, series_uid) if study_uid else None
        except Exception as e:
            print(f"[OrthancClient] UID resolution error: {e}")
            return None

    @staticmethod
    def _extract_multipart_boundary(content_type: str) -> Optional[str]:
        """Extract the boundary token from a multipart/related Content-Type header."""
        for param in content_type.split(';'):
            p = param.strip()
            if p.lower().startswith('boundary='):
                return p[9:].strip('"\'')
        return None

    @staticmethod
    def _write_wado_multipart(raw_stream, boundary: str, output_dir: str,
                               progress_callback=None) -> int:
        """Parse a WADO-RS multipart/related stream and write DICOM instances to disk.

        Instances are written as each part's body arrives — no need to wait for
        the complete response before any .dcm file is available.

        Uses Content-Length-guided body reading when available (Orthanc always
        provides it).  Falls back to boundary-delimiter scanning for PACS that
        omit Content-Length.

        Args:
            raw_stream:        urllib3 response raw stream (``response.raw``).
            boundary:          Boundary token extracted from Content-Type.
            output_dir:        Directory where .dcm files are written.
            progress_callback: Optional ``callable(n: int)`` called after each
                               instance is written; n is the running count.

        Returns:
            Number of instances written.
        """
        CHUNK = 1 << 20  # 1 MB per read
        b_bnd = boundary.encode('ascii') if isinstance(boundary, str) else boundary
        INIT_MARKER = b'--' + b_bnd        # opening boundary (no leading CRLF)
        NEXT_SEP    = b'\r\n--' + b_bnd    # separator between parts

        buf = bytearray()
        count = 0

        def _refill() -> bool:
            chunk = raw_stream.read(CHUNK, decode_content=False)
            if chunk:
                buf.extend(chunk)
                return True
            return False

        def _ensure(n: int) -> bool:
            while len(buf) < n:
                if not _refill():
                    return len(buf) >= n
            return True

        def _find(needle: bytes) -> int:
            """Return index of needle in buf, filling as needed. -1 if not found."""
            while True:
                idx = buf.find(needle)
                if idx != -1:
                    return idx
                if not _refill():
                    return buf.find(needle)

        def _consume(n: int) -> bytes:
            data = bytes(buf[:n])
            del buf[:n]
            return data

        # ---- Skip preamble; locate and consume opening boundary line ----
        pos = _find(INIT_MARKER)
        if pos < 0:
            return 0
        _consume(pos + len(INIT_MARKER))   # discard preamble + --boundary
        eol = _find(b'\r\n')               # skip rest of boundary line
        if eol < 0:
            return 0
        _consume(eol + 2)

        # ---- Process parts ----
        while True:
            # Read MIME headers up to the blank line
            hdr_end = _find(b'\r\n\r\n')
            if hdr_end < 0:
                break
            headers_bytes = _consume(hdr_end)
            _consume(4)  # discard \r\n\r\n

            # Extract Content-Length (Orthanc always supplies it)
            content_length = None
            for line in headers_bytes.split(b'\r\n'):
                lo = line.lower()
                if lo.startswith(b'content-length:'):
                    try:
                        content_length = int(lo[15:].strip())
                    except (ValueError, IndexError):
                        pass

            # Read the instance body
            if content_length is not None:
                _ensure(content_length)
                dicom_data = _consume(content_length)
            else:
                # Delimiter-scan fallback for servers without Content-Length
                sep_pos = _find(NEXT_SEP)
                if sep_pos < 0:
                    dicom_data = bytes(buf)
                    buf.clear()
                else:
                    dicom_data = _consume(sep_pos)

            # Write instance to disk
            if len(dicom_data) > 132:   # sanity: min valid DICOM size
                out = os.path.join(output_dir, f'wado_{count:06d}.dcm')
                with open(out, 'wb') as f:
                    f.write(dicom_data)
                count += 1
                if progress_callback:
                    progress_callback(count)

            # Advance past \r\n--{boundary}; check for end marker
            # After either read path, buf begins with \r\n--{boundary}...
            _ensure(len(NEXT_SEP) + 2)
            if bytes(buf[:len(NEXT_SEP)]) != NEXT_SEP:
                break  # stream desync or EOF
            _consume(len(NEXT_SEP))

            # Examine the two bytes that follow --{boundary}:
            #   \r\n  → more parts follow (boundary-line terminator)
            #   --    → end marker; no more parts
            _ensure(2)
            if len(buf) < 2:
                break
            suffix = bytes(buf[:2])
            if suffix == b'--':
                break            # end of multipart body
            if suffix == b'\r\n':
                _consume(2)      # skip CRLF; next iteration reads next part headers
            else:
                break            # unexpected content

        return count

    def _wado_rs_stream_series(self, study_uid: str, series_uid: str,
                                output_dir: str,
                                progress_callback=None) -> int:
        """Retrieve a DICOM series from Orthanc via WADO-RS multipart streaming.

        Instances are written to output_dir as each part arrives in the HTTP
        response — enabling Slicer to begin DICOM import before the series is
        fully transferred.

        Args:
            study_uid:         DICOM StudyInstanceUID.
            series_uid:        DICOM SeriesInstanceUID.
            output_dir:        Directory for output .dcm files.
            progress_callback: Optional ``callable(n: int)`` → None, called
                               after each instance is written.

        Returns:
            Number of instances written, or 0 on failure.
        """
        url = (f"{self.server_url}/dicom-web"
               f"/studies/{study_uid}/series/{series_uid}")
        os.makedirs(output_dir, exist_ok=True)
        try:
            r = self.session.get(
                url,
                headers={"Accept": 'multipart/related; type="application/dicom"'},
                stream=True,
                timeout=(15, 600),
            )
            if r.status_code == 404:
                print("[OrthancClient] WADO-RS: series not found or plugin absent")
                return 0
            if r.status_code != 200:
                print(f"[OrthancClient] WADO-RS HTTP {r.status_code}")
                return 0

            boundary = self._extract_multipart_boundary(
                r.headers.get("Content-Type", "")
            )
            if not boundary:
                print("[OrthancClient] WADO-RS: no boundary in Content-Type — "
                      f"got: {r.headers.get('Content-Type', '')!r}")
                return 0

            n = self._write_wado_multipart(r.raw, boundary, output_dir,
                                           progress_callback)
            print(f"[OrthancClient] WADO-RS: {n} instances → "
                  f"{os.path.basename(output_dir)}")
            return n

        except Exception as e:
            print(f"[OrthancClient] WADO-RS streaming error: {e}")
            return 0

    def get_dicom_seg_series_id(self, study_id: str) -> Optional[str]:
        """
        Find the DICOM SEG series in a study.

        Returns the Orthanc series ID of the first SEG-modality series, or None.
        """
        try:
            r = self.session.get(f"{self.server_url}/studies/{study_id}")
            if r.status_code != 200:
                return None
            for sid in r.json().get("Series", []):
                series_r = self.session.get(f"{self.server_url}/series/{sid}")
                if series_r.status_code == 200:
                    mod = series_r.json().get("MainDicomTags", {}).get("Modality", "")
                    if mod == "SEG":
                        return sid
        except Exception as e:
            print(f"[OrthancClient] Error finding SEG series in study {study_id}: {e}")
        return None

    def download_dicom_seg_file(self, seg_series_id: str,
                                 output_path: str) -> Optional[str]:
        """
        Download the DICOM SEG instance from a series to output_path.

        DICOM SEG objects are normally a single instance.  If the series has
        multiple instances, the first one is downloaded (unusual but safe as a
        fallback — the Slicer loader handles multi-frame SEG in one file).

        Returns the local file path on success, or None.
        """
        try:
            r = self.session.get(f"{self.server_url}/series/{seg_series_id}")
            if r.status_code != 200:
                return None
            instance_ids = r.json().get("Instances", [])
            if not instance_ids:
                return None

            iid = instance_ids[0]
            file_r = self.session.get(
                f"{self.server_url}/instances/{iid}/file",
                stream=True,
            )
            if file_r.status_code != 200:
                print(f"[OrthancClient] DICOM SEG download HTTP {file_r.status_code}")
                return None

            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "wb") as f:
                for chunk in file_r.raw.stream(1 << 20, decode_content=False):
                    f.write(chunk)
            print(f"[OrthancClient] DICOM SEG downloaded: {os.path.basename(output_path)}")
            return output_path
        except Exception as e:
            print(f"[OrthancClient] Error downloading DICOM SEG {seg_series_id}: {e}")
            return None

    def _download_dicom_native_profile(self, series_id: str, patient_id: str,
                                        profile, temp_dir: str,
                                        progress_callback=None,
                                        ct_ready_callback=None) -> Dict[str, Optional[str]]:
        """Download CT instances + DICOM SEG for the DICOM_NATIVE profile.

        CT streaming and SEG download run concurrently.  Optional attachment
        downloads (e.g. a pre-computed centerline) are fetched afterwards.

        ``progress_callback``, when provided, is forwarded to the CT instance
        streamer and called as ``progress_callback(n)`` after each slice lands.

        Returns dict of {logical_name: local_path_or_None}.
        """
        from .DatasetProfile import download_filename
        paths: Dict[str, Optional[str]] = {}

        # Resolve parent study (SEG lives there alongside the CT series)
        parent_study_id = ""
        try:
            r = self.session.get(f"{self.server_url}/series/{series_id}")
            if r.status_code == 200:
                parent_study_id = r.json().get("ParentStudy", "")
        except Exception:
            pass

        # Find SEG series before launching parallel tasks
        seg_series_id = (
            self.get_dicom_seg_series_id(parent_study_id)
            if parent_study_id else None
        )

        # CT and SEG download in parallel.
        # SEG lives in its own subdirectory so that _loadDicomSeg's seg_dir
        # (dirname of the .dcm path) contains only the one SEG file and never
        # accidentally re-imports the 518-file CT folder alongside it.
        ct_dir = os.path.join(temp_dir, f"{patient_id}_ct_dicom")
        seg_subdir = os.path.join(temp_dir, f"{patient_id}_seg_dicom")
        os.makedirs(seg_subdir, exist_ok=True)
        seg_path = os.path.join(seg_subdir, f"{patient_id}_seg.dcm")

        def _fetch_ct():
            ok = self._stream_series_instances_to_dir(
                series_id, ct_dir, progress_callback=progress_callback
            )
            result = ct_dir if ok else None
            if result and ct_ready_callback:
                ct_ready_callback(result)
            return result

        def _fetch_seg():
            if not seg_series_id:
                print("[OrthancClient] No DICOM SEG series found in parent study")
                return None
            return self.download_dicom_seg_file(seg_series_id, seg_path)

        with ThreadPoolExecutor(max_workers=2) as pool:
            ct_future  = pool.submit(_fetch_ct)
            seg_future = pool.submit(_fetch_seg)
            paths["ct"]       = ct_future.result()
            paths["seg_mask"] = seg_future.result()

        # Optional attachments (e.g. pre-computed centerline stored as attachment)
        for logical_name, att_id in profile.optional_attachments.items():
            if att_id is None:
                continue
            fname = download_filename(logical_name, patient_id)
            local = self.download_nifti_from_series(
                series_id, att_id, os.path.join(temp_dir, fname)
            )
            if logical_name == "centerline" and local:
                local = self._detect_and_fix_centerline_ext(local)
            paths[logical_name] = local

        return paths

    def submit_annotation_on_series(self, series_id: str, files: Dict[str, str],
                                     notes: str = "") -> Tuple[bool, str]:
        """Upload annotation files to series-level attachments and advance status."""
        if self.user_role not in ("annotator", "admin"):
            return False, f"Role '{self.user_role}' cannot submit annotations"
        metadata = self.get_series_annotation_metadata(series_id) or {}
        if (metadata.get("annotator") != self.current_user
                and self.user_role != "admin"):
            return False, "Cannot submit: series not claimed by you"

        file_mapping = {
            "refined_mask": self.ATTACHMENT_REFINED_MASK,
            "centerline":   self.ATTACHMENT_CENTERLINE,
            "zones":        self.ATTACHMENT_ZONES,
            "endpoints":    self.ATTACHMENT_ENDPOINTS,
        }
        uploaded = []
        for name, path in files.items():
            att_id = file_mapping.get(name)
            if att_id is None:
                continue
            if path and os.path.exists(path):
                if self.upload_nifti_to_series(series_id, path, att_id):
                    uploaded.append(name)
                else:
                    return False, f"Failed to upload {name}"

        if notes:
            try:
                self.session.put(
                    f"{self.server_url}/series/{series_id}"
                    f"/attachments/{self.ATTACHMENT_NOTES}",
                    data=notes.encode("utf-8"),
                    headers={"Content-Type": "text/plain"},
                )
            except Exception as e:
                print(f"[OrthancClient] Warning: failed to upload notes: {e}")

        metadata["status"] = AnnotationStatus.ANNOTATED.value
        metadata["annotation_completed"] = datetime.now().isoformat()
        metadata["uploaded_files"] = uploaded
        metadata.setdefault("history", []).append({
            "action": "annotation_submitted",
            "user": self.current_user,
            "user_id": self.user_id,
            "timestamp": datetime.now().isoformat(),
            "files_uploaded": uploaded,
        })
        if self.set_series_annotation_metadata(series_id, metadata):
            return True, f"Annotation submitted ({len(uploaded)} files uploaded)"
        return False, "Failed to update metadata"

    def approve_annotation_on_series(self, series_id: str,
                                      comments: str = "") -> Tuple[bool, str]:
        """Approve a series annotation as ground truth."""
        if self.user_role not in ("reviewer", "admin"):
            return False, f"Role '{self.user_role}' cannot approve annotations"
        metadata = self.get_series_annotation_metadata(series_id) or {}
        if (metadata.get("reviewer") != self.current_user
                and self.user_role != "admin"):
            return False, "Cannot approve: series not claimed by you for review"
        metadata["status"] = AnnotationStatus.GROUND_TRUTH.value
        metadata["review_completed"] = datetime.now().isoformat()
        metadata["reviewer_comments"] = comments
        metadata["approved_by"] = self.current_user
        metadata.setdefault("history", []).append({
            "action": "approved_as_ground_truth",
            "user": self.current_user,
            "user_id": self.user_id,
            "timestamp": datetime.now().isoformat(),
        })
        if self.set_series_annotation_metadata(series_id, metadata):
            return True, "Annotation approved as ground truth"
        return False, "Failed to update metadata"

    def reject_annotation_on_series(self, series_id: str,
                                     reason: str) -> Tuple[bool, str]:
        """Reject a series annotation and return it to the annotator."""
        if self.user_role not in ("reviewer", "admin"):
            return False, f"Role '{self.user_role}' cannot reject annotations"
        if not reason:
            return False, "Rejection reason is required"
        metadata = self.get_series_annotation_metadata(series_id) or {}
        if (metadata.get("reviewer") != self.current_user
                and self.user_role != "admin"):
            return False, "Cannot reject: series not claimed by you for review"
        metadata["status"] = AnnotationStatus.REJECTED.value
        metadata["review_completed"] = datetime.now().isoformat()
        metadata["rejection_reason"] = reason
        metadata["rejected_by"] = self.current_user
        metadata["reviewer"] = None
        metadata.setdefault("history", []).append({
            "action": "rejected",
            "user": self.current_user,
            "user_id": self.user_id,
            "timestamp": datetime.now().isoformat(),
            "reason": reason,
        })
        if self.set_series_annotation_metadata(series_id, metadata):
            return True, "Annotation rejected and returned to annotator"
        return False, "Failed to update metadata"

    def get_statistics_for_series(self) -> Dict[str, int]:
        """Get annotation statistics across all CT series."""
        all_series = self.get_all_ct_series()
        stats = {status.value: 0 for status in AnnotationStatus}
        for s in all_series:
            st = s.get("annotation_status", AnnotationStatus.PENDING.value)
            if st in stats:
                stats[st] += 1
        stats["total"] = len(all_series)
        return stats
    
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