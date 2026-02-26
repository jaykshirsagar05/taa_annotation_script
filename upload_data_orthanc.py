import os
import requests
import json
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from datetime import datetime
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import glob

# --- CONFIGURATION (CHANGE THIS PATH) ---
# New data structure paths for simplified-zonal-workflow branch
BASE_DATA_DIR = r"C:\Users\JayKshirsagar\Documents\ResampledDataInNifti"
CT_IMAGES_DIR = os.path.join(BASE_DATA_DIR, "images")
MASKS_DIR = os.path.join(BASE_DATA_DIR, "masks")
CENTERLINES_DIR = os.path.join(BASE_DATA_DIR, "vmtk_centerlines")

ORTHANC_URL = "http://localhost:8042"
AUTH = ('orthanc', 'orthanc')

# Attachments IDs from your OrthancClient.py
IDs = {
    'CT': 1025,
    'UNIFIED': 1026,
    'CENTERLINE': 1029,
    'METADATA': 1024
}

# configure basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def find_subject_files(subject_id):
    """
    Find CT, mask, and centerline files for a given subject ID.
    Returns a dict with file paths, or None if required files are missing.
    """
    ct_file = os.path.join(CT_IMAGES_DIR, f"{subject_id}_CTA.nii.gz")
    mask_file = os.path.join(MASKS_DIR, f"{subject_id}_label.seg.nrrd")
    centerline_file = os.path.join(CENTERLINES_DIR, f"{subject_id}_label_centerline.vtp")

    # Check if required files exist
    if not os.path.exists(ct_file):
        logging.warning(f"CT file not found: {ct_file}")
        return None

    if not os.path.exists(mask_file):
        logging.warning(f"Mask file not found: {mask_file}")
        return None

    # Centerline is optional but log if missing
    if not os.path.exists(centerline_file):
        logging.warning(f"Centerline file not found: {centerline_file}")
        centerline_file = None

    return {
        'ct': ct_file,
        'mask': mask_file,
        'centerline': centerline_file
    }


def create_and_upload_study(subject_id):
    """
    Create a study in Orthanc and upload CT, mask, and centerline attachments.
    """
    print(f"\n{'='*60}")
    print(f"Processing Subject: {subject_id}")
    print(f"{'='*60}")

    # 1. Find all required files
    files = find_subject_files(subject_id)
    if not files:
        print(f"Error: Could not find required files for {subject_id}")
        return False

    # 2. Create a Dummy DICOM to establish the Study
    suffix = '.dcm'
    filename_little_endian = 'temp_header' + suffix

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.2'  # CT Image Storage
    file_meta.MediaStorageSOPInstanceUID = '1.2.3'
    file_meta.ImplementationClassUID = '1.2.3.4'

    ds = FileDataset(filename_little_endian, {}, file_meta=file_meta, preamble=b"\0" * 128)
    ds.PatientName = f"Patient^{subject_id}"
    ds.PatientID = subject_id
    ds.StudyInstanceUID = pydicom.uid.generate_uid()
    ds.SeriesInstanceUID = pydicom.uid.generate_uid()
    ds.SOPInstanceUID = pydicom.uid.generate_uid()
    ds.SOPClassUID = '1.2.840.10008.5.1.4.1.1.2'
    ds.StudyDate = datetime.now().strftime('%Y%m%d')
    ds.Modality = 'CT'
    ds.StudyDescription = "TAA Annotation Test"

    # Save dummy dicom in temp directory
    temp_dcm = os.path.join(os.path.expanduser("~"), "temp_auth_header.dcm")
    ds.save_as(temp_dcm, write_like_original=False)

    # 3. Upload Dummy DICOM to create study
    try:
        with open(temp_dcm, 'rb') as f:
            resp = requests.post(f"{ORTHANC_URL}/instances", data=f.read(), auth=AUTH)

        os.remove(temp_dcm)  # Cleanup

        if resp.status_code != 200:
            print(f"Error: Failed to upload header: {resp.text}")
            return False

        orthanc_id = resp.json()['ParentStudy']
        print(f"[OK] Created Orthanc Study ID: {orthanc_id}")

    except Exception as e:
        print(f"Error: Failed to create study: {e}")
        if os.path.exists(temp_dcm):
            os.remove(temp_dcm)
        return False

    # 4. Upload Attachments
    attachments_to_upload = {
        'CT': files['ct'],
        'UNIFIED': files['mask']
    }

    if files['centerline']:
        attachments_to_upload['CENTERLINE'] = files['centerline']

    for key, file_path in attachments_to_upload.items():
        try:
            print(f"  Uploading {key}... ", end="", flush=True)
            with open(file_path, 'rb') as f:
                resp = requests.put(
                    f"{ORTHANC_URL}/studies/{orthanc_id}/attachments/{IDs[key]}",
                    data=f.read(),
                    auth=AUTH,
                    headers={"Content-Type": "application/octet-stream"}
                )

            if resp.status_code == 200:
                print("OK")
            else:
                print(f"FAIL (Status: {resp.status_code})")
        except Exception as e:
            print(f"FAIL (Error: {e})")

    # 5. Set Initial Metadata
    try:
        metadata = {
            "status": "pending",
            "created": datetime.now().isoformat(),
            "history": [{"action": "initial_upload", "timestamp": datetime.now().isoformat()}]
        }
        requests.put(
            f"{ORTHANC_URL}/studies/{orthanc_id}/attachments/{IDs['METADATA']}",
            data=json.dumps(metadata),
            auth=AUTH
        )
        print("[OK] Metadata uploaded")
    except Exception as e:
        print(f"Warning: Failed to upload metadata: {e}")

    print(f"[COMPLETE] Upload Complete for {subject_id}!")
    return True

