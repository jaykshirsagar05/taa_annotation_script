"""
upload_dicom_to_orthanc.py - Upload DICOM CT + DICOM SEG studies to Orthanc.

Expected directory structure (one folder per study):

    <data_dir>/
        <study_folder>/               # e.g. StudyUID or any descriptive name
            <ct_series_subfolder>/    # contains CT slice .dcm files
                0001.dcm
                0002.dcm
                ...
            <seg_series_subfolder>/   # contains DICOM SEG .dcm file(s)
                seg.dcm

All .dcm files are uploaded to Orthanc's /instances endpoint.
Orthanc organises them into studies/series automatically via the embedded
DICOM tags (StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID).

After upload, annotation metadata (status=pending) is stored as attachment
1024 on each Orthanc study — the same slot read by TAAAnnotation module.

Remaining attachments (refined_mask, centerline, zones, endpoints, notes)
are written back by the annotators through the normal Orthanc workflow and
are NOT touched by this script.

Alternative scan-list mode:

    python upload_dicom_to_orthanc.py \
        --scan-list healthy-all-ae.txt \
        --dicom-root /q/TAA-AI/taa_uohi_anon \
        --seg-root /q/TAA-AI/taa_uohi_anon_dcm_seg

In scan-list mode, each line is a CT series folder. DICOM SEG files are
paired only when they match the same source DICOM directory, either by the
mirrored folder layout, provenance.json, or DICOM UID references.
"""

import os
import sys
import json
import argparse
import logging
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import pydicom
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Configuration defaults — override via CLI args
# ---------------------------------------------------------------------------
DEFAULT_ORTHANC_URL = "http://10.16.65.17:8042"
DEFAULT_AUTH_USER = "orthanc"
DEFAULT_AUTH_PASS = "orthanc"

ATTACHMENT_METADATA = 1024  # same ID as OrthancClient.ATTACHMENT_METADATA

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

def make_session(orthanc_url: str, username: str, password: str) -> requests.Session:
    """Create a requests Session with retry logic and connection pooling."""
    session = requests.Session()
    session.auth = (username, password)
    retry = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=["HEAD", "GET", "POST", "PUT"],
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=20,
        pool_maxsize=20,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# ---------------------------------------------------------------------------
# DICOM file discovery
# ---------------------------------------------------------------------------

def find_dicom_files(folder: str) -> list[str]:
    """Recursively find every .dcm file under folder."""
    result = []
    for root, _, files in os.walk(folder):
        for fname in files:
            if fname.lower().endswith(".dcm"):
                result.append(os.path.join(root, fname))
    return sorted(result)


def same_path(left: str, right: str) -> bool:
    """Return True when two path strings identify the same normalized path."""
    return os.path.realpath(os.path.abspath(left)) == os.path.realpath(os.path.abspath(right))


def relative_path_under(path: str, root: str) -> str | None:
    """Return path relative to root, or None when path is outside root."""
    if not root:
        return None
    path_abs = os.path.abspath(path)
    root_abs = os.path.abspath(root)
    try:
        if os.path.commonpath([path_abs, root_abs]) != root_abs:
            return None
    except ValueError:
        return None
    return os.path.relpath(path_abs, root_abs)


def read_scan_list(scan_list: str, dicom_root: str = "") -> list[str]:
    """Read CT series folders from a text file, resolving relative lines."""
    folders = []
    with open(scan_list, "r", encoding="utf-8") as fh:
        for line in fh:
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            if not os.path.isabs(entry):
                if not dicom_root:
                    raise ValueError(
                        f"Relative scan-list entry needs --dicom-root: {entry}"
                    )
                entry = os.path.join(dicom_root, entry)
            folders.append(os.path.abspath(entry))
    return folders


def read_study_series(filepath: str) -> tuple[str, str]:
    """Read StudyInstanceUID and SeriesInstanceUID without loading pixels."""
    try:
        ds = pydicom.dcmread(filepath, stop_before_pixels=True, force=True)
        return (
            str(getattr(ds, "StudyInstanceUID", "")).strip(),
            str(getattr(ds, "SeriesInstanceUID", "")).strip(),
        )
    except Exception:
        return "", ""


