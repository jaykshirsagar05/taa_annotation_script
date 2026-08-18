"""
Tests for CenterlinePicker scroll navigation (arc-length stepping + wheel
coalescing).

Runs without a Slicer process: ``slicer`` and ``vtk`` are MagicMocks, while
``qt`` is a small hand-written stub because CenterlinePicker subclasses
qt.QObject at module level and installs real QTimers.
"""

import os
import sys
import types
import unittest
from unittest.mock import MagicMock

import numpy as np


# ── qt stub (needs real classes, not MagicMocks) ─────────────────────────────

class _FakeTimer:
    """QTimer stand-in whose firing is driven explicitly by the tests."""

    def __init__(self):
        self._active = False
        self._cb = None
        self.timeout = self          # so `timer.timeout.connect(cb)` works
        self.interval = 0
        self.startCount = 0

    def connect(self, cb):
        self._cb = cb

    def setInterval(self, ms):
        self.interval = ms

    def setSingleShot(self, _):
        pass

    def isActive(self):
        return self._active

    def start(self):
        self._active = True
        self.startCount += 1

    def stop(self):
        self._active = False

    def fire(self):
        self._active = False
        if self._cb:
            self._cb()


class _QObject:
    def __init__(self, *a, **kw):
        pass


_qt = types.ModuleType("qt")
_qt.QObject = _QObject
_qt.QTimer = _FakeTimer
_qt.QEvent = types.SimpleNamespace(Wheel="Wheel")


class _AutoMeta(type):
    """Serves class-level enum lookups such as qt.QSizePolicy.Expanding."""

    def __getattr__(cls, name):
        return MagicMock()


class _AutoBase(metaclass=_AutoMeta):
    """Permissive stand-in for arbitrary Qt classes; safe to subclass."""

    def __init__(self, *a, **kw):
        pass

    def __getattr__(self, name):
        return MagicMock()


_auto_classes = {}


def _qt_getattr(name):
    """Auto-create a real (subclassable) class for any other Qt name.

    Sibling test modules import TAAAnnotationLib, whose widgets subclass
    qt.QWidget at module scope.  This stub is installed process-wide, so it has
    to be a superset of the MagicMock those tests would otherwise get.
    """
    if name not in _auto_classes:
        _auto_classes[name] = type(name, (_AutoBase,), {})
    return _auto_classes[name]


_qt.__getattr__ = _qt_getattr           # PEP 562 module-level __getattr__
sys.modules["qt"] = _qt

for _mod in ("slicer", "slicer.util", "vtk", "vtk.util"):
    sys.modules.setdefault(_mod, MagicMock())

# Real vtk_to_numpy so the arc-length maths is genuinely tested.
_nps = types.ModuleType("vtk.util.numpy_support")
_nps.vtk_to_numpy = lambda arr: np.asarray(arr)
sys.modules["vtk.util.numpy_support"] = _nps

# CenterlinePicker has no relative imports, so load it straight from disk and
# skip TAAAnnotationLib/__init__.py (which drags in the Qt widget modules).
import importlib.util   # noqa: E402

_HERE = os.path.dirname(__file__)
_CP_PATH = os.path.join(
    _HERE, "..", "TAAAnnotation", "TAAAnnotationLib", "CenterlinePicker.py"
)
_spec = importlib.util.spec_from_file_location("_centerline_picker_uut", _CP_PATH)
_cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cp)

CenterlinePicker = _cp.CenterlinePicker
_YellowWheelFilter = _cp._YellowWheelFilter
SCROLL_STEP_MM = _cp.SCROLL_STEP_MM


# ── polydata / node fakes ────────────────────────────────────────────────────

class _FakePointData:
    def GetArray(self, _name):
        return None          # force the geometric tangent fallback


class _FakePolyData:
    def __init__(self, pts):
        self._pts = np.asarray(pts, dtype=np.float64)

    def GetNumberOfPoints(self):
        return len(self._pts)

    def GetPoint(self, i):
        return tuple(self._pts[int(i)])

    def GetPoints(self):
        return self

    def GetData(self):
        return self._pts

    def GetPointData(self):
        return _FakePointData()


def _straight_line_picker(spacing=0.5, n=201):
    """Picker wired to a straight centerline along +Z with uniform spacing."""
    pts = np.zeros((n, 3), dtype=np.float64)
    pts[:, 2] = np.arange(n) * spacing

    picker = CenterlinePicker(MagicMock(), MagicMock())
    picker.centerlineNode = MagicMock()
    picker.centerlineNode.GetPolyData.return_value = _FakePolyData(pts)
    picker._centerlinePath = list(range(n))
    picker._ensurePathArcLengths()

    picker.previewNode = MagicMock()
    picker.previewNode.GetNumberOfControlPoints.return_value = 1
    picker.currentPreviewPos = tuple(pts[0])
    picker.currentPreviewId = 0
    picker._currentPathIndex = 0
    return picker


