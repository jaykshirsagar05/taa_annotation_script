import os
import requests
import json
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from datetime import datetime

# --- CONFIGURATION (CHANGE THIS PATH) ---
data_folder = r"C:\Users\JayKshirsagar\Documents\taa_ohi_anon\taa_ohi_anon\Scans\Scan_004" 
# C:\Users\JayKshirsagar\Documents\taa_ohi_anon\taa_ohi_anon\Scans\Scan_004
# Ensure this folder contains: ct_scan_X.nii.gz, X_merged.nii.gz, X_unified_mask_smoothed.nii.gz

ORTHANC_URL = "http://localhost:8042"
AUTH = ('slicer', 'slicer')

# Attachments IDs from your OrthancClient.py
IDs = {
    'CT': 1025,
    'UNIFIED': 1026,
    'MERGED': 1027,
    'METADATA': 1024
}

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
        'MERGED': os.path.join(folder, f"{patient_id}_merged.nii.gz")
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

# Run it
try:
    import pydicom
    create_and_upload_study(data_folder)
except ImportError:
    print("Installing pydicom...")
    # slicer.util.pip_install("pydicom")
    # import pydicom
    # create_and_upload_study(data_folder)