def collect_uid_values(filepath: str) -> set[str]:
    """Collect all UID-valued DICOM elements without loading pixels."""
    try:
        ds = pydicom.dcmread(filepath, stop_before_pixels=True, force=True)
    except Exception:
        return set()

    values: set[str] = set()
    for elem in ds.iterall():
        if elem.VR != "UI" or elem.value in (None, ""):
            continue
        if isinstance(elem.value, (list, tuple)):
            values.update(str(value).strip() for value in elem.value if value)
        else:
            values.add(str(elem.value).strip())
    return values


def find_patient_seg_root(
    scan_folder: str,
    dicom_root: str,
    seg_root: str,
    ct_files: list[str],
) -> str:
    """Return the narrowest SEG search root for a CT series."""
    rel = relative_path_under(scan_folder, dicom_root)
    if rel:
        patient_folder = rel.split(os.sep)[0]
        candidate = os.path.join(seg_root, patient_folder)
        if os.path.isdir(candidate):
            return candidate

    patient_id = read_patient_id(ct_files[0]) if ct_files else ""
    if patient_id:
        candidate = os.path.join(seg_root, patient_id)
        if os.path.isdir(candidate):
            return candidate

    return seg_root


def find_matching_seg_folders(
    scan_folder: str,
    dicom_root: str,
    seg_root: str,
    ct_files: list[str],
) -> list[str]:
    """Find SEG folders that match a CT series by path/provenance/DICOM UIDs."""
    if not seg_root or not os.path.isdir(seg_root):
        return []

    scan_folder = os.path.abspath(scan_folder)
    candidates: set[str] = set()

    rel = relative_path_under(scan_folder, dicom_root)
    if rel:
        mirrored = os.path.join(seg_root, rel)
        if os.path.isdir(mirrored) and find_dicom_files(mirrored):
            candidates.add(os.path.realpath(mirrored))

    search_root = find_patient_seg_root(scan_folder, dicom_root, seg_root, ct_files)

    for root, _, files in os.walk(search_root):
        if "provenance.json" not in files:
            continue
        provenance_path = os.path.join(root, "provenance.json")
        try:
            with open(provenance_path, "r", encoding="utf-8") as fh:
                provenance = json.load(fh)
        except Exception as exc:
            log.debug(f"  Could not read {provenance_path}: {exc}")
            continue
        source_dicom_dir = provenance.get("source_dicom_dir", "")
        if source_dicom_dir and same_path(source_dicom_dir, scan_folder):
            if find_dicom_files(root):
                candidates.add(os.path.realpath(root))

    if candidates or not ct_files:
        return sorted(candidates)

    ct_study_uid, ct_series_uid = read_study_series(ct_files[0])
    if not ct_study_uid or not ct_series_uid:
        return sorted(candidates)

    for seg_file in find_dicom_files(search_root):
        if read_modality(seg_file) != "SEG":
            continue
        uid_values = collect_uid_values(seg_file)
        if ct_study_uid in uid_values and ct_series_uid in uid_values:
            candidates.add(os.path.realpath(os.path.dirname(seg_file)))

    return sorted(candidates)


def read_patient_id(filepath: str) -> str:
    """Read PatientID from a DICOM file without loading pixels. Returns '' on failure."""
    try:
        ds = pydicom.dcmread(filepath, stop_before_pixels=True, force=True)
        return str(getattr(ds, "PatientID", "")).strip()
    except Exception:
        return ""


def read_modality(filepath: str) -> str:
    """Read Modality tag without loading pixels."""
    try:
        ds = pydicom.dcmread(filepath, stop_before_pixels=True, force=True)
        return str(getattr(ds, "Modality", "")).strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Orthanc instance upload
# ---------------------------------------------------------------------------

