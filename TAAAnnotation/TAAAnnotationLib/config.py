"""
config.py — Server connection settings for TAA Annotation.

Edit this file to match your deployment. Do not change anything else.
"""

# Orthanc PACS server base URL
ORTHANC_URL = "http://10.16.65.17:8042"

# AdminDashboard base URL
ADMIN_DASHBOARD_URL = "http://10.16.65.17:7778"

# Orthanc basic-auth service account.
# Used for all direct Orthanc requests (series listing, downloads, uploads)
# regardless of whether the user logged in via the dashboard or direct mode.
ORTHANC_USERNAME = "orthanc"
ORTHANC_PASSWORD = "orthanc"
