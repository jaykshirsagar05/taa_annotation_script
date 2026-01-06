import os
import json
import slicer
from datetime import datetime

class ExportManager:
    """Handles data export"""
    
    def __init__(self, logic):
        self.logic = logic

    def exportAll(self, notes=""):
        """Export all data"""
        import qt
        
        if not self.logic.rootDir or not self.logic.currentId:
            slicer.util.errorDisplay("No data loaded")
            return False
        
        if not self.logic.segNode:
            slicer.util.errorDisplay("No segmentation to export")
            return False
        
        # Warn if no zones
        if not self.logic.zoneNode or self.logic.zoneNode.GetNumberOfControlPoints() == 0:
            reply = qt.QMessageBox.question(
                None, 'No Zone Landmarks',
                "No zone landmarks captured.\n\nContinue export anyway?",
                qt.QMessageBox.Yes | qt.QMessageBox.No,
                qt.QMessageBox.No
            )
            if reply == qt.QMessageBox.No:
                return False
        
        saveDir = os.path.join(self.logic.rootDir, f"{self.logic.currentId}_GT_Bundle")
        if not os.path.exists(saveDir):
            os.makedirs(saveDir)
        
        progress = qt.QProgressDialog("Exporting data...", "Cancel", 0, 7, None)
        progress.setWindowModality(qt.Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        slicer.app.processEvents()
        
        exportedFiles = []
        
        try:
            # 1. Segmentation
            progress.setLabelText("Saving refined mask...")
            progress.setValue(1)
            slicer.app.processEvents()
            
            segPath = os.path.join(saveDir, f"{self.logic.currentId}_refined_mask.seg.nrrd")
            slicer.util.saveNode(self.logic.segNode, segPath)
            exportedFiles.append("✓ Refined Mask")
            
            # 2. Centerline
            progress.setLabelText("Saving centerline...")
            progress.setValue(2)
            slicer.app.processEvents()
            
            if self.logic.centerlineNode:
                slicer.util.saveNode(
                    self.logic.centerlineNode,
                    os.path.join(saveDir, f"{self.logic.currentId}_Centerline.vtk")
                )
                exportedFiles.append("✓ Centerline Model")
            
            # 3. Network
            progress.setLabelText("Saving network...")
            progress.setValue(3)
            slicer.app.processEvents()
            
            if self.logic.networkNode:
                slicer.util.saveNode(
                    self.logic.networkNode,
                    os.path.join(saveDir, f"{self.logic.currentId}_Network.vtk")
                )
                exportedFiles.append("✓ Network Model")
            
            # 4. Endpoints
            progress.setLabelText("Saving endpoints...")
            progress.setValue(4)
            slicer.app.processEvents()
            
            if self.logic.endpointNode:
                slicer.util.saveNode(
                    self.logic.endpointNode,
                    os.path.join(saveDir, f"{self.logic.currentId}_Endpoints.fcsv")
                )
                exportedFiles.append("✓ Endpoints")
            
            # 5. Zones
            progress.setLabelText("Saving zone landmarks...")
            progress.setValue(5)
            slicer.app.processEvents()
            
            if self.logic.zoneNode and self.logic.zoneNode.GetNumberOfControlPoints() > 0:
                slicer.util.saveNode(
                    self.logic.zoneNode,
                    os.path.join(saveDir, f"{self.logic.currentId}_Zones.fcsv")
                )
                exportedFiles.append(f"✓ Zone Landmarks ({self.logic.zoneNode.GetNumberOfControlPoints()} points)")
            
            # 6. Notes
            progress.setLabelText("Saving notes...")
            progress.setValue(6)
            slicer.app.processEvents()
            
            if notes:
                notesPath = os.path.join(saveDir, f"{self.logic.currentId}_notes.txt")
                with open(notesPath, "w", encoding="utf-8") as f:
                    f.write(notes + "\n")
                exportedFiles.append("✓ Notes")
            
            # 7. Metadata
            progress.setLabelText("Saving metadata...")
            progress.setValue(7)
            slicer.app.processEvents()
            
            metadata = {
                "patient_id": self.logic.currentId,
                "export_date": datetime.now().isoformat(),
                "workflow_version": "2.1_modular",
                "exported_files": exportedFiles,
                "zone_count": self.logic.zoneNode.GetNumberOfControlPoints() if self.logic.zoneNode else 0,
                "has_notes": bool(notes),
            }
            
            with open(os.path.join(saveDir, "export_metadata.json"), 'w') as f:
                json.dump(metadata, f, indent=2)
            
            progress.close()
            
            # Success message
            exportMsg = f"✓ Export Complete!\n\nSaved to:\n{saveDir}\n\nFiles:\n"
            exportMsg += "\n".join(exportedFiles)
            
            qt.QMessageBox.information(None, "Export Complete",
                exportMsg + "\n\nClick OK to reset for next scan.")
            
            self.logic.hasUnsavedWork = False
            return True
            
        except Exception as e:
            progress.close()
            slicer.util.errorDisplay(f"Export failed: {str(e)}")
            return False