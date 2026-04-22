"""
OrthancBackend.py - Orthanc PACS storage backend implementing StorageBackend.

Wraps the OrthancClient from TAAAnnotationLib (backward-compat) AND provides
a standalone OrthancAdminDashboardAuth helper.
"""
import os
import json
import tempfile
import requests
from typing import Tuple, Optional

from .StorageBackend import StorageBackend


class OrthancAdminDashboardAuth:
    """
    Handles authentication with the AdminDashboard service.
    Isolated so it can be unit-tested or swapped without touching OrthancBackend.
    """

    def __init__(self, admin_url: str):
        self.admin_url = admin_url.rstrip("/")
        self.auth_token: Optional[str] = None
        self.current_user: Optional[str] = None
        self.user_role: Optional[str] = None
        self.user_id: Optional[int] = None
        self.assigned_series: list = []
        self.available: bool = True

    def login(self, username: str, password: str) -> Tuple[bool, str]:
        try:
            resp = requests.post(
                f"{self.admin_url}/auth/api/login",
                json={"username": username, "password": password},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    self.auth_token = data["token"]
                    user = data["user"]
                    self.current_user = user["username"]
                    self.user_role = user["role"]
                    self.user_id = user["id"]
                    self.assigned_series = user.get("assigned_series_uids", [])
                    return True, f"Logged in as {self.current_user} ({self.user_role})"
                return False, data.get("error", "Authentication failed")
            if resp.status_code == 401:
                return False, "Invalid credentials"
            return False, f"Server error: HTTP {resp.status_code}"
        except requests.exceptions.ConnectionError:
            self.available = False
            return False, f"Cannot connect to AdminDashboard at {self.admin_url}"
        except requests.exceptions.Timeout:
            return False, "Connection timed out"
        except Exception as e:
            return False, str(e)

    def logout(self):
        self.auth_token = None
        self.current_user = None
        self.user_role = None
        self.user_id = None
        self.assigned_series = []

    def get_auth_headers(self) -> dict:
        if self.auth_token:
            return {"Authorization": f"Bearer {self.auth_token}"}
        return {}

    def is_authenticated(self) -> bool:
        return self.auth_token is not None


class OrthancBackend(StorageBackend):
    """
    StorageBackend backed by an Orthanc PACS server.
    Authentication is handled by OrthancAdminDashboardAuth.
    Attachment ID mapping comes from the active VascularProfile.
    """

    CAPABILITIES = {"worklist", "pacs", "submit", "review"}

    def __init__(self, orthanc_url: str = "http://localhost:8042",
                 admin_dashboard_url: str = "http://localhost:8000"):
        self.server_url = orthanc_url.rstrip("/")
        self.auth = OrthancAdminDashboardAuth(admin_dashboard_url)
        self._session = requests.Session()
        self._orthanc_auth: Optional[Tuple[str, str]] = None

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def login(self, username: str, password: str) -> Tuple[bool, str]:
        ok, msg = self.auth.login(username, password)
        return ok, msg

    def login_direct(self, username: str, password: str, role: str = "annotator") -> Tuple[bool, str]:
        """Authenticate directly against Orthanc (no AdminDashboard)."""
        self._set_orthanc_credentials(username, password)
        ok, msg = self._verify_orthanc_connection()
        if not ok:
            self._orthanc_auth = None
            self._session.auth = None
            return False, f"Orthanc authentication failed: {msg}"
        self.auth.current_user = username
        self.auth.user_role = role
        self.auth.available = False
        return True, f"Logged in directly as {username} ({role})"

    def logout(self):
        self.auth.logout()
        self._orthanc_auth = None
        self._session.auth = None
        try:
            self._session.close()
        except Exception:
            pass
        self._session = requests.Session()

    def _set_orthanc_credentials(self, username: str, password: str):
        self._orthanc_auth = (username, password)
        self._session.auth = self._orthanc_auth

    def _verify_orthanc_connection(self) -> Tuple[bool, str]:
        try:
            resp = self._session.get(f"{self.server_url}/system", timeout=10)
            if resp.status_code == 200:
                info = resp.json()
                return True, f"Orthanc {info.get('Version', 'unknown')}"
            return False, f"HTTP {resp.status_code}"
        except Exception as e:
            return False, str(e)

    def is_connected(self) -> bool:
        ok, _ = self._verify_orthanc_connection()
        return ok

    # ------------------------------------------------------------------
    # StorageBackend interface
    # ------------------------------------------------------------------

    def list_worklist(self, filter_name: str = "all") -> list:
        """List Orthanc studies, annotated with annotation status metadata."""
        try:
            resp = self._session.get(f"{self.server_url}/studies")
            resp.raise_for_status()
            study_ids = resp.json()
        except Exception as e:
            print(f"[OrthancBackend] list_worklist error: {e}")
            return []

        result = []
        for study_id in study_ids:
            info = self._get_study_info(study_id)
            if info:
                result.append(info)
        return result

    def fetch_study(self, study_id: str, profile, temp_dir: str) -> dict:
        """Download study attachments to temp_dir per profile input definitions."""
        os.makedirs(temp_dir, exist_ok=True)

        # Get patient ID from study metadata
        patient_id = self._get_patient_id(study_id)

        file_paths = {}
        for input_name, input_spec in profile.inputs.items():
            try:
                att_id = profile.get_orthanc_attachment_id(input_name)
            except KeyError:
                continue

            # Derive filename from first file_pattern with patient_id substituted
            filename = _derive_filename(input_spec.file_patterns, patient_id, input_name)
            dest_path = os.path.join(temp_dir, filename)

            if self._download_attachment(study_id, att_id, dest_path):
                file_paths[input_name] = dest_path
            elif input_spec.required:
                print(f"[OrthancBackend] WARNING: Required attachment '{input_name}' not found")

        return {
            "patient_id": patient_id,
            "file_paths": file_paths,
            "_profile": profile,
            "_temp_dir": temp_dir,
        }

    def submit_artifacts(
        self, study_id: str, artifacts: dict, notes: str, metadata: dict
    ) -> Tuple[bool, str]:
        """Upload annotation artifacts as Orthanc attachments."""
        # For submission we need the profile to get attachment IDs.
        # This is called by the workflow with artifacts already resolved.
        uploaded = []
        failed = []

        # Upload each artifact to the corresponding attachment ID via series-level attachment.
        # We use a generic approach: look up the attachment ID from the metadata dict
        # if it contains a profile, otherwise use conventional IDs.
        profile = metadata.get("_profile")

        for key, src_path in artifacts.items():
            if not src_path or not os.path.isfile(src_path):
                continue
            try:
                if profile:
                    att_id = profile.get_orthanc_attachment_id(key)
                else:
                    # Fallback conventional IDs
                    _fallback = {
                        "refined_mask": 1028, "centerline": 1029,
                        "landmarks": 1030, "endpoints": 1031, "notes": 1032,
                    }
                    att_id = _fallback.get(key)
                    if att_id is None:
                        continue

                with open(src_path, "rb") as f:
                    data = f.read()
                resp = self._session.put(
                    f"{self.server_url}/studies/{study_id}/attachments/{att_id}",
                    data=data,
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=120,
                )
                resp.raise_for_status()
                uploaded.append(key)
            except Exception as e:
                failed.append(f"{key}: {e}")

        if notes:
            try:
                notes_att_id = 1032
                if profile:
                    try:
                        notes_att_id = profile.get_orthanc_attachment_id("notes")
                    except KeyError:
                        pass
                resp = self._session.put(
                    f"{self.server_url}/studies/{study_id}/attachments/{notes_att_id}",
                    data=notes.encode("utf-8"),
                    headers={"Content-Type": "text/plain"},
                    timeout=30,
                )
                resp.raise_for_status()
                uploaded.append("notes")
            except Exception as e:
                failed.append(f"notes: {e}")

        if failed:
            return False, f"Uploaded {len(uploaded)}; failures: {'; '.join(failed)}"
        return True, f"Uploaded {len(uploaded)} artifacts to Orthanc study {study_id}"

    # ------------------------------------------------------------------
    # Optional review methods
    # ------------------------------------------------------------------

    def claim_study(self, study_id: str, role: str) -> Tuple[bool, str]:
        """Mark a study as in-progress by the current user."""
        return self._update_metadata(study_id, {
            "status": "in_progress",
            role: self.auth.current_user,
        })

    def release_study(self, study_id: str) -> Tuple[bool, str]:
        return self._update_metadata(study_id, {"status": "pending"})

    def approve_annotation(self, study_id: str, comments: str) -> Tuple[bool, str]:
        return self._update_metadata(study_id, {
            "status": "ground_truth",
            "review_comments": comments,
            "reviewer": self.auth.current_user,
        })

    def reject_annotation(self, study_id: str, reason: str) -> Tuple[bool, str]:
        return self._update_metadata(study_id, {
            "status": "rejected",
            "rejection_reason": reason,
            "reviewer": self.auth.current_user,
        })

    def get_worklist_filters(self, role: str) -> list:
        filters = ["all"]
        if role in ("annotator",):
            filters += ["pending", "in_progress"]
        if role in ("reviewer", "admin"):
            filters += ["annotated", "in_review", "ground_truth", "rejected"]
        return filters

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_study_info(self, study_id: str) -> Optional[dict]:
        try:
            resp = self._session.get(f"{self.server_url}/studies/{study_id}")
            resp.raise_for_status()
            data = resp.json()
            patient_id = (
                data.get("PatientMainDicomTags", {}).get("PatientID", "")
                or study_id
            )
            metadata = self._get_annotation_metadata(study_id)
            return {
                "id": study_id,
                "patient_id": patient_id,
                "status": metadata.get("status", "pending") if metadata else "pending",
                "annotator": metadata.get("annotator") if metadata else None,
                "reviewer": metadata.get("reviewer") if metadata else None,
                "annotation_metadata": metadata,
            }
        except Exception as e:
            print(f"[OrthancBackend] _get_study_info error for {study_id}: {e}")
            return None

    def _get_patient_id(self, study_id: str) -> str:
        info = self._get_study_info(study_id)
        if info:
            return info.get("patient_id", study_id)
        return study_id

    def _get_annotation_metadata(self, study_id: str) -> dict:
        try:
            resp = self._session.get(
                f"{self.server_url}/studies/{study_id}/attachments/1024/data",
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return {}

    def _update_metadata(self, study_id: str, updates: dict) -> Tuple[bool, str]:
        meta = self._get_annotation_metadata(study_id)
        meta.update(updates)
        try:
            resp = self._session.put(
                f"{self.server_url}/studies/{study_id}/attachments/1024",
                data=json.dumps(meta).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            resp.raise_for_status()
            return True, f"Metadata updated: {updates}"
        except Exception as e:
            return False, str(e)

    def _download_attachment(self, study_id: str, att_id: int, dest_path: str) -> bool:
        try:
            resp = self._session.get(
                f"{self.server_url}/studies/{study_id}/attachments/{att_id}/data",
                timeout=120,
                stream=True,
            )
            if resp.status_code == 200:
                with open(dest_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        f.write(chunk)
                return True
        except Exception as e:
            print(f"[OrthancBackend] Download error att={att_id}: {e}")
        return False

    def get_display_name(self) -> str:
        return f"Orthanc PACS ({self.server_url})"


def _derive_filename(patterns: list, patient_id: str, fallback_name: str) -> str:
    """Derive a local filename from the first pattern by substituting patient_id."""
    if patterns:
        return patterns[0].replace("{id}", patient_id)
    return f"{patient_id}_{fallback_name}"
