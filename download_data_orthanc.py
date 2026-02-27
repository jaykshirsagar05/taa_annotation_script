import os
import requests
import json

# --- CONFIGURATION (CHANGE THESE) ---
# Where to save the downloaded files
DOWNLOAD_FOLDER = r"C:\Users\JayKshirsagar\Downloads\TAA_Review_Exports"

# The Patient ID to search for (make sure this matches your uploaded patient)
PATIENT_ID = "subject001"

ORTHANC_URL = "http://localhost:8042"
AUTH = ('slicer', 'slicer')

# Attachment ID to filename mapping
ATTACHMENT_NAMES = {
    1024: "annotation_metadata.json",
    1025: "ct_scan.nii.gz",
    1026: "unified_mask_smoothed.nii.gz",
    1027: "merged_mask.nii.gz",
    1028: "refined_mask.seg.nrrd",
    1029: "centerline.vtp",
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

    # 2. Query available attachments
    print("Querying available attachments...")
    try:
        resp = requests.get(f"{ORTHANC_URL}/studies/{study_id}/attachments", auth=AUTH)
        if resp.status_code != 200:
            print(f"Error querying attachments: {resp.text}")
            return
        available_ids = resp.json()
    except Exception as e:
        print(f"Error querying attachments: {e}")
        return

    if not available_ids:
        print("No attachments found for this study.")
        return

    # 3. Download available attachments
    print(f"Downloading {len(available_ids)} artifacts...")
    download_count = 0
    
    for att_id in available_ids:
        att_id = int(att_id)
        # Use mapped name or generate a default one
        filename = ATTACHMENT_NAMES.get(att_id, f"attachment_{att_id}")
        
        # URL pattern to retrieve attachment content: .../attachments/{id}/data
        url = f"{ORTHANC_URL}/studies/{study_id}/attachments/{att_id}/data"
        
        # Name the file with patient prefix for clarity
        save_name = f"{patient_id}_{filename}"
        save_path = os.path.join(download_folder, save_name)

        try:
            r = requests.get(url, auth=AUTH)
            
            if r.status_code == 200:
                with open(save_path, 'wb') as f:
                    f.write(r.content)
                print(f" [OK] {save_name}")
                download_count += 1
            else:
                print(f" [ERROR] Attachment {att_id}: HTTP {r.status_code}")
        except Exception as e:
            print(f" [ERROR] Attachment {att_id}: {e}")

    print(f"\nDownload complete! {download_count} files saved to:\n{download_folder}")

if __name__ == "__main__":
    download_data(DOWNLOAD_FOLDER, PATIENT_ID)