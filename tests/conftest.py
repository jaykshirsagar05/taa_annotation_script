"""
conftest.py — shared Slicer/Qt stubs for the whole test session.

Project code under TAAAnnotationLib/ is designed to run inside 3D Slicer's
embedded Python, so most modules import slicer/qt/vtk at module scope. These
tests run outside Slicer, so those modules are stubbed — once, here, for the
whole session, before any test module imports project code.

``qt.QWidget`` and ``qt.QObject`` are real (empty) classes rather than plain
MagicMock attributes: several project classes subclass them directly (e.g.
``CaseBrowserWidget(qt.QWidget)``), and subclassing a MagicMock instance
silently produces a MagicMock *instance* instead of a real class — a PEP 560
``__mro_entries__`` pitfall — which breaks any test that touches attributes
or methods defined on the subclass, without raising an import error.
"""

import os
import sys
from unittest.mock import MagicMock


class _StubQtBase:
    """Stand-in for qt.QWidget / qt.QObject so project classes can subclass them."""

    def __init__(self, *args, **kwargs):
        pass


def _install_stub(name, **attrs):
    module = sys.modules.get(name)
    if module is None:
        module = MagicMock()
        sys.modules[name] = module
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


_install_stub('qt', QWidget=_StubQtBase, QObject=_StubQtBase)

for _mod in ['slicer', 'slicer.util', 'vtk', 'vtk.util',
             'SimpleITK', 'sitkUtils', 'numpy']:
    sys.modules.setdefault(_mod, MagicMock())

_TAAA_DIR = os.path.join(os.path.dirname(__file__), '..', 'TAAAnnotation')
if _TAAA_DIR not in sys.path:
    sys.path.insert(0, _TAAA_DIR)