# ── arc-length table ─────────────────────────────────────────────────────────

class TestPathArcLengths(unittest.TestCase):

    def test_uniform_spacing(self):
        p = _straight_line_picker(spacing=0.5, n=11)
        np.testing.assert_allclose(
            p._pathArcLengths, np.arange(11) * 0.5, atol=1e-9
        )

    def test_monotonic_with_irregular_spacing(self):
        pts = np.array([[0, 0, 0], [0, 0, 3], [0, 4, 3], [5, 4, 3]], float)
        p = CenterlinePicker(MagicMock(), MagicMock())
        p.centerlineNode = MagicMock()
        p.centerlineNode.GetPolyData.return_value = _FakePolyData(pts)
        p._centerlinePath = [0, 1, 2, 3]
        p._ensurePathArcLengths()
        np.testing.assert_allclose(p._pathArcLengths, [0.0, 3.0, 7.0, 12.0])
        self.assertTrue(np.all(np.diff(p._pathArcLengths) > 0))

    def test_cached_after_first_build(self):
        p = _straight_line_picker()
        first = p._pathArcLengths
        p._ensurePathArcLengths()
        self.assertIs(p._pathArcLengths, first)

    def test_single_point_path_is_safe(self):
        p = CenterlinePicker(MagicMock(), MagicMock())
        p.centerlineNode = MagicMock()
        p.centerlineNode.GetPolyData.return_value = _FakePolyData([[1, 2, 3]])
        p._centerlinePath = [0]
        p._ensurePathArcLengths()
        np.testing.assert_allclose(p._pathArcLengths, [0.0])


# ── index-at-offset lookup ───────────────────────────────────────────────────

class TestPathIndexAtOffset(unittest.TestCase):

    def setUp(self):
        # 0.5 mm spacing, 201 points => 100 mm of centerline
        self.p = _straight_line_picker(spacing=0.5, n=201)

    def test_offset_maps_to_nearest_point(self):
        # 1.5 mm at 0.5 mm spacing == 3 points
        self.assertEqual(self.p._pathIndexAtOffset(0, 1.5), 3)
        self.assertEqual(self.p._pathIndexAtOffset(10, 1.5), 13)

    def test_rounds_to_closer_neighbour(self):
        self.assertEqual(self.p._pathIndexAtOffset(0, 0.6), 1)   # 0.5 beats 1.0
        self.assertEqual(self.p._pathIndexAtOffset(0, 0.9), 2)   # 1.0 beats 0.5

    def test_negative_offset_moves_proximally(self):
        self.assertEqual(self.p._pathIndexAtOffset(20, -1.5), 17)

    def test_clamps_at_both_ends(self):
        self.assertEqual(self.p._pathIndexAtOffset(0, -50.0), 0)
        self.assertEqual(self.p._pathIndexAtOffset(200, 50.0), 200)

    def test_sub_spacing_offset_does_not_move(self):
        # 0.1 mm is well under the 0.5 mm point spacing
        self.assertEqual(self.p._pathIndexAtOffset(10, 0.1), 10)


# ── wheel accumulation ───────────────────────────────────────────────────────

class TestQueueScroll(unittest.TestCase):

    def setUp(self):
        self.p = _straight_line_picker()

    def test_one_notch_is_one_step_mm(self):
        self.p._queueScroll(120)
        self.assertAlmostEqual(self.p._pendingScrollMm, SCROLL_STEP_MM)

    def test_negative_notch(self):
        self.p._queueScroll(-120)
        self.assertAlmostEqual(self.p._pendingScrollMm, -SCROLL_STEP_MM)

    def test_small_deltas_accumulate(self):
        for _ in range(10):
            self.p._queueScroll(12)          # a tenth of a notch each
        self.assertAlmostEqual(self.p._pendingScrollMm, SCROLL_STEP_MM)

    def test_zero_delta_ignored(self):
        self.p._queueScroll(0)
        self.assertEqual(self.p._pendingScrollMm, 0.0)
        self.assertFalse(self.p._scrollTimer.isActive())

    def test_burst_schedules_a_single_apply(self):
        """A 10-notch flick must arm the coalesce timer exactly once."""
        for _ in range(10):
            self.p._queueScroll(120)
        self.assertEqual(self.p._scrollTimer.startCount, 1)
        self.assertAlmostEqual(self.p._pendingScrollMm, 10 * SCROLL_STEP_MM)


# ── coalesced apply ──────────────────────────────────────────────────────────

