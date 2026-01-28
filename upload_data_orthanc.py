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

# --- CONFIGURATION (CHANGE THIS PATH) ---
data_folder = r"C:\Users\JayKshirsagar\Documents\taa_ohi_anon\taa_ohi_anon\Scans" 
# C:\Users\JayKshirsagar\Documents\taa_ohi_anon\taa_ohi_anon\Scans\Scan_004
# Ensure this folder contains: ct_scan_X.nii.gz, X_merged.nii.gz, X_unified_mask_smoothed.nii.gz

ORTHANC_URL = "http://localhost:8042"
AUTH = ('orthanc', 'orthanc')

# Attachments IDs from your OrthancClient.py
IDs = {
    'CT': 1025,
    'UNIFIED': 1026,
    'MERGED': 1027,
    'METADATA': 1024
}

# configure basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def create_and_upload_study(folder):
    # 1. Identify Patient ID from filename
    files = os.listdir(folder)
    ct_file = next((f for f in files if f.startswith("ct_scan_") and f.endswith(".nii.gz")), None)
    
    if not ct_file:
        print(f"Error: No ct_scan_*.nii.gz found in {folder}")
        return

    patient_id = ct_file.replace("ct_scan_", "").replace(".nii.gz", "")
    print(f"Processing Patient: {patient_id}")

    # 2. Create a Dummy DICOM to establish the Study
    suffix = '.dcm'
    filename_little_endian = 'temp_header' + suffix
    
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.2' # CT Image Storage
    file_meta.MediaStorageSOPInstanceUID = '1.2.3' 
    file_meta.ImplementationClassUID = '1.2.3.4'

    ds = FileDataset(filename_little_endian, {}, file_meta=file_meta, preamble=b"\0" * 128)
    ds.PatientName = f"Patient^{patient_id}"
    ds.PatientID = patient_id
    ds.StudyInstanceUID = pydicom.uid.generate_uid()
    ds.SeriesInstanceUID = pydicom.uid.generate_uid()
    ds.SOPInstanceUID = pydicom.uid.generate_uid()
    ds.SOPClassUID = '1.2.840.10008.5.1.4.1.1.2'
    ds.StudyDate = datetime.now().strftime('%Y%m%d')
    ds.Modality = 'CT'
    ds.StudyDescription = "TAA Annotation Test"
    
    # Save dummy dicom
    temp_dcm = os.path.join(folder, "temp_auth_header.dcm")
    ds.save_as(temp_dcm, write_like_original=False)

    # 3. Upload Dummy DICOM to create study
    with open(temp_dcm, 'rb') as f:
        resp = requests.post(f"{ORTHANC_URL}/instances", data=f.read(), auth=AUTH)
    
    os.remove(temp_dcm) # Cleanup
    
    if resp.status_code != 200:
        print(f"Failed to upload header: {resp.text}")
        return

    orthanc_id = resp.json()['ParentStudy']
    print(f"Created Orthanc Study ID: {orthanc_id}")

    # 4. Upload NIfTI Attachments
    defaults = {
        'CT': os.path.join(folder, ct_file),
        'UNIFIED': os.path.join(folder, f"{patient_id}_unified_mask_smoothed.nii.gz"),
        'MERGED': os.path.join(folder, f"{patient_id}_merged_mask.nii.gz")
    }

    for key, path in defaults.items():
        if os.path.exists(path):
            print(f"Uploading {key}...")
            with open(path, 'rb') as f:
                requests.put(f"{ORTHANC_URL}/studies/{orthanc_id}/attachments/{IDs[key]}", 
                             data=f.read(), auth=AUTH, headers={"Content-Type": "application/octet-stream"})
        else:
            print(f"Warning: {path} not found")

    # 5. Set Initial Metadata
    metadata = {
        "status": "pending",
        "created": datetime.now().isoformat(),
        "history": [{"action": "initial_upload", "timestamp": datetime.now().isoformat()}]
    }
    requests.put(f"{ORTHANC_URL}/studies/{orthanc_id}/attachments/{IDs['METADATA']}", 
                 data=json.dumps(metadata), auth=AUTH)
    
    print("✅ Upload Complete!")

def process_folder(folder, dry_run=False, pause=0.5):
    logging.info(f"Processing folder: {folder}")
    if dry_run:
        logging.info("(dry-run) would upload here")
        return True
    try:
        create_and_upload_study(folder)
        time.sleep(pause)  # small pause to avoid hammering Orthanc
        return True
    except Exception:
        logging.exception(f"Upload failed for {folder}")
        return False

def find_scan_folders(data_dir, prefix="Scan_"):
    return sorted([
        os.path.join(data_dir, name) for name in os.listdir(data_dir)
        if name.startswith(prefix) and os.path.isdir(os.path.join(data_dir, name))
    ])

# Run it
try:
    import pydicom
    create_and_upload_study(data_folder)
except ImportError:
    print("Installing pydicom...")
    # slicer.util.pip_install("pydicom")
    # import pydicom
    # create_and_upload_study(data_folder)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload multiple scan_{N} folders to Orthanc.")
    parser.add_argument("--data-dir", default=r"C:\Users\JayKshirsagar\Documents\taa_ohi_anon\taa_ohi_anon\Scans",
                        help="Root folder containing scan_* subfolders")
    parser.add_argument("--all", action="store_true", help="Process all scan_* folders")
    parser.add_argument("--count", type=int, default=0, help="If >0, process only the first N folders (sorted)")
    parser.add_argument("--scans", nargs="+", help="Specific scan folder names (e.g. scan_000 scan_001)")
    parser.add_argument("--workers", type=int, default=1, help="Number of parallel upload workers")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually upload; just show what would run")
    parser.add_argument("--pause", type=float, default=0.5, help="Seconds to pause between uploads per worker")
    args = parser.parse_args()

    if args.scans:
        folders = [os.path.join(args.data_dir, s) for s in args.scans]
    else:
        folders = find_scan_folders(args.data_dir)

    if not args.all and args.count > 0:
        folders = folders[: args.count]

    if not folders:
        logging.warning("No scan folders found to process.")
        exit(0)

    logging.info(f"Found {len(folders)} folders to process (workers={args.workers})")

    successes = 0
    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(process_folder, f, args.dry_run, args.pause): f for f in folders}
            for fut in as_completed(futures):
                if fut.result():
                    successes += 1
    else:
        for f in folders:
            if process_folder(f, args.dry_run, args.pause):
                successes += 1

    logging.info(f"Finished. Successful uploads: {successes}/{len(folders)}")