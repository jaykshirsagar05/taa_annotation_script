import os
import requests
import json

# --- CONFIGURATION (CHANGE THESE) ---
# Where to save the downloaded files
DOWNLOAD_FOLDER = r"C:\Users\JayKshirsagar\Downloads\TAA_Review_Exports"

# The Patient ID to search for (make sure this matches your uploaded patient)
PATIENT_ID = "004"

ORTHANC_URL = "http://localhost:8042"
AUTH = ('slicer', 'slicer')

# Attachments IDs from OrthancClient.py
# These correspond to the artifacts created during the annotation/review cycle
ATTACHMENTS = {
    1024: "annotation_metadata.json",
    1025: "ct_scan.nii.gz",
    1026: "unified_mask_smoothed.nii.gz",
    1027: "merged_mask.nii.gz",
    1028: "refined_mask.seg.nrrd",
    1029: "centerline.vtk",
    1030: "zones.fcsv",
    1031: "endpoints.fcsv",
    1032: "notes.txt"
}

def download_data(download_folder, patient_id):
    if not os.path.exists(download_folder):
        os.makedirs(download_folder)
        print(f"Created folder: {download_folder}")

    print(f"Looking for study for Patient: {patient_id}")

    # 1. Find Study by PatientID using Orthanc's tools/find endpoint
    query = {
        "Level": "Study",
        "Query": {"PatientID": patient_id}
    }
    
    try:
        resp = requests.post(f"{ORTHANC_URL}/tools/find", json=query, auth=AUTH)
    except requests.exceptions.ConnectionError:
        print(f"Error: Could not connect to Orthanc at {ORTHANC_URL}")
        return

    if resp.status_code != 200:
        print(f"Error querying Orthanc: {resp.text}")
        return

    study_ids = resp.json()
    if not study_ids:
        print(f"No studies found for PatientID '{patient_id}'")
        return

    # If multiple studies exist for the patient, pick the last one (most recent)
    study_id = study_ids[-1]
    print(f"Found Study ID: {study_id}")

    # 2. Download Attachments
    print("Downloading artifacts...")
    download_count = 0
    
    for att_id, filename in ATTACHMENTS.items():
        # URL pattern to retrieve attachment content: .../attachments/{id}/data
        url = f"{ORTHANC_URL}/studies/{study_id}/attachments/{att_id}/data"
        
        # Name the file with patient prefix for clarity
        save_name = f"{patient_id}_{filename}"
        save_path = os.path.join(download_folder, save_name)

        r = requests.get(url, auth=AUTH)
        
        if r.status_code == 200:
            with open(save_path, 'wb') as f:
                f.write(r.content)
            print(f" [OK] {save_name}")
            download_count += 1
            
            # If it's the metadata JSON, inspect it briefly
            if att_id == 1024:
                try:
                    meta = json.loads(r.content)
                    print(f"      Current Status: {meta.get('status', 'unknown')}")
                    print(f"      History Count: {len(meta.get('history', []))}")
                except: pass

        elif r.status_code == 404:
            print(f" [MISSING] Attachment {att_id} ({filename}) - Not uploaded yet?")
        else:
            print(f" [ERROR] Attachment {att_id}: HTTP {r.status_code}")

    print(f"\nDownload complete! {download_count} files saved to:\n{download_folder}")

if __name__ == "__main__":
    download_data(DOWNLOAD_FOLDER, PATIENT_ID)