def upload_instance(
    filepath: str,
    orthanc_url: str,
    session: requests.Session,
) -> dict | None:
    """
    Stream-upload one DICOM file to Orthanc /instances.

    Returns the Orthanc response dict (keys: ID, ParentStudy, ParentSeries,
    ParentPatient, Status) or None on unrecoverable failure.
    HTTP 409 (already exists) is treated as success — Orthanc returns the
    existing instance info in that case.
    """
    try:
        with open(filepath, "rb") as fh:
            # Pass file handle directly so requests streams the upload
            # without reading the whole file into RAM first.
            resp = session.post(
                f"{orthanc_url}/instances",
                data=fh,
                headers={"Content-Type": "application/dicom"},
                timeout=300,
            )
        if resp.status_code in (200, 201):
            log.debug(f"    uploaded {os.path.basename(filepath)}")
            return resp.json()
        if resp.status_code == 409:
            # Already in Orthanc — return the existing record.
            data = resp.json()
            log.debug(f"    already exists {os.path.basename(filepath)} → {data.get('ID', '?')}")
            return data
        log.warning(
            f"    FAILED {os.path.basename(filepath)} "
            f"HTTP {resp.status_code}: {resp.text[:200]}"
        )
        return None
    except Exception as exc:
        log.error(f"    exception uploading {filepath}: {exc}")
        return None


def upload_dicom_files_parallel(
    filepaths: list[str],
    orthanc_url: str,
    session: requests.Session,
    workers: int = 8,
) -> tuple[set[str], set[str], str]:
    """
    Upload a list of DICOM files in parallel.

    Returns:
        (study_ids, series_ids, patient_id)
        study_ids  — Orthanc study IDs seen across successful uploads
        series_ids — Orthanc series IDs seen
        patient_id — first PatientID found in successful responses
    """
    study_ids: set[str] = set()
    series_ids: set[str] = set()
    patient_id = ""

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(upload_instance, fp, orthanc_url, session): fp
            for fp in filepaths
        }
        for fut in as_completed(futures):
            result = fut.result()
            if result is None:
                continue
            if "ParentStudy" in result:
                study_ids.add(result["ParentStudy"])
            if "ParentSeries" in result:
                series_ids.add(result["ParentSeries"])

    # Retrieve PatientID from Orthanc tags for the first uploaded study
    if study_ids and not patient_id:
        sid = next(iter(study_ids))
        try:
            r = session.get(f"{orthanc_url}/studies/{sid}", timeout=10)
            if r.status_code == 200:
                patient_id = (
                    r.json()
                    .get("PatientMainDicomTags", {})
                    .get("PatientID", "")
                )
        except Exception:
            pass

    return study_ids, series_ids, patient_id


# ---------------------------------------------------------------------------
# Post-upload helpers
# ---------------------------------------------------------------------------

def get_series_modality(
    series_id: str, orthanc_url: str, session: requests.Session
) -> str:
    try:
        r = session.get(f"{orthanc_url}/series/{series_id}", timeout=10)
        if r.status_code == 200:
            return r.json().get("MainDicomTags", {}).get("Modality", "")
    except Exception:
        pass
    return ""


def get_study_modality_map(
    study_id: str, orthanc_url: str, session: requests.Session
) -> dict[str, list[str]]:
    """Return {modality: [series_id, ...]} for every series in the study."""
    result: dict[str, list[str]] = {}
    try:
        r = session.get(f"{orthanc_url}/studies/{study_id}", timeout=10)
        if r.status_code != 200:
            return result
        for sid in r.json().get("Series", []):
            mod = get_series_modality(sid, orthanc_url, session)
            result.setdefault(mod, []).append(sid)
    except Exception as exc:
        log.warning(f"  get_study_modality_map: {exc}")
    return result


