import qt
import slicer
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin
from TAAAnnotationLib import (
    CaseBrowserWidget, CenterlinePicker, CheckpointManager,
    ExportManager, WorkflowWidget,
)
from TAAAnnotationLib import config as _cfg
from TAAAnnotationLib.DataLoader import DataLoader
from TAAAnnotationLib.RefinementLogic import RefinementLogic
from TAAAnnotationLib.ZoneManager import ZoneManager


class TAAAnnotation(ScriptedLoadableModule):
    """Main module class - defines metadata and help text"""

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = "TAA Annotation"
        # Ensure Python deps then check for updates — deferred so Slicer finishes
        # loading the module before any pip/network activity starts.
        qt.QTimer.singleShot(1000, self._ensureDependenciesThenUpdate)

        self.parent.categories = ["Segmentation"]
        self.parent.dependencies = ["SegmentEditor", "ExtractCenterline"]
        self.parent.contributors = ["University of Ottawa Heart Institute (Canada)"]
        self.parent.helpText = """
        TAA (Thoracic Aortic Aneurysm) Annotation Protocol Module.
        <br><br>
        Offline workflow — all data is read from and written to a local folder:
        <ul>
        <li>Pick a dataset folder and load a case (CT + segmentation mask NIfTI)</li>
        <li>Refine the segmentation using Segment Editor</li>
        <li>Extract the centerline using VMTK</li>
        <li>Place SVS/STS zonal landmarks on the centerline</li>
        <li>Save progress as a checkpoint, or export the finished annotation</li>
        </ul>
        Outputs are written back into the case folder. No server connection is used.
        """
        self.parent.acknowledgementText = """
        Developed for TAA research annotation workflow.
        """

    def _ensureDependenciesThenUpdate(self):
        """Install missing Python packages, then run the update check."""
        try:
            from TAAAnnotationLib.DependencyInstaller import ensureDependencies
            ensureDependencies()
        except Exception as e:
            print(f"[TAAAnnotation] Dependency install error: {e}")
        self._checkForUpdates()

    def _checkForUpdates(self):
        try:
            from TAAAnnotationLib.AutoUpdater import checkAndUpdate
            checkAndUpdate()
        except Exception as e:
            print(f"[TAAAnnotation] Update check error: {e}")


class TAAAnnotationWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
    """Widget class - handles the UI"""

    def __init__(self, parent=None):
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)
        self.logic = None
        self.checkpointManager = None

    def setup(self):
        ScriptedLoadableModuleWidget.setup(self)

        # Create logic
        self.logic = TAAAnnotationLogic()
        self.checkpointManager = CheckpointManager(self.logic)

        # --- Main Layout ---
        mainWidget = qt.QWidget()
        mainLayout = qt.QVBoxLayout(mainWidget)

        # --- Header ---
        title = qt.QLabel("TAA Refinement Protocol")
        title.setStyleSheet("font-weight: bold; font-size: 16px; margin-bottom: 10px; color: #333;")
        title.setAlignment(qt.Qt.AlignCenter)
        mainLayout.addWidget(title)

        # --- Case Browser ---
        self.caseBrowser = CaseBrowserWidget()
        self.caseBrowser.caseSelected.connect(self.onCaseSelected)
        mainLayout.addWidget(self.caseBrowser)

        # --- Separator ---
        separator = qt.QFrame()
        separator.setFrameShape(qt.QFrame.HLine)
        separator.setStyleSheet("margin: 10px 0;")
        mainLayout.addWidget(separator)

        # --- Workflow Widget ---
        self.workflowWidget = WorkflowWidget()
        self.workflowWidget.setLogic(self.logic)
        self.workflowWidget.refineRequested.connect(self.onRefineSetup)
        self.workflowWidget.vmtkRequested.connect(self.onVmtkSetup)
        self.workflowWidget.exportRequested.connect(self.onExport)
        self.workflowWidget.saveProgressRequested.connect(self.onSaveProgress)
        self.workflowWidget.notesChanged.connect(self.onNotesChanged)
        mainLayout.addWidget(self.workflowWidget)

        # Spacer
        mainLayout.addStretch(1)

        self.layout.addWidget(mainWidget)

        # Initialize centerline picker
        self.centerlinePicker = CenterlinePicker(self.logic, self.workflowWidget.updateZoneUI)
        self.workflowWidget.setCenterlinePicker(self.centerlinePicker)

        # Initial UI state
        self.workflowWidget.updateUIState(0)

    def enter(self):
        """Called each time the user switches back to this module.

        VMTK extraction (Step 3) runs inside the separate ExtractCenterline
        module, so re-check the workflow phase here — this catches the case
        where the user leaves it unrun/failed and lands on Zone Landmarks anyway.
        """
        if self.logic:
            self.workflowWidget.updateUIState(self.logic.workflowState.get("phase", 0))

    # --- Case Loading ---

    def onCaseSelected(self, caseDir: str):
        """Load a case directory, offering to resume from its checkpoint."""
        if self.logic.hasUnsavedWork:
            if not slicer.util.confirmYesNoDisplay(
                "You have unsaved work. Loading another case will discard it.\n\nContinue?",
                "Unsaved Work"
            ):
                return

        self.caseBrowser.setEnabledState(False)
        self.workflowWidget.setButtonsEnabled(False)
        qt.QApplication.setOverrideCursor(qt.Qt.WaitCursor)

        try:
            self.centerlinePicker.cleanup()
            self.workflowWidget.resetUI()

            errors = self.logic.loadCase(caseDir)
            self.logic.applyConfiguredCtWindowLevel()

            source = self._restorePreviousWork(caseDir, errors)

            phase = self.logic.workflowState.get("phase", 1)
            if phase >= 2:
                self.workflowWidget.markDone(2, "Refine Mode")
            if phase >= 3:
                self.workflowWidget.markDone(3, "VMTK Ready")
                self._prepareForZonePicking()

            self.workflowWidget.setCurrentId(self.logic.currentId, source)
            self.workflowWidget.updateUIState(phase)
            self.workflowWidget.updateZoneUI()
            self.workflowWidget.setStatus(f"✓ Loaded: {self.logic.currentId}")
            self.caseBrowser.setCurrentCase(caseDir)

            if errors:
                slicer.util.warningDisplay(
                    "Some data could not be loaded:\n\n" + "\n".join(errors),
                    "Partial Load"
                )

        except Exception as e:
            slicer.util.errorDisplay(f"Failed to load case:\n{str(e)}")
            import traceback
            traceback.print_exc()
        finally:
            qt.QApplication.restoreOverrideCursor()
            self.caseBrowser.setEnabledState(True)
            self.workflowWidget.updateUIState(self.logic.workflowState.get("phase", 0))

    def _restorePreviousWork(self, caseDir, errors):
        """Offer to reload a checkpoint or a completed export. Returns a source label.

        A checkpoint wins when both exist: export clears the checkpoint, so a
        checkpoint sitting beside exported outputs is newer work.
        """
        checkpointSummary = CheckpointManager.describe(caseDir)
        if checkpointSummary:
            if not self._confirmResume(checkpointSummary):
                return "local folder"
            state, restoreErrors = self.checkpointManager.restore(caseDir)
            errors.extend(restoreErrors)
            if state:
                self.workflowWidget.setNotesText(state.get("notes", ""))
            self.centerlinePicker.syncLandmarksFromZoneNode()
            return "resumed from checkpoint"

        exportSummary = CheckpointManager.describeExport(caseDir)
        if exportSummary:
            if not self._confirmReopen(exportSummary):
                return "local folder"
            metadata, restoreErrors = self.checkpointManager.restoreExport(caseDir)
            errors.extend(restoreErrors)
            if metadata:
                self.workflowWidget.setNotesText(metadata.get("notes", ""))
            self.centerlinePicker.syncLandmarksFromZoneNode()
            return "completed — reopened for review"

        return "local folder"

    def _prepareForZonePicking(self):
        """Rebuild the 3D view state that step 3 normally leaves behind.

        A checkpoint stores nodes, not view state, so a resumed session comes
        back with the refined segmentation opaque and visible and no picking
        surface. That buries the centerline inside a solid model, which stops
        the zone picker's hover, click, and Yellow-view scrolling from working.
        """
        self.logic.buildPickingSurface()
        self.workflowWidget.setCenterlineNode(self.logic.centerlineNode)

    @staticmethod
    def _confirmResume(summary: str) -> bool:
        return slicer.util.confirmYesNoDisplay(
            f"A saved checkpoint was found for this case.\n\n{summary}\n\n"
            "Resume from the checkpoint?\n"
            "Choosing No starts fresh from the original segmentation mask.",
            "Resume Annotation"
        )

    @staticmethod
    def _confirmReopen(summary: str) -> bool:
        return slicer.util.confirmYesNoDisplay(
            f"This case is already annotated.\n\n{summary}\n\n"
            "Load the exported annotation for review?\n"
            "Choosing No starts fresh from the original segmentation mask; "
            "the exported files stay on disk either way.",
            "Reopen Completed Case"
        )

    # --- Workflow Handlers ---

    def onRefineSetup(self):
        self.workflowWidget.setButtonsEnabled(False)
        try:
            if self.logic.setupRefinement():
                self.workflowWidget.markDone(2, "Refine Mode")
        finally:
            self.workflowWidget.updateUIState(self.logic.workflowState.get("phase", 0))

    def onVmtkSetup(self):
        self.workflowWidget.setButtonsEnabled(False)
        try:
            if self.logic.setupVMTK():
                self.workflowWidget.markDone(3, "VMTK Ready")
                self.workflowWidget.setCenterlineNode(self.logic.centerlineNode)
        finally:
            self.workflowWidget.updateUIState(self.logic.workflowState.get("phase", 0))

    def onSaveProgress(self):
        success, message = self.checkpointManager.save(self.workflowWidget.getNotesText())
        if success:
            self.workflowWidget.setStatus(f"✓ {message} at {self.logic.getTimestamp()}")
            self.caseBrowser.setCurrentCase(self.logic.caseDir)
        else:
            slicer.util.warningDisplay(message)

    def onExport(self):
        caseDir = self.logic.caseDir
        exporter = ExportManager(self.logic)
        if not exporter.exportAll(self.workflowWidget.getNotesText()):
            return

        CheckpointManager.clear(caseDir)
        self.resetForNextCase()
        self.caseBrowser.setCurrentCase("")

    def onNotesChanged(self, text):
        self.logic.hasUnsavedWork = True

    # --- Reset ---

    def resetForNextCase(self):
        """Clear the scene and UI, ready for the next case."""
        # Disable the picker before the scene clear: it removes VTK observers
        # and MRML node refs while the nodes are still alive.  Skipping this
        # leaves dangling state that crashes Slicer on the next disable().
        self.centerlinePicker.cleanup()
        self.logic.reset()
        self.workflowWidget.resetUI()
        self.workflowWidget.updateUIState(0)
        self.workflowWidget.setStatus("✓ Ready for the next case.")

    def cleanup(self):
        """Module cleanup."""
        self.centerlinePicker.disable()