def process_subject(subject_id, dry_run=False, pause=0.5):
    """Process a single subject for upload."""
    logging.info(f"Processing subject: {subject_id}")
    if dry_run:
        logging.info("(dry-run) would upload here")
        return True
    try:
        result = create_and_upload_study(subject_id)
        time.sleep(pause)  # small pause to avoid hammering Orthanc
        return result
    except Exception as e:
        logging.exception(f"Upload failed for {subject_id}")
        return False


def discover_subjects(ct_images_dir, limit=None):
    """
    Discover all unique subject IDs from CT images directory.
    Returns a list of subject IDs, optionally limited by count.
    """
    ct_files = glob.glob(os.path.join(ct_images_dir, "*_CTA.nii.gz"))
    subject_ids = []

    for ct_file in sorted(ct_files):
        # Extract subject ID: subject001_CTA.nii.gz -> subject001
        filename = os.path.basename(ct_file)
        subject_id = filename.replace("_CTA.nii.gz", "")
        subject_ids.append(subject_id)

    if limit:
        subject_ids = subject_ids[:limit]

    return subject_ids


# Run it
try:
    import pydicom
except ImportError:
    print("Installing pydicom...")
    # slicer.util.pip_install("pydicom")
    # import pydicom

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Upload CT scans, masks, and centerlines from new data structure to Orthanc."
    )
    parser.add_argument(
        "--base-dir",
        default=BASE_DATA_DIR,
        help="Base directory containing 'images', 'masks', and 'vmtk_centerlines' subdirectories"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=5,
        help="Number of subjects to upload (default: 5 for testing)"
    )
    parser.add_argument(
        "--subjects",
        nargs="+",
        help="Specific subject IDs to upload (e.g., subject001 subject002)"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel upload workers"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually upload; just show what would run"
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=0.5,
        help="Seconds to pause between uploads per worker"
    )
    args = parser.parse_args()

    # Update paths based on argument
    if args.base_dir != BASE_DATA_DIR:
        CT_IMAGES_DIR = os.path.join(args.base_dir, "images")
        MASKS_DIR = os.path.join(args.base_dir, "masks")
        CENTERLINES_DIR = os.path.join(args.base_dir, "vmtk_centerlines")

    # Discover subjects to upload
    if args.subjects:
        subject_ids = args.subjects
    else:
        subject_ids = discover_subjects(CT_IMAGES_DIR, limit=args.count)

    if not subject_ids:
        logging.warning("No CT scan files found to process.")
        exit(0)

    logging.info(f"Found {len(subject_ids)} subjects to process (workers={args.workers})")
    logging.info(f"Subjects: {', '.join(subject_ids)}")

    successes = 0
    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {
                ex.submit(process_subject, s, args.dry_run, args.pause): s
                for s in subject_ids
            }
            for fut in as_completed(futures):
                if fut.result():
                    successes += 1
    else:
        for s in subject_ids:
            if process_subject(s, args.dry_run, args.pause):
                successes += 1

    logging.info(f"Finished. Successful uploads: {successes}/{len(subject_ids)}")