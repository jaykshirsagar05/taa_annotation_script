import os
import json
import slicer
from datetime import datetime

class AutosaveManager:
    """Handles autosave and crash recovery"""
    
    def __init__(self, logic):
        self.logic = logic
        self.timer = None
        self.stateFilePath = os.path.join(slicer.app.temporaryPath, "taa_wizard_state.json")
        self.savedRecoveryState = None
        self._setupTimer()

    def _setupTimer(self):
        """Setup autosave timer"""
        import qt
        self.timer = qt.QTimer()
        self.timer.timeout.connect(self._autosave)
        self.timer.start(30000)  # Every 30 seconds

    def stop(self):
        """Stop autosave timer"""
        if self.timer:
            self.timer.stop()

    def _autosave(self):
        """Automatically save state"""
        if not self.logic.currentId or not self.logic.hasUnsavedWork:
            return
        
        try:
            # Safely get node IDs — nodes may have been deleted from the scene
            volNodeId = None
            segNodeId = None
            try:
                if self.logic.volNode and self.logic.volNode.GetID():
                    volNodeId = self.logic.volNode.GetID()
            except Exception:
                pass
            try:
                if self.logic.segNode and self.logic.segNode.GetID():
                    segNodeId = self.logic.segNode.GetID()
            except Exception:
                pass
            
            state = {
                "timestamp": datetime.now().isoformat(),
                "currentId": self.logic.currentId,
                "rootDir": self.logic.rootDir,
                "phase": self.logic.workflowState["phase"],
                "zoneCount": self.logic.workflowState.get("zoneCount", 0),
                "volNodeId": volNodeId,
                "segNodeId": segNodeId,
            }
            
            with open(self.stateFilePath, 'w') as f:
                json.dump(state, f, indent=2)
            
            print(f"[Autosave] State saved at {state['timestamp']}")
            
        except Exception as e:
            print(f"[Autosave] Failed: {e}")

    def attemptCrashRecovery(self, showBannerCallback):
        """Check for previous session"""
        if not os.path.exists(self.stateFilePath):
            return
        
        try:
            with open(self.stateFilePath, 'r') as f:
                savedState = json.load(f)
            
            savedTime = datetime.fromisoformat(savedState["timestamp"])
            timeDiff = datetime.now() - savedTime
            
            if timeDiff.total_seconds() < 86400:  # 24 hours
                self.savedRecoveryState = savedState
                hours = timeDiff.total_seconds() / 3600
                message = f"⚠ Found session: {savedState['currentId']} (Phase {savedState['phase']}, {hours:.1f}h ago)"
                showBannerCallback(message)
            else:
                os.remove(self.stateFilePath)
                
        except Exception as e:
            print(f"[Recovery] Could not read state: {e}")

    def recoverSession(self):
        """Recover previous session"""
        if not self.savedRecoveryState:
            return False
        
        try:
            state = self.savedRecoveryState
            self.logic.currentId = state["currentId"]
            self.logic.rootDir = state["rootDir"]
            self.logic.workflowState["phase"] = state["phase"]
            
            # Reload data
            success = self.logic.loadData(state["rootDir"])
            if success:
                import qt
                qt.QMessageBox.information(None, "Recovery Successful",
                    f"Session restored!\n\nContinue from Phase {state['phase']}")
            return success
            
        except Exception as e:
            slicer.util.errorDisplay(f"Recovery failed: {str(e)}")
            self.cleanup()
            return False

    def quickSave(self):
        """Manual quick save"""
        if not self.logic.currentId:
            slicer.util.warningDisplay("No data loaded — nothing to save.")
            return
        
        if not self.logic.rootDir or not os.path.isdir(self.logic.rootDir):
            slicer.util.warningDisplay("Root directory is missing or invalid.")
            return
        
        try:
            saveDir = os.path.join(self.logic.rootDir, f"{self.logic.currentId}_GT_Bundle")
            if not os.path.exists(saveDir):
                os.makedirs(saveDir)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            savedItems = []
            
            if self.logic.segNode:
                try:
                    slicer.util.saveNode(
                        self.logic.segNode,
                        os.path.join(saveDir, f"{self.logic.currentId}_WIP_{timestamp}.seg.nrrd")
                    )
                    savedItems.append("segmentation")
                except Exception as e:
                    print(f"[QuickSave] Failed to save segmentation: {e}")
            
            if self.logic.zoneNode and self.logic.zoneNode.GetNumberOfControlPoints() > 0:
                try:
                    slicer.util.saveNode(
                        self.logic.zoneNode,
                        os.path.join(saveDir, f"{self.logic.currentId}_Zones_WIP_{timestamp}.fcsv")
                    )
                    savedItems.append("zones")
                except Exception as e:
                    print(f"[QuickSave] Failed to save zones: {e}")
            
            if savedItems:
                print(f"[QuickSave] Saved {', '.join(savedItems)} to {saveDir}")
            else:
                slicer.util.warningDisplay("Nothing to save — no segmentation or zone data yet.")
                
        except Exception as e:
            slicer.util.errorDisplay(f"Quick save failed: {str(e)}")

    def cleanup(self):
        """Remove autosave file"""
        if os.path.exists(self.stateFilePath):
            try:
                os.remove(self.stateFilePath)
            except:
                pass