#
# TAAAnnotationLogic
#
class TAAAnnotationLogic(ScriptedLoadableModuleLogic):
    """Orchestrates workflow state; delegates I/O and processing to sub-modules."""

    def __init__(self):
        ScriptedLoadableModuleLogic.__init__(self)
        # Sub-modules are created after reset() initialises the shared state
        # that they read from / write to via self._logic references.
        self._loader = DataLoader(self)
        self._refiner = RefinementLogic(self)
        self._zones = ZoneManager(self)
        self.reset()

    def reset(self, clearScene=True):
        """Reset all state.

        Args:
            clearScene: If True, clears the MRML scene.  Set to False when
                        the caller will clear the scene separately.
        """
        self.currentId = ""
        self.caseDir = ""
        self.volNode = None
        self.segNode = None
        self.zoneNode = None
        self.networkNode = None
        self.endpointNode = None
        self.centerlineNode = None
        self.hasUnsavedWork = False
        self.workflowState = {
            "phase": 0,
            "lastSave": None,
            "zoneCount": 0
        }
        if clearScene:
            slicer.mrmlScene.Clear(0)

    def getTimestamp(self):
        from datetime import datetime
        return datetime.now().strftime("%H:%M:%S")

    def applyConfiguredCtWindowLevel(self):
        """Apply CT window/level values from config.py to the loaded CT volume."""
        if not self.volNode:
            return False
        displayNode = self.volNode.GetDisplayNode()
        if not displayNode:
            self.volNode.CreateDefaultDisplayNodes()
            displayNode = self.volNode.GetDisplayNode()
        if not displayNode:
            return False
        width = float(getattr(_cfg, "DEFAULT_CT_WINDOW_WIDTH", 1200))
        level = float(getattr(_cfg, "DEFAULT_CT_WINDOW_LEVEL", 350))
        displayNode.SetAutoWindowLevel(False)
        displayNode.SetWindowLevel(width, level)
        print(f"[Display] Applied CT window/level from config: W={width}, L={level}")
        return True

    # --- Loading (delegated to DataLoader) ---

    def loadCase(self, caseDir):
        return self._loader.loadCase(caseDir)

    # --- Refinement & VMTK (delegated to RefinementLogic) ---

    def setupRefinement(self):
        return self._refiner.setupRefinement()

    def setupVMTK(self):
        return self._refiner.setupVMTK()

    def buildPickingSurface(self):
        return self._refiner.buildPickingSurface()

    def getAortaSurfacePolyData(self):
        return self._refiner.getAortaSurfacePolyData()

    # --- Zone landmarks (delegated to ZoneManager) ---

    def createZoneNode(self):
        return self._zones.createZoneNode()

    def addZonePoint(self, worldPos, zoneName=None):
        return self._zones.addZonePoint(worldPos, zoneName)

    def undoLastZonePoint(self):
        return self._zones.undoLastZonePoint()

    def deleteZonePoint(self, index):
        return self._zones.deleteZonePoint(index)

    def renameZonePoint(self, index, newName):
        return self._zones.renameZonePoint(index, newName)

    def jumpToZonePoint(self, index):
        return self._zones.jumpToZonePoint(index)


#
# TAAAnnotationTest
#
class TAAAnnotationTest(ScriptedLoadableModuleTest):
    """Test case for module"""

    def setUp(self):
        slicer.mrmlScene.Clear()

    def runTest(self):
        self.setUp()
        self.test_TAAAnnotation1()

    def test_TAAAnnotation1(self):
        self.delayDisplay("Starting test")
        logic = TAAAnnotationLogic()
        self.assertIsNotNone(logic)
        self.delayDisplay("Test passed")
