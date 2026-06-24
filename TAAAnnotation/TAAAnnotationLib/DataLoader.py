"""
DataLoader.py — File I/O layer for TAAAnnotationLogic.

All methods that read files from disk and load them into the Slicer MRML scene
live here. TAAAnnotationLogic delegates every loading call to this class.
"""

import os
import slicer

from TAAAnnotationLib.DatasetProfile import (
    detect_profile_from_folder,
)


class DataLoader:
    """Loads CT volumes, segmentations, centerlines, and masks into the MRML scene."""

    def __init__(self, logic):
        self._logic = logic

    # ------------------------------------------------------------------
    # High-level entry points
    # ------------------------------------------------------------------

    def loadData(self, folderPath):
        """Load data from a local folder with automatic profile detection."""
        try:
            detectedId = self._logic.getIdFromFiles(folderPath)

            if not detectedId:
                slicer.util.errorDisplay("Could not find 'ct_scan_*.nii.gz' in folder")
                return False

            profile, found_files = detect_profile_from_folder(folderPath, detectedId)

            if profile is None:
                slicer.util.errorDisplay(
                    "Could not determine dataset type.\n\n"
                    "Expected at minimum:\n"
                    "  - ct_scan_{id}.nii.gz\n"
                    "  - {id}_unified_mask_smoothed.nii.gz\n\n"
                    "Optional (selects a richer profile):\n"
                    "  - {id}_merged_mask.nii.gz  → Dual Mask profile\n"
                    "  - a .vtp/.vtk centerline file  → Mask+Centerline profile"
                )
                return False

            print(f"[LoadData] Detected profile: {profile.name} for {detectedId}")

            missing = [name for name in profile.required_attachments
                       if name not in found_files]
            if missing:
                slicer.util.errorDisplay(
                    f"Missing required files for '{profile.name}' profile:\n"
                    f"  {', '.join(missing)}"
                )
                return False

            self._logic.reset()
            self._logic.rootDir = folderPath
            self._logic.currentId = detectedId
            self._logic.activeProfile = profile

            errors = self.loadDataWithProfile(profile, found_files)
            if errors:
                slicer.util.warningDisplay(
                    "Data loaded with warnings:\n\n" + "\n".join(errors),
                    "Partial Load"
                )
            return True

        except Exception as e:
            slicer.util.errorDisplay(f"Failed to load data: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

    def loadDataWithProfile(self, profile, file_paths: dict):
        """
        Load data into the scene based on a DatasetProfile.

        This is the single, profile-aware entry point used by both Orthanc and
        manual-folder loading paths. Each section is wrapped in its own
        try/except so a failure in one file does not prevent the rest loading.

        Args:
            profile:    DatasetProfile instance.
            file_paths: dict of {logical_name: local_file_path}

        Returns:
            list of error strings (empty if everything loaded successfully).
        """
        errors = []
        slicer.mrmlScene.Clear(0)

        ct_path = file_paths.get("ct")
        if not ct_path or not os.path.exists(ct_path):
            raise RuntimeError(f"CT scan path missing or not found: {ct_path}")

        # --- DICOM_NATIVE: staged load (CT first, then SEG) ---
        _dicom_native_loaded = False
        if profile.profile_type == "dicom_native" and os.path.isdir(ct_path):
            _seg_path_raw = file_paths.get("seg_mask", "")
            if _seg_path_raw and os.path.exists(_seg_path_raw):
                print("[Loading] DICOM native: staged CT-first load")
                slicer.util.showStatusMessage("Phase 1/2: Loading CT DICOM…")
                slicer.app.processEvents()
                vol = self._loadDicomNativeCtOnly(ct_path)
                if not vol:
                    raise RuntimeError(
                        f"Failed to load CT volume (DICOM native) from {ct_path}")
                self._logic.volNode = vol
                self._setupViews()
                slicer.app.processEvents()

                slicer.util.showStatusMessage("Phase 2/2: Loading DICOM segmentation…")
                slicer.app.processEvents()
                # CT was already imported into slicer.dicomDatabase in Phase 1;
                # pass ct_already_in_db=True to skip the 840-file re-index.
                seg = self._loadDicomSeg(_seg_path_raw, ct_dicom_dir=ct_path,
                                         ct_already_in_db=True)
                if seg:
                    self._logic.segNode = seg
                    self._logic.segNode.CreateClosedSurfaceRepresentation()
                    print("[Loading] Segmentation mask loaded successfully")
                else:
                    errors.append("DICOM SEG segmentation could not be loaded")
                _dicom_native_loaded = True

        # --- CT volume (NIfTI / NRRD / non-native DICOM folder) ---
        if not _dicom_native_loaded:
            try:
                print(f"[Loading] CT from: {ct_path}")
                slicer.util.showStatusMessage("Loading CT volume…")
                self._logic.volNode = self._loadCtVolume(ct_path)
                if not self._logic.volNode:
                    raise RuntimeError("Failed to load CT volume")
            except Exception as e:
                raise RuntimeError(f"Failed to load CT volume from {ct_path}: {e}")

        # --- Segmentation mask ---
        seg_path = file_paths.get("seg_mask")
        if not _dicom_native_loaded and seg_path and os.path.exists(seg_path):
            try:
                slicer.util.showStatusMessage("Loading segmentation mask…")
                seg_path = self._fixFileExtension(seg_path)
                file_size = os.path.getsize(seg_path)
                print(f"[Loading] Segmentation mask from: {seg_path} "
                      f"(size={file_size} bytes)")
                if file_size == 0:
                    raise RuntimeError("Downloaded mask file is empty (0 bytes)")

                if seg_path.endswith('.seg.nrrd') or seg_path.endswith('.nrrd'):
                    self._logic.segNode = self._loadSegmentationFromNrrd(seg_path)
                    if not self._logic.segNode:
                        raise RuntimeError("Failed to load NRRD segmentation mask")
                elif seg_path.lower().endswith('.dcm') or (
                    os.path.isdir(seg_path)
                    and any(f.lower().endswith('.dcm') for f in os.listdir(seg_path))
                ):
                    _ct_dicom_dir = ct_path if os.path.isdir(ct_path) else None
                    self._logic.segNode = self._loadDicomSeg(seg_path, ct_dicom_dir=_ct_dicom_dir)
                    if not self._logic.segNode:
                        raise RuntimeError("Failed to load DICOM SEG segmentation")
                else:
                    unifiedLabelNode = self._loadNiftiAsLabelMap(seg_path)
                    if not unifiedLabelNode:
                        raise RuntimeError(
                            "All loading strategies failed for segmentation mask")

                    self._logic.segNode = slicer.mrmlScene.AddNewNodeByClass(
                        "vtkMRMLSegmentationNode",
                        f"{self._logic.currentId}_Segmentation"
                    )
                    slicer.util.showStatusMessage(
                        "Importing label map to segmentation — this may take a minute…")
                    slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                        unifiedLabelNode, self._logic.segNode
                    )
                    slicer.mrmlScene.RemoveNode(unifiedLabelNode)

                slicer.util.showStatusMessage("Creating 3D surface representation…")
                self._logic.segNode.CreateClosedSurfaceRepresentation()
                print("[Loading] Segmentation mask loaded successfully")
            except Exception as e:
                msg = f"Segmentation mask: {e}"
                print(f"[Loading] ERROR — {msg}")
                errors.append(msg)
        elif not _dicom_native_loaded and seg_path:
            msg = f"Segmentation mask file not found: {seg_path}"
            print(f"[Loading] ERROR — {msg}")
            errors.append(msg)

        # --- Reference mask (optional — present in Dual Mask profile) ---
        ref_path = file_paths.get("ref_mask")
        if ref_path and os.path.exists(ref_path):
            try:
                ref_path = self._fixFileExtension(ref_path)
                print(f"[Loading] Reference mask from: {ref_path}")
                slicer.util.showStatusMessage("Loading reference mask…")
                mergedLabelNode = self._loadNiftiAsLabelMap(ref_path)
                if mergedLabelNode:
                    self._logic.refNode = slicer.mrmlScene.AddNewNodeByClass(
                        "vtkMRMLSegmentationNode",
                        f"{self._logic.currentId}_Merged"
                    )
                    slicer.util.showStatusMessage(
                        "Importing reference mask to segmentation…")
                    slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                        mergedLabelNode, self._logic.refNode
                    )
                    slicer.mrmlScene.RemoveNode(mergedLabelNode)
                    slicer.util.showStatusMessage("Creating reference 3D surface…")
                    self._logic.refNode.CreateClosedSurfaceRepresentation()
                    self._logic.refNode.GetDisplayNode().SetVisibility(False)
                    print("[Loading] Reference mask loaded successfully")

                self._createBinarizedMask(ref_path)
            except Exception as e:
                msg = f"Reference mask: {e}"
                print(f"[Loading] ERROR — {msg}")
                errors.append(msg)

        # --- Pre-computed centerline (Mask+Centerline profile) ---
        centerline_path = file_paths.get("centerline")
        if centerline_path and os.path.exists(centerline_path):
            try:
                self._logic.centerlineNode = self._loadCenterlineModel(centerline_path)
            except Exception as e:
                msg = f"Centerline: {e}"
                print(f"[Loading] ERROR — {msg}")
                errors.append(msg)

        if self._logic.segNode and self._logic.segNode.GetDisplayNode():
            self._logic.segNode.GetDisplayNode().SetVisibility(True)

        slicer.util.showStatusMessage("Setting up views…")
        self._setupViews()

        self._logic.workflowState["phase"] = 1
        self._logic.hasUnsavedWork = False

        print(f"[Loading] Complete — profile: {profile.name}")
        if errors:
            print(f"[Loading] Errors: {errors}")
        return errors

    # ------------------------------------------------------------------
    # CT / DICOM loading
    # ------------------------------------------------------------------

    def _loadCtVolume(self, ct_path):
        """Load CT from either a file path (NIfTI/NRRD) or a DICOM folder."""
        if os.path.isdir(ct_path):
            return self._loadDicomFromFolder(ct_path)
        return slicer.util.loadVolume(ct_path)

    def _loadDicomFromFolder(self, dicom_folder):
        """Import and load a DICOM study from a folder into the MRML scene."""
        try:
            from DICOMLib import DICOMUtils

            # Pre-read series UID so we load exactly this series even when the
            # persistent database already contains unrelated series.
            series_uid = ""
            try:
                import pydicom
                for fname in os.listdir(dicom_folder):
                    if fname.lower().endswith(".dcm"):
                        ds = pydicom.dcmread(
                            os.path.join(dicom_folder, fname), stop_before_pixels=True
                        )
                        series_uid = str(getattr(ds, "SeriesInstanceUID", "") or "")
                        if series_uid:
                            break
            except Exception:
                pass

            db = slicer.dicomDatabase
            DICOMUtils.importDicom(dicom_folder, db)
            slicer.app.processEvents()

            if series_uid:
                loaded = DICOMUtils.loadSeriesByUID([series_uid])
                for nid in loaded:
                    node = slicer.mrmlScene.GetNodeByID(nid)
                    if node and node.IsA("vtkMRMLScalarVolumeNode"):
                        print(f"[Loading] DICOM CT loaded from folder: {dicom_folder}")
                        return node

            # Fallback: iterate all patients in the database
            for patient_uid in db.patients():
                for nid in DICOMUtils.loadPatientByUID(patient_uid):
                    node = slicer.mrmlScene.GetNodeByID(nid)
                    if node and node.IsA("vtkMRMLScalarVolumeNode"):
                        print(f"[Loading] DICOM CT loaded from folder: {dicom_folder}")
                        return node

            # Final fallback: first .dcm file as generic volume load
            for root, _, files in os.walk(dicom_folder):
                for name in files:
                    if name.lower().endswith(".dcm"):
                        dicom_file = os.path.join(root, name)
                        node = slicer.util.loadVolume(dicom_file)
                        if node:
                            print(f"[Loading] DICOM CT loaded via file fallback: {dicom_file}")
                            return node
            return None
        except Exception as e:
            print(f"[Loading] Failed to load DICOM CT from folder '{dicom_folder}': {e}")
            return None

    def _loadDicomNativeAll(self, ct_dir: str, seg_path: str):
        """
        Load CT DICOM folder and DICOM SEG in a single TemporaryDICOMDatabase.

        Using one shared database ensures the DICOMSegPlugin can resolve the
        referenced CT series by SeriesInstanceUID without a second fetch, and
        any duplicate vtkMRMLScalarVolumeNode created as a side-effect is
        detected and removed immediately.

        Returns (vtkMRMLScalarVolumeNode, vtkMRMLSegmentationNode);
        either element may be None on failure.
        """
        vol_node = None
        seg_node = None

        if not ct_dir or not os.path.isdir(ct_dir):
            print(f"[Loading] CT DICOM directory not found: {ct_dir}")
            return None, None

        # seg_dir must be a dedicated folder containing only the SEG .dcm so
        # that the importDicom call below does not accidentally sweep in CT files.
        seg_dir = (os.path.dirname(os.path.abspath(seg_path))
                   if seg_path and os.path.exists(seg_path) else None)

        try:
            from DICOMLib import DICOMUtils
            import ctk

            ct_count = len([f for f in os.listdir(ct_dir) if f.lower().endswith(".dcm")])
            print(f"[Loading] DICOM native: importing {ct_count} CT + SEG "
                  f"into shared TemporaryDICOMDatabase…")

            with DICOMUtils.TemporaryDICOMDatabase() as db:
                ct_indexer = ctk.ctkDICOMIndexer()
                slicer.util.showStatusMessage(f"Indexing {ct_count} CT DICOM files…")
                slicer.app.processEvents()
                ct_indexer.addDirectory(db, ct_dir, False)
                ct_indexer.waitForImportFinished()
                slicer.app.processEvents()

                if seg_dir and os.path.isdir(seg_dir):
                    seg_indexer = ctk.ctkDICOMIndexer()
                    seg_indexer.addDirectory(db, seg_dir, False)
                    seg_indexer.waitForImportFinished()
                    slicer.app.processEvents()

                seg_series_uid = None
                if seg_path and os.path.exists(seg_path):
                    try:
                        import pydicom
                        ds = pydicom.dcmread(seg_path, stop_before_pixels=True)
                        seg_series_uid = str(getattr(ds, "SeriesInstanceUID", "") or "")
                    except Exception as e_uid:
                        print(f"[Loading] Could not read SEG SeriesUID: {e_uid}")

                for patient_uid in db.patients():
                    if vol_node:
                        break
                    for study_uid in db.studiesForPatient(patient_uid):
                        if vol_node:
                            break
                        for series_uid in db.seriesForStudy(study_uid):
                            if series_uid == seg_series_uid:
                                continue
                            loaded = DICOMUtils.loadSeriesByUID([series_uid])
                            for nid in loaded:
                                node = slicer.mrmlScene.GetNodeByID(nid)
                                if node and node.IsA("vtkMRMLScalarVolumeNode"):
                                    vol_node = node
                                    vol_node.SetName(f"{self._logic.currentId}_CT")
                                    break
                            if vol_node:
                                break

                if not vol_node:
                    print("[Loading] No CT volume found in DICOM native database")
                    return None, None

                print(f"[Loading] CT loaded: {vol_node.GetName()}")

                if seg_series_uid:
                    existing_vol_ids = set()
                    c = slicer.mrmlScene.GetNodesByClass("vtkMRMLScalarVolumeNode")
                    for i in range(c.GetNumberOfItems()):
                        existing_vol_ids.add(c.GetItemAsObject(i).GetID())

                    loaded_seg = DICOMUtils.loadSeriesByUID([seg_series_uid])
                    for nid in loaded_seg:
                        node = slicer.mrmlScene.GetNodeByID(nid)
                        if node and node.IsA("vtkMRMLSegmentationNode"):
                            seg_node = node
                            seg_node.SetName(f"{self._logic.currentId}_Segmentation")
                            print(f"[Loading] DICOM SEG loaded: {seg_node.GetName()}")
                            break

                    if not seg_node:
                        print(f"[Loading] loadSeriesByUID for SEG returned "
                              f"{len(loaded_seg)} node(s), none are SegmentationNode")

                    # DICOMSegPlugin may load a duplicate CT volume as a side-effect
                    dup_ids = []
                    c = slicer.mrmlScene.GetNodesByClass("vtkMRMLScalarVolumeNode")
                    for i in range(c.GetNumberOfItems()):
                        n = c.GetItemAsObject(i)
                        if n.GetID() not in existing_vol_ids:
                            dup_ids.append(n.GetID())
                    for dup_id in dup_ids:
                        dup = slicer.mrmlScene.GetNodeByID(dup_id)
                        if dup:
                            print(f"[Loading] Removing duplicate CT node "
                                  f"(DICOMSegPlugin artefact): {dup.GetName()}")
                            slicer.mrmlScene.RemoveNode(dup)
                else:
                    print("[Loading] SEG series UID not resolved — SEG not loaded")

        except Exception as e:
            print(f"[Loading] _loadDicomNativeAll failed: {e}")
            import traceback
            traceback.print_exc()

        return vol_node, seg_node

    def _loadDicomNativeCtOnly(self, ct_dir: str):
        """Index CT DICOM and load the CT volume — Phase 1 of the staged load.

        Uses slicer.dicomDatabase (persistent, WAL-mode) instead of
        TemporaryDICOMDatabase.  TemporaryDICOMDatabase creates a fresh SQLite
        file without WAL mode; ctkDICOMIndexer's multi-threaded writes then
        contend on the write lock, causing SQLITE_BUSY crashes with large
        series (800+ slices) on Windows.

        Annotates the returned volume node with DICOM.instanceUIDs so that
        DICOMSegPlugin can resolve SEG geometry in Phase 2 without reloading
        CT pixel data.
        """
        if not ct_dir or not os.path.isdir(ct_dir):
            print(f"[Loading] CT DICOM directory not found: {ct_dir}")
            return None

        try:
            from DICOMLib import DICOMUtils

            ct_count = len([f for f in os.listdir(ct_dir) if f.lower().endswith(".dcm")])
            print(f"[Loading] Phase 1: importing {ct_count} CT DICOM files into database…")

            # Read the series UID from the first .dcm file so we can load exactly
            # this series even if the persistent database contains others.
            target_series_uid = ""
            try:
                import pydicom
                for fname in os.listdir(ct_dir):
                    if fname.lower().endswith(".dcm"):
                        ds = pydicom.dcmread(
                            os.path.join(ct_dir, fname), stop_before_pixels=True
                        )
                        target_series_uid = str(getattr(ds, "SeriesInstanceUID", "") or "")
                        if target_series_uid:
                            break
            except Exception as e_uid:
                print(f"[Loading] Could not pre-read CT SeriesUID: {e_uid}")

            db = slicer.dicomDatabase

            slicer.util.showStatusMessage(
                f"Importing {ct_count} CT DICOM files — this may take a moment…"
            )
            slicer.app.processEvents()

            DICOMUtils.importDicom(ct_dir, db)
            slicer.app.processEvents()

            slicer.util.showStatusMessage("Loading CT volume from database…")
            slicer.app.processEvents()

            vol_node = None
            ct_series_uid = None

            # Primary: load the specific series identified above
            if target_series_uid:
                loaded = DICOMUtils.loadSeriesByUID([target_series_uid])
                for nid in loaded:
                    node = slicer.mrmlScene.GetNodeByID(nid)
                    if node and node.IsA("vtkMRMLScalarVolumeNode"):
                        vol_node = node
                        ct_series_uid = target_series_uid
                        break

            # Fallback: scan database patients (handles already-indexed series)
            if not vol_node:
                for patient_uid in db.patients():
                    if vol_node:
                        break
                    for study_uid in db.studiesForPatient(patient_uid):
                        if vol_node:
                            break
                        for series_uid in db.seriesForStudy(study_uid):
                            loaded = DICOMUtils.loadSeriesByUID([series_uid])
                            for nid in loaded:
                                node = slicer.mrmlScene.GetNodeByID(nid)
                                if node and node.IsA("vtkMRMLScalarVolumeNode"):
                                    vol_node = node
                                    ct_series_uid = series_uid
                                    break
                            if vol_node:
                                break

            if not vol_node:
                print("[Loading] Phase 1: no CT ScalarVolumeNode found in database")
                return None

            vol_node.SetName(f"{self._logic.currentId}_CT")

            if ct_series_uid:
                inst_uids = db.instancesForSeries(ct_series_uid)
                if inst_uids:
                    vol_node.SetAttribute(
                        "DICOM.instanceUIDs", " ".join(inst_uids)
                    )
                    print(f"[Loading] Phase 1: CT loaded with "
                          f"{len(inst_uids)} DICOM instance UIDs")

            self._setupViews()
            return vol_node

        except Exception as e:
            print(f"[Loading] _loadDicomNativeCtOnly failed: {e}")
            import traceback
            traceback.print_exc()
            return None

    # ------------------------------------------------------------------
    # Segmentation / mask loading helpers
    # ------------------------------------------------------------------

    def _loadSegmentationFromNrrd(self, filepath):
        """Load a .seg.nrrd or .nrrd file directly as a segmentation node."""
        try:
            print(f"[Loading]   Loading NRRD segmentation: {os.path.basename(filepath)}")
            segNode = slicer.util.loadSegmentation(filepath)
            if segNode:
                segNode.SetName(f"{self._logic.currentId}_Segmentation")
                print(f"[Loading]   loadSegmentation succeeded "
                      f"({segNode.GetSegmentation().GetNumberOfSegments()} segments)")
                return segNode
        except Exception as e1:
            print(f"[Loading]   loadSegmentation failed: {e1}")

        try:
            print("[Loading]   Fallback: loading NRRD as label volume...")
            labelNode = slicer.util.loadLabelVolume(filepath)
            if labelNode:
                segNode = slicer.mrmlScene.AddNewNodeByClass(
                    "vtkMRMLSegmentationNode",
                    f"{self._logic.currentId}_Segmentation"
                )
                slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                    labelNode, segNode
                )
                slicer.mrmlScene.RemoveNode(labelNode)
                print("[Loading]   NRRD label-volume fallback succeeded")
                return segNode
        except Exception as e2:
            print(f"[Loading]   NRRD label-volume fallback failed: {e2}")

        return None

    def _loadDicomSeg(self, path: str, ct_dicom_dir: str = None,
                      ct_already_in_db: bool = False):
        """
        Load a DICOM SEG file (or directory containing one) as a
        vtkMRMLSegmentationNode using Slicer's persistent DICOM database.

        Strategy 1: Import SEG (and optionally CT) into slicer.dicomDatabase,
                    then load via DICOMUtils.loadSeriesByUID.
        Strategy 2: Last-resort direct load via loadSegmentation.

        Args:
            ct_dicom_dir:      Directory of CT DICOM files needed by DICOMSegPlugin
                               to resolve SEG geometry.  Pass None when the CT was
                               already imported in a prior phase (e.g. staged load).
            ct_already_in_db:  When True, skip re-importing ct_dicom_dir even if it
                               is provided (CT was imported into slicer.dicomDatabase
                               in Phase 1 and is already present).
        """
        if os.path.isdir(path):
            dcm_files = [
                os.path.join(path, f)
                for f in os.listdir(path)
                if f.lower().endswith(".dcm")
            ]
            if not dcm_files:
                print(f"[Loading] No .dcm files in DICOM SEG directory: {path}")
                return None
            path = dcm_files[0]

        seg_dir = os.path.dirname(os.path.abspath(path))

        try:
            from DICOMLib import DICOMUtils

            # Use the persistent database — avoids the TemporaryDICOMDatabase
            # SQLite write-lock crash that occurs with large CT series on Windows.
            db = slicer.dicomDatabase

            # Import CT only when needed and not already present in the database.
            if ct_dicom_dir and os.path.isdir(ct_dicom_dir) and not ct_already_in_db:
                print("[Loading]   Importing CT DICOM into persistent DB for SEG reference")
                DICOMUtils.importDicom(ct_dicom_dir, db)
                slicer.app.processEvents()

            DICOMUtils.importDicom(seg_dir, db)
            slicer.app.processEvents()

            seg_series_uid = None
            try:
                import pydicom
                ds = pydicom.dcmread(path, stop_before_pixels=True)
                file_series_uid = str(getattr(ds, "SeriesInstanceUID", "") or "")
                if file_series_uid:
                    for patient in db.patients():
                        for study in db.studiesForPatient(patient):
                            for series in db.seriesForStudy(study):
                                if series == file_series_uid:
                                    seg_series_uid = series
                                    break
                            if seg_series_uid:
                                break
                        if seg_series_uid:
                            break
                if not seg_series_uid:
                    print(f"[Loading]   SEG SeriesInstanceUID {file_series_uid!r} "
                          f"not found in persistent DB after import")
            except Exception as e_uid:
                print(f"[Loading]   Could not resolve SEG series via UID: {e_uid}")

            if not seg_series_uid:
                print(f"[Loading]   SEG file not indexed in persistent DB after import: "
                      f"{os.path.basename(path)}")
            else:
                loaded_ids = DICOMUtils.loadSeriesByUID([seg_series_uid])
                for nid in loaded_ids:
                    node = slicer.mrmlScene.GetNodeByID(nid)
                    if node and node.IsA("vtkMRMLSegmentationNode"):
                        node.SetName(f"{self._logic.currentId}_Segmentation")
                        print(f"[Loading]   DICOM SEG loaded via persistent database "
                              f"(series {seg_series_uid[:8]}…)")
                        return node
                print(f"[Loading]   loadSeriesByUID returned {len(loaded_ids)} node(s), "
                      f"none are SegmentationNode")
        except Exception as e1:
            print(f"[Loading]   DICOM persistent database strategy failed: {e1}")

        try:
            node = slicer.util.loadSegmentation(path)
            if node:
                node.SetName(f"{self._logic.currentId}_Segmentation")
                print(f"[Loading]   DICOM SEG loaded via loadSegmentation fallback: "
                      f"{os.path.basename(path)}")
                return node
        except Exception as e2:
            print(f"[Loading]   loadSegmentation fallback failed: {e2}")

        print(f"[Loading] ERROR — could not load DICOM SEG: {path}")
        return None

    # ------------------------------------------------------------------
    # Centerline / view helpers
    # ------------------------------------------------------------------

    def _loadCenterlineModel(self, centerline_path):
        """
        Load a centerline model from disk (.vtk or .vtp).

        If the first load fails, retries with the alternate extension —
        Slicer picks its reader by extension, so a file with the wrong
        extension silently fails without this retry.
        """
        print(f"[Loading] Pre-computed centerline from: {centerline_path}")
        node = slicer.util.loadModel(centerline_path)

        if node is None:
            alt_ext = ".vtp" if centerline_path.endswith(".vtk") else ".vtk"
            alt_path = centerline_path.rsplit(".", 1)[0] + alt_ext
            print(f"[Loading] loadModel failed for {os.path.basename(centerline_path)}, "
                  f"retrying as {alt_ext}...")
            try:
                import shutil
                shutil.copy2(centerline_path, alt_path)
                node = slicer.util.loadModel(alt_path)
            except Exception as e2:
                print(f"[Loading] Retry with {alt_ext} also failed: {e2}")

        if node is None:
            print(f"[Loading] WARNING: Could not load centerline from {centerline_path}")
            return None

        node.SetName(f"{self._logic.currentId}_Centerline")
        displayNode = node.GetDisplayNode()
        if displayNode:
            displayNode.SetVisibility(True)
            displayNode.SetColor(1.0, 1.0, 0.0)
            displayNode.SetLineWidth(3)

        polyData = node.GetPolyData()
        npts = polyData.GetNumberOfPoints() if polyData else 0
        print(f"[Loading] Centerline loaded ({npts} pts)")
        return node

    def _createBinarizedMask(self, merged_path):
        """Create a binarized segmentation node from a merged mask path."""
        if not os.path.exists(merged_path):
            return

        merged_path = self._fixFileExtension(merged_path)
        tempMergedBinary = self._loadNiftiAsLabelMap(merged_path)
        if not tempMergedBinary:
            print("[Loading] WARNING — could not load merged mask for binarization")
            return
        array = slicer.util.arrayFromVolume(tempMergedBinary)
        array[array > 0] = 1
        slicer.util.updateVolumeFromArray(tempMergedBinary, array)

        self._logic.binarizedMergedNode = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLSegmentationNode",
            f"{self._logic.currentId}_merged_binary"
        )
        slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
            tempMergedBinary, self._logic.binarizedMergedNode
        )
        slicer.mrmlScene.RemoveNode(tempMergedBinary)
        self._logic.binarizedMergedNode.CreateClosedSurfaceRepresentation()
        self._logic.binarizedMergedNode.GetDisplayNode().SetVisibility(False)

    def _setupViews(self):
        """Configure 4-up view layout and set background volume."""
        if self._logic.volNode:
            (slicer.app.layoutManager()
             .sliceWidget('Red').sliceLogic()
             .GetSliceCompositeNode()
             .SetBackgroundVolumeID(self._logic.volNode.GetID()))
        slicer.app.layoutManager().setLayout(
            slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)

    # ------------------------------------------------------------------
    # Static helpers — no Slicer/logic dependency
    # ------------------------------------------------------------------

    @staticmethod
    def _fixFileExtension(filepath):
        """
        Ensure the file extension matches the actual content.

        Handles:
        - .nii.gz that is actually NRRD  → rename to .nrrd / .seg.nrrd
        - .nii.gz that is uncompressed NIfTI → rename to .nii
        - Already-correct files → returned unchanged
        """
        if not filepath.endswith('.nii.gz'):
            return filepath
        try:
            with open(filepath, 'rb') as f:
                header = f.read(512)
            if len(header) < 4:
                return filepath

            if header[0] == 0x1f and header[1] == 0x8b:
                return filepath

            if header[:4] == b'NRRD':
                is_seg = b'Segment' in header
                new_path = filepath.replace('.nii.gz',
                                            '.seg.nrrd' if is_seg else '.nrrd')
                os.rename(filepath, new_path)
                print(f"[Loading] Renamed {os.path.basename(filepath)} → "
                      f"{os.path.basename(new_path)} (NRRD detected)")
                return new_path

            import struct
            hdr_size = struct.unpack('<i', header[:4])[0]
            if hdr_size in (348, 540):
                new_path = filepath[:-3]  # strip .gz
                os.rename(filepath, new_path)
                print(f"[Loading] Renamed {os.path.basename(filepath)} → "
                      f"{os.path.basename(new_path)} (raw NIfTI)")
                return new_path

            return filepath
        except Exception as e:
            print(f"[Loading] _fixFileExtension warning: {e}")
            return filepath

    @staticmethod
    def _loadNiftiAsLabelMap(filepath):
        """
        Robustly load a NIfTI mask file as a vtkMRMLLabelMapVolumeNode.

        Tries, in order:
          1. slicer.util.loadLabelVolume
          2. slicer.util.loadVolume → convert to label map
          3. SimpleITK direct read  → push into label map node
        """
        try:
            node = slicer.util.loadLabelVolume(filepath)
            if node:
                print("[Loading]   Strategy 1 (loadLabelVolume) succeeded")
                return node
        except Exception as e1:
            print(f"[Loading]   Strategy 1 (loadLabelVolume) failed: {e1}")

        try:
            tmpVol = slicer.util.loadVolume(filepath)
            if tmpVol:
                labelNode = slicer.mrmlScene.AddNewNodeByClass(
                    "vtkMRMLLabelMapVolumeNode", "tmp_label_conv"
                )
                volLogic = slicer.modules.volumes.logic()
                volLogic.CreateLabelVolumeFromVolume(
                    slicer.mrmlScene, labelNode, tmpVol
                )
                slicer.mrmlScene.RemoveNode(tmpVol)
                print("[Loading]   Strategy 2 (loadVolume + convert) succeeded")
                return labelNode
        except Exception as e2:
            print(f"[Loading]   Strategy 2 (loadVolume + convert) failed: {e2}")

        try:
            import SimpleITK as sitk
            import sitkUtils

            print("[Loading]   Strategy 3: reading with SimpleITK...")
            sitkImage = sitk.ReadImage(filepath)

            pixel_type = sitkImage.GetPixelID()
            print(f"[Loading]   SimpleITK pixel type: {pixel_type} "
                  f"({sitkImage.GetPixelIDTypeAsString()})")
            if pixel_type in (sitk.sitkFloat32, sitk.sitkFloat64):
                sitkImage = sitk.Cast(sitkImage, sitk.sitkInt16)

            tempName = "tmp_sitk_label"
            sitkUtils.PushVolumeToSlicer(sitkImage, name=tempName,
                                         className='vtkMRMLLabelMapVolumeNode')
            labelNode = slicer.util.getNode(tempName)
            if labelNode:
                print("[Loading]   Strategy 3 (SimpleITK) succeeded")
                return labelNode
        except Exception as e3:
            print(f"[Loading]   Strategy 3 (SimpleITK) failed: {e3}")

        print("[Loading]   All loading strategies exhausted — returning None")
        return None