class TestApplyPendingScroll(unittest.TestCase):

    def setUp(self):
        self.p = _straight_line_picker(spacing=0.5, n=201)

    def test_burst_moves_once_to_the_final_position(self):
        for _ in range(10):
            self.p._queueScroll(120)
        self.p._scrollTimer.fire()

        # 10 notches * 1.0 mm = 10 mm = 20 points at 0.5 mm spacing
        self.assertEqual(self.p._currentPathIndex, 20)
        self.assertEqual(self.p.currentPreviewId, 20)
        self.assertEqual(self.p._pendingScrollMm, 0.0)
        # the marker was moved in place, not removed and re-added
        self.p.previewNode.SetNthControlPointPosition.assert_called_once()
        self.p.previewNode.RemoveAllControlPoints.assert_not_called()

    def test_settle_timer_armed_after_a_move(self):
        self.p._queueScroll(120)
        self.p._scrollTimer.fire()
        self.assertTrue(self.p._settleTimer.isActive())

    def test_sub_point_residual_is_retained(self):
        """Tiny trackpad increments must accumulate, not be discarded."""
        self.p._queueScroll(4)               # 0.05 mm — far under 0.5 mm spacing
        self.p._scrollTimer.fire()
        self.assertEqual(self.p._currentPathIndex, 0)
        self.assertGreater(self.p._pendingScrollMm, 0.0)
        self.assertFalse(self.p._settleTimer.isActive())

        # keep nudging until it crosses a point boundary
        for _ in range(40):
            self.p._queueScroll(4)
            self.p._scrollTimer.fire()
        self.assertGreater(self.p._currentPathIndex, 0)

    def test_clamped_at_distal_end(self):
        self.p._currentPathIndex = 200
        self.p.currentPreviewId = 200
        self.p._queueScroll(120)
        self.p._scrollTimer.fire()
        self.assertEqual(self.p._currentPathIndex, 200)

    def test_reentrancy_guard(self):
        self.p._pendingScrollMm = 10.0
        self.p._isScrolling = True
        self.p._applyPendingScroll()
        self.assertEqual(self.p._currentPathIndex, 0)      # nothing happened
        self.assertEqual(self.p._pendingScrollMm, 10.0)    # delta preserved

    def test_no_preview_clears_pending(self):
        self.p.currentPreviewPos = None
        self.p._pendingScrollMm = 5.0
        self.p._applyPendingScroll()
        self.assertEqual(self.p._pendingScrollMm, 0.0)

    def test_snap_handler_suppressed_during_scroll(self):
        """The programmatic marker move must not trigger snap-back."""
        self.p._queueScroll(120)
        self.p._scrollTimer.fire()
        self.assertFalse(self.p._isSnapping)      # flag released again
        self.assertFalse(self.p._snapTimer.isActive())

    def test_scroll_fast_path_defers_heavy_work(self):
        """The disk, Red/Green jump and UI callback belong to the settle pass."""
        self.p._createOrUpdatePreviewPlane = MagicMock()
        self.p._centerSliceViewsOnPoint = MagicMock()
        self.p.updateCallback = MagicMock()

        self.p._queueScroll(120)
        self.p._scrollTimer.fire()

        self.p._createOrUpdatePreviewPlane.assert_not_called()
        self.p._centerSliceViewsOnPoint.assert_not_called()
        self.p.updateCallback.assert_not_called()

        self.p._settleTimer.fire()

        self.p._createOrUpdatePreviewPlane.assert_called_once()
        self.p._centerSliceViewsOnPoint.assert_called_once()
        self.p.updateCallback.assert_called_once()


# ── wheel filter delegation ──────────────────────────────────────────────────

class TestWheelFilter(unittest.TestCase):

    def _wheel_event(self, dy):
        ev = MagicMock()
        ev.type.return_value = _qt.QEvent.Wheel
        ev.angleDelta.return_value = types.SimpleNamespace(y=lambda: dy)
        return ev

    def test_wheel_is_queued_and_consumed(self):
        picker = MagicMock()
        f = _YellowWheelFilter(picker)
        self.assertTrue(f.eventFilter(None, self._wheel_event(120)))
        picker._queueScroll.assert_called_once_with(120)

    def test_scroll_direction_follows_wheel_sign(self):
        picker = MagicMock()
        f = _YellowWheelFilter(picker)
        f.eventFilter(None, self._wheel_event(-120))
        picker._queueScroll.assert_called_once_with(-120)

    def test_non_wheel_events_pass_through(self):
        picker = MagicMock()
        f = _YellowWheelFilter(picker)
        ev = MagicMock()
        ev.type.return_value = "KeyPress"
        self.assertFalse(f.eventFilter(None, ev))
        picker._queueScroll.assert_not_called()


if __name__ == "__main__":
    unittest.main()
