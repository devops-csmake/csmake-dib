# <copyright>
# (c) Copyright 2026 Autumn Patterson
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
# Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
# </copyright>
"""Test-only shim: let plugin modules written as ``from Csmake.X import Y``
import against an installed csmake that only ships ``CsmakeCore``.

Production imports are deliberately untouched (known, out-of-scope
ecosystem issue); call ``install()`` before importing repo modules.
"""
import importlib
import importlib.abc
import importlib.util
import os
import sys
import unittest


class _CsmakeAliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Resolve ``Csmake.<sub>`` to the very same module as ``CsmakeCore.<sub>``."""

    def find_spec(self, fullname, path, target=None):
        if fullname.startswith('Csmake.'):
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        real = importlib.import_module('CsmakeCore.' + spec.name[len('Csmake.'):])
        spec.loader_state = real.__spec__  # import machinery overwrites __spec__
        return real

    def exec_module(self, module):
        module.__spec__ = module.__spec__.loader_state  # restore the real spec


def install():
    try:
        import Csmake  # noqa: F401  (a real Csmake package is available)
        return
    except ImportError:
        pass
    try:
        import CsmakeCore
    except ImportError:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sibling = os.path.join(os.path.dirname(here), 'csmake')
        if not os.path.isdir(os.path.join(sibling, 'CsmakeCore')):
            raise unittest.SkipTest("csmake core not found")
        sys.path.append(sibling)
        import CsmakeCore
    sys.modules['Csmake'] = CsmakeCore
    sys.meta_path.insert(0, _CsmakeAliasFinder())
