import os
import qt
import slicer
import vtk
import glob
import json
import time
import numpy as np
from datetime import datetime

class AnnotationGUI(qt.QWidget):
    def __init__(self, parent=None):
        super(AnnotationGUI, self).__init__(parent)
        self.setWindowTitle("TAA annotation protocol")
        self.setLayout(qt.QVBoxLayout())
        self.resize(400, 800)
        
        # Global variables
        self.current_id = ""
        self.root_dir = ""
        self.vol_node = None
        self.seg_node = None
        self.ref_node = None
        self.zone_node = None
        self.network_node = None
        self.endpoint_node = None
        self.centerline_node = None
        
        # Centerline interaction
        self.centerline_click_observer = None
        self.click_mode_active = False
        
        # State tracking for crash recovery
        self.state_file = None
        self.workflow_state = {
            "phase": 0,
            "last_save": None,
            "unsaved_changes": False,
            "zone_count": 0
        }
        
        # Track if user made changes
        self.has_unsaved_work = False
        
        self.setup_ui()
        self.setup_autosave()
        self.attempt_crash_recovery()
        self.show()

    def closeEvent(self, event):
        """Intercept window close to warn about unsaved work"""
        # Clean up observers
        self.disable_centerline_click_mode()
        
        if self.has_unsaved_work:
            reply = qt.QMessageBox.question(
                self, 
                'Unsaved Work Detected',
                f"You have unsaved work for {self.current_id}.\n\n"
                "Are you sure you want to close?\n\n"
                "• Click 'Yes' to force close (work will be lost)\n"
                "• Click 'No' to go back and export your work",
                qt.QMessageBox.Yes | qt.QMessageBox.No,
                qt.QMessageBox.No
            )
            
            if reply == qt.QMessageBox.No:
                event.ignore()
                return
        
        # Clean up autosave
        self.cleanup_autosave()
        event.accept()

    def setup_ui(self):
        # --- Header ---
        title = qt.QLabel("TAA Refinement Protocol")
        title.setStyleSheet("font-weight: bold; font-size: 16px; margin-bottom: 10px; color: #333;")
        title.setAlignment(qt.Qt.AlignCenter)
        self.layout().addWidget(title)

        # Crash Recovery Banner (hidden by default)
        self.recovery_banner = qt.QFrame()
        self.recovery_banner.setStyleSheet("background-color: #ffc107; padding: 10px; border-radius: 5px;")
        recovery_layout = qt.QHBoxLayout(self.recovery_banner)
        self.recovery_label = qt.QLabel("⚠ Previous session detected")
        self.btn_recover = qt.QPushButton("Recover")
        self.btn_recover.connect('clicked()', self.recover_session)
        self.btn_ignore = qt.QPushButton("Start Fresh")
        self.btn_ignore.connect('clicked()', self.ignore_recovery)
        recovery_layout.addWidget(self.recovery_label)
        recovery_layout.addWidget(self.btn_recover)
        recovery_layout.addWidget(self.btn_ignore)
        self.recovery_banner.hide()
        self.layout().addWidget(self.recovery_banner)

        self.default_style = "text-align: left; padding: 8px; font-size: 12px;"
        self.done_style = "background-color: #28a745; color: white; text-align: left; padding: 8px; font-weight: bold;"
        self.active_style = "background-color: #fff3cd; border: 2px solid #ffc107; text-align: left; padding: 8px;"

        # --- Phase 1: Load ---
        self.btn_load = qt.QPushButton("1. Load Data & Initialize")
        self.btn_load.connect('clicked()', self.on_load_data)
        self.btn_load.setStyleSheet(self.default_style)
        self.layout().addWidget(self.btn_load)

        # --- Phase 2: Refine ---
        self.btn_seg = qt.QPushButton("2. Refine Mask")
        self.btn_seg.connect('clicked()', self.on_refine_setup)
        self.btn_seg.setStyleSheet(self.default_style)
        self.btn_seg.setEnabled(False)
        self.layout().addWidget(self.btn_seg)

        # --- Phase 3: VMTK Prep ---
        self.btn_vmtk = qt.QPushButton("3. Extract VMTK Centerline")
        self.btn_vmtk.connect('clicked()', self.on_vmtk_setup)
        self.btn_vmtk.setStyleSheet(self.default_style)
        self.btn_vmtk.setEnabled(False)
        self.layout().addWidget(self.btn_vmtk)

        # --- Phase 4: Zones (Direct Click Mode) ---
        self.group_zones = qt.QGroupBox("4. Zonal Landmarks (Preview & Confirm)")
        self.group_zones.setLayout(qt.QVBoxLayout())
        
        # Centerline selector
        cl_layout = qt.QHBoxLayout()
        cl_label = qt.QLabel("Centerline:")
        self.centerline_combo = slicer.qMRMLNodeComboBox()
        self.centerline_combo.nodeTypes = ["vtkMRMLModelNode"]
        self.centerline_combo.selectNodeUponCreation = False
        self.centerline_combo.addEnabled = False
        self.centerline_combo.removeEnabled = False
        self.centerline_combo.noneEnabled = True
        self.centerline_combo.showHidden = False
        self.centerline_combo.setMRMLScene(slicer.mrmlScene)
        self.centerline_combo.setToolTip("Select the centerline model (all branches)")
        cl_layout.addWidget(cl_label)
        cl_layout.addWidget(self.centerline_combo)
        self.group_zones.layout().addLayout(cl_layout)
        
        # Start/Stop Click Mode & Confirm
        btn_layout = qt.QHBoxLayout()
        
        self.btn_click_mode = qt.QPushButton("🎯 Start Zone Picker")
        self.btn_click_mode.setToolTip("Click on centerline/vessel in 3D view to preview cross-section")
        self.btn_click_mode.connect('clicked()', self.toggle_click_mode)
        self.btn_click_mode.setStyleSheet("background-color: #17a2b8; color: white; font-weight: bold; padding: 10px;")
        self.btn_click_mode.setEnabled(False)
        btn_layout.addWidget(self.btn_click_mode)
        
        self.btn_confirm_point = qt.QPushButton("✅ Confirm Zone")
        self.btn_confirm_point.setToolTip("Save the currently previewed point")
        self.btn_confirm_point.connect('clicked()', self.confirm_zone_point)
        self.btn_confirm_point.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 10px;")
        self.btn_confirm_point.setEnabled(False)
        btn_layout.addWidget(self.btn_confirm_point)
        
        self.group_zones.layout().addLayout(btn_layout)
        
        # Click mode status
        self.lbl_click_status = qt.QLabel("Picker: OFF")
        self.lbl_click_status.setStyleSheet("color: #666; font-style: italic;")
        self.group_zones.layout().addWidget(self.lbl_click_status)
        
        # Undo last point button
        self.btn_undo_point = qt.QPushButton("↩ Undo Last Point")
        self.btn_undo_point.connect('clicked()', self.undo_last_zone_point)
        self.btn_undo_point.setEnabled(False)
        self.group_zones.layout().addWidget(self.btn_undo_point)
        
        # Zone Counter
        self.lbl_zone_count = qt.QLabel("Zone Points: 0")
        self.lbl_zone_count.setStyleSheet("font-weight: bold; color: #17a2b8;")
        self.group_zones.layout().addWidget(self.lbl_zone_count)
        
        # Zone points list
        self.zone_list = qt.QListWidget()
        self.zone_list.setMaximumHeight(120)
        self.zone_list.setToolTip("Double-click to jump to point, right-click to delete")
        self.zone_list.itemDoubleClicked.connect(self.jump_to_zone_point)
        self.zone_list.setContextMenuPolicy(qt.Qt.CustomContextMenu)
        self.zone_list.customContextMenuRequested.connect(self.show_zone_context_menu)
        self.group_zones.layout().addWidget(self.zone_list)
        
        self.layout().addWidget(self.group_zones)

        # --- Quick Save Button ---
        self.btn_quick_save = qt.QPushButton("💾 Quick Save Progress")
        self.btn_quick_save.connect('clicked()', self.quick_save)
        self.btn_quick_save.setStyleSheet("background-color: #6c757d; color: white; padding: 8px;")
        self.btn_quick_save.setEnabled(False)
        self.layout().addWidget(self.btn_quick_save)

        # --- Phase 5: Export ---
        line = qt.QFrame()
        line.setFrameShape(qt.QFrame.HLine)
        self.layout().addWidget(line)
        
        self.btn_export = qt.QPushButton("5. Export & Reset")
        self.btn_export.connect('clicked()', self.on_export)
        self.btn_export.setStyleSheet("text-align: center; padding: 10px; font-weight: bold; background-color: #007bff; color: white;")
        self.btn_export.setEnabled(False)
        self.layout().addWidget(self.btn_export)

        # Status Label
        self.lbl_status = qt.QLabel("Status: Ready for Scan")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color: #666; margin-top: 10px;")
        self.layout().addStretch()
        self.layout().addWidget(self.lbl_status)

        # --- Notes Section ---
        notes_label = qt.QLabel("Annotation Notes (saved with export)")
        notes_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        self.layout().addWidget(notes_label)

        self.notes_edit = qt.QPlainTextEdit()
        self.notes_edit.setPlaceholderText("Add any observations about scan quality, artifacts, segmentation decisions, or landmark rationale...")
        self.notes_edit.setMaximumHeight(100)
        self.notes_edit.textChanged.connect(lambda: setattr(self, 'has_unsaved_work', True))
        self.layout().addWidget(self.notes_edit)

        # Internal variables for preview
        self.preview_node = None
        self.current_preview_pos = None
        self.current_preview_id = -1

    # ------------------------------------------------------------------
    # DIRECT CENTERLINE CLICK MODE (IMPROVED)
    # ------------------------------------------------------------------
    def toggle_click_mode(self):
        """Toggle the direct centerline click mode"""
        if self.click_mode_active:
            self.disable_centerline_click_mode()
        else:
            self.enable_centerline_click_mode()

    def enable_centerline_click_mode(self):
        """Enable clicking on centerline to add zone points"""
        # Get selected centerline
        self.centerline_node = self.centerline_combo.currentNode()
        
        if not self.centerline_node:
            qt.QMessageBox.warning(self, "No Centerline", 
                "Please select a centerline model first.")
            return
        
        # Validate centerline has points
        if not self.centerline_node.GetPolyData() or self.centerline_node.GetPolyData().GetNumberOfPoints() == 0:
            qt.QMessageBox.warning(self, "Invalid Centerline",
                "Selected model has no points. Please select a valid centerline.")
            return
        
        # Create zone node if not exists
        if not self.zone_node:
            self.zone_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsFiducialNode", 
                f"{self.current_id}_Zones"
            )
            display = self.zone_node.GetDisplayNode()
            if display:
                display.SetSelectedColor(1, 0.5, 0)  # Orange
                display.SetGlyphScale(3.0)
                display.SetTextScale(4.0)

        # Create Preview Node (Temporary point)
        self.preview_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLMarkupsFiducialNode", 
            "Zone_Preview_Temp"
        )
        self.preview_node.GetDisplayNode().SetSelectedColor(0, 1, 1) # Cyan
        self.preview_node.GetDisplayNode().SetGlyphScale(4.0)
        self.preview_node.GetDisplayNode().SetTextScale(0) # No label
        
        # Make centerline pickable
        display_node = self.centerline_node.GetDisplayNode()
        if display_node:
            display_node.SetVisibility(True)
            # Highlight centerline
            original_color = display_node.GetColor()
            self.original_centerline_color = original_color
            display_node.SetColor(0, 1, 0)  # Green when active
            display_node.SetLineWidth(3)
        
        # Setup point picking
        self.setup_point_picking()
        
        self.click_mode_active = True
        self.btn_click_mode.setText("🛑 Stop Picker")
        self.btn_click_mode.setStyleSheet("background-color: #dc3545; color: white; font-weight: bold; padding: 10px;")
        self.lbl_click_status.setText("Picker: ON - Left-click to preview, then Confirm")
        self.lbl_click_status.setStyleSheet("color: #28a745; font-weight: bold;")
        
        self.lbl_status.setText(f"✓ Picker active. Left-click on the vessel/centerline to preview cross-section.")

    def disable_centerline_click_mode(self):
        """Disable the click mode"""
        # Restore centerline color
        if self.centerline_node and hasattr(self, 'original_centerline_color'):
            display_node = self.centerline_node.GetDisplayNode()
            if display_node:
                display_node.SetColor(self.original_centerline_color)
                display_node.SetLineWidth(1)
        
        # Remove observer
        self.remove_point_picking()
        
        # Remove preview node
        if self.preview_node:
            slicer.mrmlScene.RemoveNode(self.preview_node)
            self.preview_node = None
        
        self.click_mode_active = False
        self.btn_click_mode.setText("🎯 Start Zone Picker")
        self.btn_click_mode.setStyleSheet("background-color: #17a2b8; color: white; font-weight: bold; padding: 10px;")
        self.lbl_click_status.setText("Picker: OFF")
        self.lbl_click_status.setStyleSheet("color: #666; font-style: italic;")
        self.btn_confirm_point.setEnabled(False)

    def setup_point_picking(self):
        """Setup VTK point picking on the centerline"""
        threeDWidget = slicer.app.layoutManager().threeDWidget(0)
        if not threeDWidget:
            return
        
        threeDView = threeDWidget.threeDView()
        renderWindow = threeDView.renderWindow()
        interactor = renderWindow.GetInteractor()
        
        # Use CellPicker for better surface detection
        self.cell_picker = vtk.vtkCellPicker()
        self.cell_picker.SetTolerance(0.005)
        
        # Store original picker
        self.original_picker = interactor.GetPicker()
        interactor.SetPicker(self.cell_picker)
        
        # Add observer for left click with high priority
        self.click_observer_tag = interactor.AddObserver(
            vtk.vtkCommand.LeftButtonPressEvent, 
            self.on_centerline_clicked,
            1.0 # High priority
        )
        
        # Change cursor to crosshair
        threeDView.setCursor(qt.Qt.CrossCursor)
        
        print("✓ Point picking enabled")

    def remove_point_picking(self):
        """Remove point picking observer"""
        threeDWidget = slicer.app.layoutManager().threeDWidget(0)
        if not threeDWidget:
            return
        
        threeDView = threeDWidget.threeDView()
        renderWindow = threeDView.renderWindow()
        interactor = renderWindow.GetInteractor()
        
        # Remove observer
        if hasattr(self, 'click_observer_tag'):
            interactor.RemoveObserver(self.click_observer_tag)
            del self.click_observer_tag
        
        # Restore original picker
        if hasattr(self, 'original_picker') and self.original_picker:
            interactor.SetPicker(self.original_picker)
            
        # Restore cursor
        threeDView.setCursor(qt.Qt.ArrowCursor)

    def on_centerline_clicked(self, caller, event):
        """Handle click on centerline"""
        if not self.click_mode_active:
            return
        
        interactor = caller
        click_pos = interactor.GetEventPosition()
        
        # Perform pick
        threeDWidget = slicer.app.layoutManager().threeDWidget(0)
        threeDView = threeDWidget.threeDView()
        renderer = threeDView.renderWindow().GetRenderers().GetFirstRenderer()
        
        self.cell_picker.Pick(click_pos[0], click_pos[1], 0, renderer)
        
        if self.cell_picker.GetCellId() >= 0:
            world_pos = self.cell_picker.GetPickPosition()
            
            try:
                # Find nearest point on centerline
                nearest_point_id = self.find_nearest_centerline_point(world_pos)
                
                if nearest_point_id >= 0:
                    # Snap to centerline
                    exact_pos = self.centerline_node.GetPolyData().GetPoints().GetPoint(nearest_point_id)
                    self.update_preview_state(exact_pos, nearest_point_id)
                    
                    # Abort event to prevent camera rotation if we hit something
                    # This fixes the "Left click not working" issue
                    # caller.SetAbortEvent(True) # Uncomment if you want to strictly block rotation on hit
            except Exception as e:
                # Prevent crashes from bubbling up to the interactor
                self.lbl_status.setText(f"Error during pick/preview: {str(e)}")
                import traceback
                traceback.print_exc()
        
    def update_preview_state(self, pos, point_id):
        """Update the preview point and cross-section view"""
        self.current_preview_pos = pos
        self.current_preview_id = point_id

        # 1. Update Preview Markup
        if self.preview_node:
            self.preview_node.RemoveAllControlPoints()
            self.preview_node.AddControlPoint(pos[0], pos[1], pos[2])
            self.preview_node.SetNthControlPointLabel(0, "Preview")

        # 2. Calculate Tangent
        tangent = self.get_tangent_at_point(self.centerline_node.GetPolyData(), point_id)

        # 3. Update Yellow Slice
        yellow_slice = slicer.app.layoutManager().sliceWidget('Yellow')
        yellow_logic = yellow_slice.sliceLogic()
        n = np.array(tangent)
        n = n / np.linalg.norm(n)

        # Arbitrary vector to create basis
        a = np.array([0, 0, 1]) if abs(n[2]) < 0.9 else np.array([0, 1, 0])
        t1 = np.cross(n, a)
        t1 = t1 / np.linalg.norm(t1)

        # Set Yellow slice orientation
        yellow_logic.GetSliceNode().SetSliceToRASByNTP(
            n[0], n[1], n[2],
            t1[0], t1[1], t1[2],
            pos[0], pos[1], pos[2], 0)

        # Keep Yellow slice visible in 2D but hide in 3D
        yellow_slice_node = yellow_logic.GetSliceNode()
        yellow_slice_node.SetSliceVisible(False)
        
        # 4. Create or update the preview plane - FIXED SIZE
        self.create_or_update_preview_plane(pos, n, t1)

        # 5. Enable Confirm
        self.btn_confirm_point.setEnabled(True)
        self.lbl_status.setText(f"Previewing point {point_id}. Check 3D view for RED plane. Click 'Confirm' to add.")

    def create_or_update_preview_plane(self, center, normal, x_axis):
        """Create a visual plane in 3D view to show cross-section location"""
        
        # Remove old plane if exists
        if hasattr(self, 'preview_plane_node') and self.preview_plane_node:
            slicer.mrmlScene.RemoveNode(self.preview_plane_node)
            self.preview_plane_node = None
        
        # Create a disk using vtkDiskSource for better visibility
        disk = vtk.vtkDiskSource()
        disk.SetInnerRadius(0)
        disk.SetOuterRadius(30)  # Increased from 20 to 30mm
        disk.SetRadialResolution(30)
        disk.SetCircumferentialResolution(30)
        
        # Create transformation to orient disk
        y_axis = np.cross(normal, x_axis)
        y_axis = y_axis / np.linalg.norm(y_axis)
        
        # Create 4x4 matrix
        matrix = vtk.vtkMatrix4x4()
        for i in range(3):
            matrix.SetElement(i, 0, x_axis[i])
            matrix.SetElement(i, 1, y_axis[i])
            matrix.SetElement(i, 2, normal[i])
            matrix.SetElement(i, 3, center[i])
        
        # Apply transformation
        transform = vtk.vtkTransform()
        transform.SetMatrix(matrix)
        
        transformFilter = vtk.vtkTransformPolyDataFilter()
        transformFilter.SetInputConnection(disk.GetOutputPort())
        transformFilter.SetTransform(transform)
        transformFilter.Update()
        
        # Create model node
        self.preview_plane_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode", "Preview_Plane_Temp")
        self.preview_plane_node.SetAndObservePolyData(transformFilter.GetOutput())
        
        # Ensure a display node exists and set its properties (visibility must be explicitly set)
        self.preview_plane_node.CreateDefaultDisplayNodes()
        display = self.preview_plane_node.GetDisplayNode()
        if not display:
            # fallback: create and attach a display node
            displayNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelDisplayNode")
            self.preview_plane_node.SetAndObserveDisplayNodeID(displayNode.GetID())
            display = self.preview_plane_node.GetDisplayNode()
        
        if display:
            display.SetColor(1, 0, 0)  # Bright red
            display.SetOpacity(0.5)    # Semi-transparent
            display.SetBackfaceCulling(False)
            display.SetVisibility(True)  # Make sure it's visible in 3D
            # Prefer newer API if available, fall back to deprecated call
            if hasattr(display, 'SetVisibility2D'):
                display.SetVisibility2D(True)
            elif hasattr(display, 'SetSliceIntersectionVisibility'):
                display.SetSliceIntersectionVisibility(True)
            # NOTE: Model display nodes do not support SetGlyphScale (was causing AttributeError)
    
        # Force render update
        slicer.app.processEvents()
        threeDWidget = slicer.app.layoutManager().threeDWidget(0)
        if threeDWidget:
            threeDWidget.threeDView().forceRender()

    def get_tangent_at_point(self, polydata, point_id):
        """Calculate or retrieve tangent vector at centerline point"""
        # Try to find existing array
        point_data = polydata.GetPointData()
        tangents = point_data.GetArray("Tangents") or point_data.GetArray("FrenetTangent")
        if tangents:
            return tangents.GetTuple3(point_id)
        
        # Fallback: Geometric calculation
        n_points = polydata.GetNumberOfPoints()
        p_curr = list(polydata.GetPoint(point_id))
        
        # Get neighbors (handle ends)
        idx_prev = max(0, point_id - 1)
        idx_next = min(n_points - 1, point_id + 1)
        
        p_prev = list(polydata.GetPoint(idx_prev))
        p_next = list(polydata.GetPoint(idx_next))
        
        import math
        # Central difference
        vec = [p_next[i] - p_prev[i] for i in range(3)]
        norm = math.sqrt(sum(x*x for x in vec))
        
        if norm > 1e-6:
            return [x/norm for x in vec]
        return [0, 0, 1] # Default Z-axis

    def confirm_zone_point(self):
        """Add the currently previewed point to the zone list"""
        if not self.current_preview_pos:
            self.lbl_status.setText("⚠ No preview point to confirm!")
            return
        
        # Ensure zone node exists
        if not self.zone_node:
            self.zone_node = slicer.mrmlScene.AddNewNodeByClass(
                "vtkMRMLMarkupsFiducialNode", 
                f"{self.current_id}_Zones"
            )
            display = self.zone_node.GetDisplayNode()
            if display:
                display.SetSelectedColor(1, 0.5, 0)  # Orange
                display.SetGlyphScale(3.0)
                display.SetTextScale(4.0)

        # Add the point
        self.add_zone_point(self.current_preview_pos, self.current_preview_id)

        # Clean up preview
        self.btn_confirm_point.setEnabled(False)
        
        if self.preview_node:
            self.preview_node.RemoveAllControlPoints()
        
        # Remove preview plane
        if hasattr(self, 'preview_plane_node') and self.preview_plane_node:
            slicer.mrmlScene.RemoveNode(self.preview_plane_node)
            self.preview_plane_node = None
        
        # Reset state
        self.current_preview_pos = None
        self.current_preview_id = -1
        
        self.lbl_status.setText(f"✓ Zone point confirmed! Total: {self.zone_node.GetNumberOfControlPoints()}. Click for next point.")

    def try_cell_picker_fallback(self, click_pos, renderer):
        """Fallback picking using cell picker with larger tolerance"""
        # This method is now largely redundant as we use CellPicker by default,
        # but kept for compatibility if called elsewhere
        pass

    def find_nearest_centerline_point(self, world_pos):
        """Find the nearest point on the centerline to the given position"""
        if not self.centerline_node or not self.centerline_node.GetPolyData():
            return -1
        
        points = self.centerline_node.GetPolyData().GetPoints()
        if not points:
            return -1
        
        # Use VTK point locator for efficiency
        locator = vtk.vtkPointLocator()
        locator.SetDataSet(self.centerline_node.GetPolyData())
        locator.BuildLocator()
        
        nearest_id = locator.FindClosestPoint(world_pos)
        return nearest_id

    def add_zone_point(self, world_pos, point_id=-1):
        """Add a zone point at the given position"""
        if not self.zone_node:
            return
        
        n = self.zone_node.GetNumberOfControlPoints()
        self.zone_node.AddControlPoint(world_pos[0], world_pos[1], world_pos[2])
        
        # Set label
        label = f"Zone_{n+1}"
        if point_id >= 0:
            label += f"_P{point_id}"
        self.zone_node.SetNthControlPointLabel(n, label)
        
        # Update UI
        self.update_zone_counter()
        self.update_zone_list()
        
        self.has_unsaved_work = True
        
        status_msg = f"✓ Added {label} at ({world_pos[0]:.1f}, {world_pos[1]:.1f}, {world_pos[2]:.1f})"
        self.lbl_status.setText(status_msg)
        print(status_msg)

    def undo_last_zone_point(self):
        """Remove the last added zone point"""
        if not self.zone_node:
            return
        
        n = self.zone_node.GetNumberOfControlPoints()
        if n > 0:
            self.zone_node.RemoveNthControlPoint(n - 1)
            self.update_zone_counter()
            self.update_zone_list()
            self.lbl_status.setText(f"↩ Removed last point. {n-1} points remaining.")

    def update_zone_list(self):
        """Update the zone points list widget"""
        self.zone_list.clear()
        
        if not self.zone_node:
            return
        
        for i in range(self.zone_node.GetNumberOfControlPoints()):
            label = self.zone_node.GetNthControlPointLabel(i)
            pos = [0, 0, 0]
            self.zone_node.GetNthControlPointPosition(i, pos)
            item_text = f"{label}: ({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f})"
            self.zone_list.addItem(item_text)

    def jump_to_zone_point(self, item):
        """Jump to the selected zone point in all views"""
        index = self.zone_list.row(item)
        if self.zone_node and 0 <= index < self.zone_node.GetNumberOfControlPoints():
            pos = [0, 0, 0]
            self.zone_node.GetNthControlPointPosition(index, pos)
            
            # Jump all slice views to this point
            slicer.util.setSliceViewerLayers(background=self.vol_node)
            for color in ['Red', 'Yellow', 'Green']:
                sliceWidget = slicer.app.layoutManager().sliceWidget(color)
                if sliceWidget:
                    sliceWidget.sliceLogic().SetSliceOffset(pos[2] if color == 'Red' else (pos[1] if color == 'Green' else pos[0]))
            
            # Center 3D view on point
            threeDWidget = slicer.app.layoutManager().threeDWidget(0)
            if threeDWidget:
                threeDWidget.threeDView().setFocalPoint(pos[0], pos[1], pos[2])
            
            self.lbl_status.setText(f"Jumped to: {self.zone_node.GetNthControlPointLabel(index)}")

    def show_zone_context_menu(self, position):
        """Show context menu for zone list"""
        item = self.zone_list.itemAt(position)
        if not item:
            return
        
        menu = qt.QMenu()
        delete_action = menu.addAction("Delete Point")
        rename_action = menu.addAction("Rename Point")
        
        action = menu.exec_(self.zone_list.mapToGlobal(position))
        
        index = self.zone_list.row(item)
        
        if action == delete_action:
            if self.zone_node and 0 <= index < self.zone_node.GetNumberOfControlPoints():
                self.zone_node.RemoveNthControlPoint(index)
                self.update_zone_counter()
                self.update_zone_list()
        elif action == rename_action:
            new_name, ok = qt.QInputDialog.getText(self, "Rename Point", "New label:", 
                qt.QLineEdit.Normal, self.zone_node.GetNthControlPointLabel(index))
            if ok and new_name:
                self.zone_node.SetNthControlPointLabel(index, new_name)
                self.update_zone_list()

    # ------------------------------------------------------------------
    # CRASH RECOVERY & AUTOSAVE
    # ------------------------------------------------------------------
    def setup_autosave(self):
        """Setup automatic state saving"""
        self.autosave_timer = qt.QTimer()
        self.autosave_timer.timeout.connect(self.autosave_state)
        self.autosave_timer.start(30000)  # Every 30 seconds

    def get_state_file_path(self):
        """Get path for state file"""
        temp_dir = slicer.app.temporaryPath
        return os.path.join(temp_dir, "taa_wizard_state.json")

    def autosave_state(self):
        """Automatically save current state"""
        if not self.current_id or not self.has_unsaved_work:
            return
        
        try:
            state = {
                "timestamp": datetime.now().isoformat(),
                "current_id": self.current_id,
                "root_dir": self.root_dir,
                "phase": self.workflow_state["phase"],
                "zone_count": self.zone_node.GetNumberOfControlPoints() if self.zone_node else 0,
                "vol_node_id": self.vol_node.GetID() if self.vol_node else None,
                "seg_node_id": self.seg_node.GetID() if self.seg_node else None,
                "notes": (self.notes_edit.toPlainText()[:500] if self.notes_edit else "")
            }
            
            state_file = self.get_state_file_path()
            with open(state_file, 'w') as f:
                json.dump(state, f, indent=2)
            
            print(f"[Autosave] State saved at {state['timestamp']}")
            
        except Exception as e:
            print(f"[Autosave] Failed: {e}")

    def attempt_crash_recovery(self):
        """Check for previous session and offer recovery"""
        state_file = self.get_state_file_path()
        
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r') as f:
                    saved_state = json.load(f)
                
                # Check if state is recent (within 24 hours)
                saved_time = datetime.fromisoformat(saved_state["timestamp"])
                time_diff = datetime.now() - saved_time
                
                if time_diff.total_seconds() < 86400:  # 24 hours
                    self.saved_recovery_state = saved_state
                    self.recovery_label.setText(
                        f"⚠ Found session: {saved_state['current_id']} "
                        f"(Phase {saved_state['phase']}, "
                        f"{(time_diff.total_seconds() / 3600):.1f}h ago)"
                    )
                    self.recovery_banner.show()
                else:
                    # Old state, delete it
                    os.remove(state_file)
                    
            except Exception as e:
                print(f"[Recovery] Could not read state: {e}")

    def recover_session(self):
        """Recover previous session"""
        if not hasattr(self, 'saved_recovery_state'):
            return
        
        try:
            state = self.saved_recovery_state
            self.recovery_banner.hide()
            
            # Restore basic info
            self.current_id = state["current_id"]
            self.root_dir = state["root_dir"]
            self.workflow_state["phase"] = state["phase"]
            
            # Attempt to reload data
            self.lbl_status.setText(f"Recovering session for {self.current_id}...")
            slicer.app.processEvents()
            
            # Load scene
            self.on_load_data_internal(self.root_dir)
            
            # Update UI based on phase
            if state["phase"] >= 1:
                self.mark_done(self.btn_load, "Data Loaded")
                self.btn_seg.setEnabled(True)
            if state["phase"] >= 2:
                self.mark_done(self.btn_seg, "Refine Mode")
                self.btn_vmtk.setEnabled(True)
            if state["phase"] >= 3:
                self.mark_done(self.btn_vmtk, "VMTK Ready")
                self.btn_click_mode.setEnabled(True)
            # Restore notes preview
            if self.notes_edit and state.get("notes"):
                self.notes_edit.setPlainText(state.get("notes", ""))
            
            self.lbl_status.setText(f"✓ Recovered session: {self.current_id}")
            qt.QMessageBox.information(self, "Recovery Successful", 
                f"Session restored!\n\nContinue from Phase {state['phase']}")
            
        except Exception as e:
            slicer.util.errorDisplay(f"Recovery failed: {str(e)}")
            self.ignore_recovery()

    def ignore_recovery(self):
        """Ignore recovery and start fresh"""
        self.recovery_banner.hide()
        self.cleanup_autosave()

    def cleanup_autosave(self):
        """Remove autosave file"""
        state_file = self.get_state_file_path()
        if os.path.exists(state_file):
            try:
                os.remove(state_file)
            except:
                pass

    def quick_save(self):
        """Manual quick save without resetting"""
        if not self.current_id:
            return
        
        try:
            save_dir = os.path.join(self.root_dir, f"{self.current_id}_GT_Bundle")
            if not os.path.exists(save_dir):
                os.makedirs(save_dir)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Save current progress
            if self.seg_node:
                slicer.util.saveNode(self.seg_node, 
                    os.path.join(save_dir, f"{self.current_id}_WIP_{timestamp}.seg.nrrd"))
            
            if self.zone_node and self.zone_node.GetNumberOfControlPoints() > 0:
                slicer.util.saveNode(self.zone_node,
                    os.path.join(save_dir, f"{self.current_id}_Zones_WIP_{timestamp}.fcsv"))
            # Save notes (WIP)
            if self.notes_edit:
                notes_txt = self.notes_edit.toPlainText().strip()
                if notes_txt:
                    with open(os.path.join(save_dir, f"{self.current_id}_notes_WIP_{timestamp}.txt"), "w", encoding="utf-8") as f:
                        f.write(notes_txt + "\n")
            
            self.lbl_status.setText(f"✓ Quick saved at {datetime.now().strftime('%H:%M:%S')}")
            
        except Exception as e:
            slicer.util.errorDisplay(f"Quick save failed: {str(e)}")

    # ------------------------------------------------------------------
    # UTILITY FUNCTIONS
    # ------------------------------------------------------------------
    def safe_execute(self, func, error_msg="Operation failed"):
        """Wrapper for safe function execution"""
        try:
            return func()
        except Exception as e:
            slicer.util.errorDisplay(f"{error_msg}\n\nError: {str(e)}")
            import traceback
            traceback.print_exc()
            return None

    def mark_done(self, button, text):
        button.setStyleSheet(self.done_style)
        button.setText(f"✔ {text}")

    def get_id_from_files(self, folder_path):
        try:
            search_pattern = os.path.join(folder_path, "ct_scan_*.nii.gz")
            found_files = glob.glob(search_pattern)
            if not found_files: return None
            filename = os.path.basename(found_files[0])
            return filename.replace("ct_scan_", "").replace(".nii.gz", "")
        except:
            return None

    def update_zone_counter(self):
        """Update zone point counter"""
        if self.zone_node:
            count = self.zone_node.GetNumberOfControlPoints()
            self.lbl_zone_count.setText(f"Zone Points: {count}")
            self.workflow_state["zone_count"] = count
            self.btn_undo_point.setEnabled(count > 0)

    # ------------------------------------------------------------------
    # STEP 1: LOAD (ENHANCED ERROR HANDLING)
    # ------------------------------------------------------------------
    def on_load_data(self):
        """Public wrapper for load"""
        folder_path = qt.QFileDialog.getExistingDirectory(self, "Select Subject Directory")
        if not folder_path:
            return
        
        self.on_load_data_internal(folder_path)

    def on_load_data_internal(self, folder_path):
        """Internal load with full error handling"""
        def load_operation():
            self.root_dir = folder_path
            detected_id = self.get_id_from_files(folder_path)
            
            if not detected_id:
                raise FileNotFoundError("Could not find 'ct_scan_*.nii.gz' in selected folder")

            self.current_id = detected_id
            self.lbl_status.setText(f"Loading {self.current_id}...")
            slicer.app.processEvents()
            
            # Clear scene with confirmation if needed
            if slicer.mrmlScene.GetNumberOfNodes() > 0:
                slicer.mrmlScene.Clear(0)

            vol_path = os.path.join(folder_path, f"ct_scan_{self.current_id}.nii.gz")
            seg_path = os.path.join(folder_path, f"{self.current_id}_unified_mask_smoothed.nii.gz")
            ref_path = os.path.join(folder_path, f"{self.current_id}_merged.nii.gz")

            if not os.path.exists(vol_path):
                raise FileNotFoundError(f"Missing CT scan file: {vol_path}")
            if not os.path.exists(seg_path):
                raise FileNotFoundError(f"Missing unified segmentation: {seg_path}")
            
            # Load with error checking
            self.vol_node = slicer.util.loadVolume(vol_path)
            if not self.vol_node:
                raise RuntimeError("Failed to load CT volume")
            
            # Load segmentation as labelmap first to binarize
            self.lbl_status.setText(f"Binarizing segmentation mask...")
            slicer.app.processEvents()
            
            temp_labelmap = slicer.util.loadLabelVolume(seg_path)
            if not temp_labelmap:
                raise RuntimeError("Failed to load segmentation")
            
            # Binarize: convert all non-zero values to 1
            import numpy as np
            array = slicer.util.arrayFromVolume(temp_labelmap)
            array[array > 0] = 1
            slicer.util.updateVolumeFromArray(temp_labelmap, array)
            
            # Convert to segmentation node
            self.seg_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", f"{self.current_id}_unified_binary")
            slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(temp_labelmap, self.seg_node)
            
            # Clean up temporary labelmap
            slicer.mrmlScene.RemoveNode(temp_labelmap)
            
            if os.path.exists(ref_path):
                self.ref_node = slicer.util.loadSegmentation(ref_path)

            # Setup display
            slicer.app.layoutManager().sliceWidget('Red').sliceLogic().GetSliceCompositeNode().SetBackgroundVolumeID(self.vol_node.GetID())
            self.seg_node.CreateClosedSurfaceRepresentation()
            slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)
            
            # Enable next steps
            self.mark_done(self.btn_load, "Data Loaded")
            self.btn_seg.setEnabled(True)
            self.btn_quick_save.setEnabled(True)
            
            self.workflow_state["phase"] = 1
            self.has_unsaved_work = True
            self.lbl_status.setText(f"✓ Loaded: {self.current_id} (Binary mask)")
        
        self.safe_execute(load_operation, "Failed to load data")

    # ------------------------------------------------------------------
    # STEP 2: REFINE (ENHANCED)
    # ------------------------------------------------------------------
    def on_refine_setup(self):
        def refine_operation():
            if not self.seg_node:
                raise RuntimeError("No segmentation loaded")
            
            slicer.util.selectModule("SegmentEditor")
            
            segmentEditorNode = slicer.mrmlScene.GetSingletonNode("SegmentEditor", "vtkMRMLSegmentEditorNode")
            if segmentEditorNode:
                segmentEditorNode.SetAndObserveSegmentationNode(self.seg_node)
                segmentEditorNode.SetAndObserveSourceVolumeNode(self.vol_node)

            # Force visibility
            if self.ref_node:
                self.ref_node.GetDisplayNode().SetVisibility(False)
            
            self.seg_node.GetDisplayNode().SetVisibility(True)
            
            self.mark_done(self.btn_seg, "Refine Mode")
            self.btn_vmtk.setEnabled(True)
            self.workflow_state["phase"] = 2
            
            qt.QMessageBox.information(self, "Refinement Mode", 
                "Proceed with topological cleaning.\n"
                "1. Assess 3D view for artifacts (Use scissors or island tool)\n"
                "Click 'Quick Save' periodically to save progress.")
        
        self.safe_execute(refine_operation, "Failed to setup refinement mode")

    # ------------------------------------------------------------------
    # STEP 3: VMTK (ENHANCED)
    # ------------------------------------------------------------------
    def on_vmtk_setup(self):
        def vmtk_operation():
            if not hasattr(slicer.modules, 'extractcenterline'):
                raise RuntimeError("VMTK Extension not installed. Install from Extension Manager.")

            self.network_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelNode", f"{self.current_id}_Network")
            self.endpoint_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", f"{self.current_id}_Endpoints")

            # Reduce segmentation opacity to see endpoints clearly
            if self.seg_node:
                display_node = self.seg_node.GetDisplayNode()
                if display_node:
                    current_opacity = display_node.GetOpacity3D()
                    new_opacity = max(0.0, current_opacity - 0.3)
                    display_node.SetOpacity3D(new_opacity)

            slicer.util.selectModule("ExtractCenterline")
            self.mark_done(self.btn_vmtk, "VMTK Ready")
            self.btn_click_mode.setEnabled(True)
            self.btn_export.setEnabled(True)
            self.workflow_state["phase"] = 3
            
            self.lbl_status.setText("✓ In VMTK Module: Extract centerline → Then use Click Mode for zones")
            
            qt.QMessageBox.information(self, "VMTK Setup",
                "Extract Centerline Instructions:\n\n"
                "1. Select your refined segmentation as input\n"
                "2. Place endpoints at vessel entry/exit points\n"
                "3. Click 'Apply' to extract centerline\n\n"
                "After extraction, use 'Start Click Mode' to add zone landmarks\n"
                "by clicking directly on the centerline in the 3D view.")
        
        self.safe_execute(vmtk_operation, "Failed to setup VMTK")

    # ------------------------------------------------------------------
    # STEP 5: EXPORT (ENHANCED WITH VALIDATION)
    # ------------------------------------------------------------------
    def on_export(self):
        def export_operation():
            # Disable click mode if active
            if self.click_mode_active:
                self.disable_centerline_click_mode()
            
            if not self.root_dir or not self.current_id:
                raise RuntimeError("No data loaded")
            
            # Validation
            if not self.seg_node:
                raise RuntimeError("No segmentation to export")
            
            # Warn if no zones captured
            if not self.zone_node or self.zone_node.GetNumberOfControlPoints() == 0:
                reply = qt.QMessageBox.question(
                    self, 
                    'No Zone Landmarks',
                    "No zone landmarks captured.\n\nContinue export anyway?",
                    qt.QMessageBox.Yes | qt.QMessageBox.No,
                    qt.QMessageBox.No
                )
                if reply == qt.QMessageBox.No:
                    return
            
            save_dir = os.path.join(self.root_dir, f"{self.current_id}_GT_Bundle")
            if not os.path.exists(save_dir):
                os.makedirs(save_dir)
            
            progress = slicer.util.createProgressDialog(parent=self, value=0, maximum=7)
            progress.labelText = "Starting export..."
            slicer.app.processEvents()
            
            exported_files = []
            
            try:
                # 1. Segmentation
                progress.labelText = "Saving refined mask..."
                progress.value = 1
                slicer.app.processEvents()
                
                seg_path = os.path.join(save_dir, f"{self.current_id}_refined_mask.seg.nrrd")
                slicer.util.saveNode(self.seg_node, seg_path)
                exported_files.append("✓ Refined Mask")
                
                # 2. Centerline
                progress.labelText = "Saving centerline..."
                progress.value = 2
                slicer.app.processEvents()
                
                centerline_model = self.find_node([
                    f"{self.current_id}_Centerline",
                    "Centerline model",
                    "Centerline Model"
                ])
                
                if centerline_model:
                    slicer.util.saveNode(centerline_model,
                        os.path.join(save_dir, f"{self.current_id}_Centerline.vtk"))
                    exported_files.append("✓ Centerline Model")
                
                # 3. Network
                progress.labelText = "Saving network..."
                progress.value = 3
                slicer.app.processEvents()
                
                network_model = self.find_node([
                    f"{self.current_id}_Network",
                    "Network model",
                    "Voronoi diagram"
                ])
                
                if network_model:
                    slicer.util.saveNode(network_model,
                        os.path.join(save_dir, f"{self.current_id}_Network.vtk"))
                    exported_files.append("✓ Network Model")
                
                # 4. Endpoints
                progress.labelText = "Saving endpoints..."
                progress.value = 4
                slicer.app.processEvents()
                
                endpoint_markups = self.find_node([
                    f"{self.current_id}_Endpoints",
                    "Centerline endpoints",
                    "Endpoints"
                ])
                
                if endpoint_markups:
                    slicer.util.saveNode(endpoint_markups,
                        os.path.join(save_dir, f"{self.current_id}_Endpoints.fcsv"))
                    exported_files.append("✓ Endpoints")
                
                # 5. Zones
                progress.labelText = "Saving zone landmarks..."
                progress.value = 5
                slicer.app.processEvents()
                
                if self.zone_node and self.zone_node.GetNumberOfControlPoints() > 0:
                    slicer.util.saveNode(self.zone_node,
                        os.path.join(save_dir, f"{self.current_id}_Zones.fcsv"))
                    exported_files.append(f"✓ Zone Landmarks ({self.zone_node.GetNumberOfControlPoints()} points)")
                
                # 6. Notes
                progress.labelText = "Saving notes..."
                progress.value = 6
                slicer.app.processEvents()
                
                notes_txt = self.notes_edit.toPlainText().strip() if self.notes_edit else ""
                if notes_txt:
                    notes_path = os.path.join(save_dir, f"{self.current_id}_notes.txt")
                    with open(notes_path, "w", encoding="utf-8") as f:
                        f.write(notes_txt + "\n")
                    exported_files.append("✓ Notes")
                
                # 7. Save metadata
                progress.labelText = "Saving metadata..."
                progress.value = 7
                slicer.app.processEvents()
                
                metadata = {
                    "patient_id": self.current_id,
                    "export_date": datetime.now().isoformat(),
                    "workflow_version": "2.1_direct_click",
                    "exported_files": exported_files,
                    "zone_count": self.zone_node.GetNumberOfControlPoints() if self.zone_node else 0,
                    "has_notes": bool(notes_txt),
                    "notes_preview": notes_txt[:120]
                }
                
                with open(os.path.join(save_dir, "export_metadata.json"), 'w') as f:
                    json.dump(metadata, f, indent=2)
                
                progress.close()
                
                # Success message
                export_msg = f"✓ Export Complete!\n\nSaved to:\n{save_dir}\n\nFiles:\n"
                export_msg += "\n".join(exported_files)
                
                qt.QMessageBox.information(self, "Export Complete", 
                    export_msg + "\n\nClick OK to reset for next scan.")
                
                # Clear unsaved work flag
                self.has_unsaved_work = False
                self.cleanup_autosave()
                
                # Reset
                self.reset_application()
                
            except Exception as e:
                progress.close()
                raise e
        
        self.safe_execute(export_operation, "Export failed")

    def find_node(self, possible_names):
        """Find node by trying multiple possible names"""
        for name in possible_names:
            node = slicer.util.getFirstNodeByName(name)
            if node:
                return node
        return None

    def reset_application(self):
        """Enhanced reset with confirmation"""
        # Clean up click mode
        self.disable_centerline_click_mode()
        
        slicer.mrmlScene.Clear(0)
        
        self.current_id = ""
        self.root_dir = ""
        self.vol_node = None
        self.seg_node = None
        self.ref_node = None
        self.zone_node = None
        self.network_node = None
        self.endpoint_node = None
        self.centerline_node = None
        
        self.workflow_state = {
            "phase": 0,
            "last_save": None,
            "unsaved_changes": False,
            "zone_count": 0
        }
        
        self.has_unsaved_work = False
        
        # Reset UI
        self.btn_load.setStyleSheet(self.default_style)
        self.btn_load.setText("1. Load Data & Initialize")
        
        self.btn_seg.setStyleSheet(self.default_style)
        self.btn_seg.setText("2. Refine Mask")
        self.btn_seg.setEnabled(False)
        
        self.btn_vmtk.setStyleSheet(self.default_style)
        self.btn_vmtk.setText("3. Extract VMTK Centerline")
        self.btn_vmtk.setEnabled(False)
        
        self.btn_click_mode.setStyleSheet("background-color: #17a2b8; color: white; font-weight: bold; padding: 10px;")
        self.btn_click_mode.setText("🎯 Start Zone Picker")
        self.btn_click_mode.setEnabled(False)
        
        self.btn_undo_point.setEnabled(False)
        self.btn_quick_save.setEnabled(False)
        self.btn_export.setEnabled(False)
        
        # Clear notes field
        if self.notes_edit:
            self.notes_edit.blockSignals(True)
            self.notes_edit.setPlainText("")
            self.notes_edit.blockSignals(False)
        
        # Clear zone list
        self.zone_list.clear()
        self.lbl_zone_count.setText("Zone Points: 0")
        self.lbl_click_status.setText("Picker: OFF")
        self.lbl_click_status.setStyleSheet("color: #666; font-style: italic;")
        
        self.lbl_status.setText("✓ System Reset. Ready for next scan.")


# Run the wizard
try:
    wizard = AnnotationGUI()
except Exception as e:
    slicer.util.errorDisplay(f"Failed to start TAA Wizard:\n\n{str(e)}")
    import traceback
    traceback.print_exc()