def set_annotation_metadata(
    study_id: str,
    patient_id: str,
    orthanc_url: str,
    session: requests.Session,
) -> bool:
    """Write status=pending annotation metadata as attachment 1024 on the study."""
    metadata = {
        "status": "pending",
        "patient_id": patient_id,
        "created": datetime.now().isoformat(),
        "annotator": None,
        "reviewer": None,
        "history": [
            {
                "action": "initial_upload",
                "timestamp": datetime.now().isoformat(),
            }
        ],
    }
    try:
        resp = session.put(
            f"{orthanc_url}/studies/{study_id}/attachments/{ATTACHMENT_METADATA}",
            data=json.dumps(metadata),
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        return resp.status_code in (200, 201)
    except Exception as exc:
        log.error(f"  set_annotation_metadata failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Main per-study processing
# ---------------------------------------------------------------------------

def process_study_folder(
    study_folder: str,
    orthanc_url: str,
    session: requests.Session,
    workers: int = 8,
    dry_run: bool = False,
) -> bool:
    """
    Upload all DICOM files found under study_folder and set annotation metadata.

    Orthanc assigns instances to the correct study/series automatically using
    the DICOM StudyInstanceUID and SeriesInstanceUID tags embedded in the files.

    Returns True on success.
    """
    dicom_files = find_dicom_files(study_folder)
    return process_dicom_files(
        os.path.basename(study_folder),
        dicom_files,
        orthanc_url,
        session,
        workers=workers,
        dry_run=dry_run,
    )


def process_scan_list_entry(
    scan_folder: str,
    dicom_root: str,
    seg_root: str,
    orthanc_url: str,
    session: requests.Session,
    workers: int = 8,
    dry_run: bool = False,
    require_seg: bool = False,
) -> bool:
    """Upload one CT series from a scan list plus its matching DICOM SEG."""
    ct_files = find_dicom_files(scan_folder)
    label = relative_path_under(scan_folder, dicom_root) or scan_folder

    if not ct_files:
        log.warning(f"  No CT .dcm files found in {scan_folder}")
        return False

    seg_files: list[str] = []
    if seg_root:
        seg_folders = find_matching_seg_folders(scan_folder, dicom_root, seg_root, ct_files)
        if seg_folders:
            for seg_folder in seg_folders:
                seg_files.extend(find_dicom_files(seg_folder))
            log.info(
                "  Matched SEG folder(s): "
                + ", ".join(os.path.relpath(folder, seg_root) for folder in seg_folders)
            )
        else:
            log.warning(f"  No matching DICOM SEG found for {label}")
            if require_seg:
                return False

    return process_dicom_files(
        label,
        ct_files + sorted(seg_files),
        orthanc_url,
        session,
        workers=workers,
        dry_run=dry_run,
    )


def process_dicom_files(
    label: str,
    dicom_files: list[str],
    orthanc_url: str,
    session: requests.Session,
    workers: int = 8,
    dry_run: bool = False,
) -> bool:
    """Upload a prepared list of DICOM files and set annotation metadata."""
    log.info(f"Processing: {label}")

    if not dicom_files:
        log.warning(f"  No .dcm files found for {label}")
        return False

    # Quick pre-scan: count modalities without uploading (useful for dry-run info)
    modality_counts: dict[str, int] = {}
    for fp in dicom_files:
        m = read_modality(fp)
        modality_counts[m or "?"] = modality_counts.get(m or "?", 0) + 1
    log.info(f"  Found {len(dicom_files)} DICOM file(s): {modality_counts}")

    if dry_run:
        log.info(f"  [dry-run] skipping upload")
        return True

    # Upload all instances in parallel
    study_ids, series_ids, patient_id = upload_dicom_files_parallel(
        dicom_files, orthanc_url, session, workers=workers
    )

    if not study_ids:
        log.error(f"  No instances uploaded — check Orthanc connectivity and credentials")
        return False

    success = True
    for study_id in study_ids:
        mod_map = get_study_modality_map(study_id, orthanc_url, session)
        log.info(
            f"  Orthanc study {study_id} "
            f"(patient={patient_id or '?'}): "
            + ", ".join(f"{mod}×{len(ids)}" for mod, ids in mod_map.items())
        )

        # Warn if no SEG series — TAAAnnotation needs it for the DICOM native profile
        if "SEG" not in mod_map:
            log.warning(
                "  No DICOM SEG series found. "
                "Ensure segmentation files have Modality=SEG in their DICOM headers."
            )
        else:
            for sid in mod_map["SEG"]:
                log.info(f"  SEG series: {sid}")

        # Write pending annotation metadata
        ok = set_annotation_metadata(study_id, patient_id, orthanc_url, session)
        log.info(f"  Metadata: {'OK' if ok else 'FAILED'}")
        if not ok:
            success = False

    return success


def find_study_folders(data_dir: str, prefix: str = "") -> list[str]:
    """Return sorted list of immediate sub-directories under data_dir."""
    dirs = []
    for name in os.listdir(data_dir):
        full = os.path.join(data_dir, name)
        if os.path.isdir(full) and (not prefix or name.startswith(prefix)):
            dirs.append(full)
    return sorted(dirs)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload DICOM CT + DICOM SEG studies to Orthanc.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        help="Root folder containing study sub-directories.",
    )
    parser.add_argument("--orthanc-url", default=DEFAULT_ORTHANC_URL)
    parser.add_argument("--username", default=DEFAULT_AUTH_USER)
    parser.add_argument("--password", default=DEFAULT_AUTH_PASS)
    parser.add_argument(
        "--scan-list",
        help="Text file containing CT series folders to upload, one per line.",
    )
    parser.add_argument(
        "--dicom-root",
        default="",
        help="Root used to resolve relative scan-list entries and mirrored SEG paths.",
    )
    parser.add_argument(
        "--seg-root",
        default="",
        help="Root containing DICOM SEG folders for scan-list mode.",
    )
    parser.add_argument(
        "--require-seg",
        action="store_true",
        help="Fail scan-list entries that do not have a matching DICOM SEG.",
    )
    parser.add_argument(
        "--all", action="store_true", help="Process all sub-folders in data-dir."
    )
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="Process only the first N folders (0 = no limit when --all is set).",
    )
    parser.add_argument(
        "--folders",
        nargs="+",
        metavar="FOLDER",
        help="Specific folder names (relative to data-dir) to upload.",
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="Only process sub-folders whose name starts with this prefix.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Parallel upload workers (instances) per study.",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=0.2,
        help="Seconds to wait between study uploads.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover files and report counts without uploading.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.scan_list and args.data_dir:
        parser.error("Use either --scan-list or --data-dir, not both.")
    if not args.scan_list and not args.data_dir:
        parser.error("Either --scan-list or --data-dir is required.")

    session = make_session(args.orthanc_url, args.username, args.password)

    # Verify Orthanc is reachable
    if not args.dry_run:
        try:
            r = session.get(f"{args.orthanc_url}/system", timeout=10)
            r.raise_for_status()
            log.info(
                f"Connected to Orthanc {r.json().get('Version', '?')} "
                f"at {args.orthanc_url}"
            )
        except Exception as exc:
            log.error(f"Cannot reach Orthanc at {args.orthanc_url}: {exc}")
            sys.exit(1)

    # Resolve which study folders to process
    if args.scan_list:
        try:
            folders = read_scan_list(args.scan_list, dicom_root=args.dicom_root)
        except Exception as exc:
            log.error(f"Could not read scan list: {exc}")
            sys.exit(1)
        if args.count > 0:
            folders = folders[: args.count]
    elif args.folders:
        folders = [os.path.join(args.data_dir, f) for f in args.folders]
    else:
        folders = find_study_folders(args.data_dir, prefix=args.prefix)
        if args.count > 0:
            folders = folders[: args.count]

    if not folders:
        log.warning("No study folders found. Check --data-dir and --prefix.")
        sys.exit(0)

    log.info(f"Will process {len(folders)} folder(s) (workers={args.workers})")

    successes = 0
    for folder in folders:
        if args.scan_list:
            ok = process_scan_list_entry(
                folder,
                args.dicom_root,
                args.seg_root,
                args.orthanc_url,
                session,
                workers=args.workers,
                dry_run=args.dry_run,
                require_seg=args.require_seg,
            )
        else:
            ok = process_study_folder(
                folder,
                args.orthanc_url,
                session,
                workers=args.workers,
                dry_run=args.dry_run,
            )
        if ok:
            successes += 1
        if args.pause > 0 and folder != folders[-1]:
            time.sleep(args.pause)

    session.close()
    log.info(f"Done. {successes}/{len(folders)} studies succeeded.")
    if successes < len(folders):
        sys.exit(1)


if __name__ == "__main__":
    main()
