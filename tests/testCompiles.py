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
"""Every python file in the repository must be valid Python 3 source.

Catches Python 2 syntax (print statements, 0777 literals, ``except E, e``,
backticks) and mixed tab/space indentation creeping back in -- including in
modules whose imports are unavailable here and so are never loaded by the
behavioural tests.
"""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = ('.git', '__pycache__')


def python_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith('.py'):
                yield os.path.join(dirpath, name)


class CompilesTests(unittest.TestCase):

    def test_every_python_file_compiles(self):
        checked = 0
        for path in python_files():
            with self.subTest(path=os.path.relpath(path, ROOT)):
                with open(path, 'rb') as source:
                    compile(source.read(), path, 'exec', dont_inherit=True)
                checked += 1
        self.assertGreater(checked, 0)


if __name__ == '__main__':
    unittest.main()
