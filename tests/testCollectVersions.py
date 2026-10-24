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
"""DIBCsversionCollectDpkgs / DIBCsversionCollectPips: parsing tool output.

Popen().communicate() returns bytes on Python 3 unless universal_newlines is
set; the parsers then ``split('\\n')`` and match str regexes.  ``sudo`` runs
its arguments, ``chroot`` just enters the directory, and ``dpkg-query`` /
``pip`` print canned listings like the real ones inside an image.
"""
import os
import unittest

from tests import dibsupport
from tests.dibsupport import calls

dpkgmod = dibsupport.import_dib('DIBCsversionCollectDpkgs')
pipmod = dibsupport.import_dib('DIBCsversionCollectPips')

TOOLS = {
    'chroot': dibsupport.CHROOT,
    'dpkg-query': ('printf "bash (5.0-6ubuntu1)\\nlibc6:amd64 (2.31-0ubuntu9)\\n'
                   'libc6:i386 (2.31-0ubuntu9)\\n\\nnot a package line\\n"\n'),
    'pip': 'printf "requests (2.22.0)\\nsetuptools (45.2.0)\\n\\n"\n',
    'deactivate': ':\n',  # a shell function in real virtualenvs
}


class CollectVersionTests(unittest.TestCase):

    def setUp(self):
        _, self.logpath = dibsupport.install_fake_tools(self, TOOLS)
        self.mountpath = dibsupport.make_tmpdir(self)

    def _module(self, cls):
        dibenv = {'shellenv': {'TMP_MOUNT_PATH': self.mountpath}}
        return dibsupport.make_module(
            cls, env={'__DIBEnv__': dibenv})

    def test_dpkg_listing_is_parsed_into_package_arch_and_version(self):
        collect = self._module(dpkgmod.DIBCsversionCollectDpkgs)

        versdict = collect.build({'tag': 'img'})

        self.assertEqual(collect.log.result, 'passed')
        self.assertIs(versdict, collect.env.env['__Csversion__']['sources'])
        self.assertEqual(versdict['bash'], {'img': ('dpkg', {
            'PACKAGE': 'bash', 'VERSION': '5.0-6ubuntu1', 'ARCH': ''})})
        # a multi-arch package is tagged per architecture
        self.assertEqual(sorted(versdict['libc6']), ['img_amd64', 'img_i386'])
        self.assertEqual(versdict['libc6']['img_i386'], ('dpkg', {
            'PACKAGE': 'libc6', 'VERSION': '2.31-0ubuntu9', 'ARCH': 'i386'}))
        self.assertEqual(sorted(versdict), ['bash', 'libc6'])
        self.assertEqual(calls(self.logpath, 'dpkg-query')[0][1][:2],
                         ['--show', '-f'])

    def test_pip_listing_covers_global_and_discovered_virtualenvs(self):
        venvbin = os.path.join(self.mountpath, 'opt', 'venv1', 'bin')
        os.makedirs(venvbin)
        open(os.path.join(venvbin, 'activate_this.py'), 'w').close()
        collect = self._module(pipmod.DIBCsversionCollectPips)

        versdict = collect.build({'tag': 'img'})

        self.assertEqual(collect.log.result, 'passed')
        self.assertEqual(sorted(versdict), ['requests', 'setuptools'])
        # tags are <tag>_<virtualenv>; the 'find | grep' output located venv1
        self.assertEqual(sorted(versdict['requests']),
                         ['img_/opt/venv1', 'img___global'])
        self.assertEqual(versdict['setuptools']['img___global'], ('pip', {
            'VENV': '__global', 'PACKAGE': 'setuptools', 'VERSION': '45.2.0'}))
        self.assertEqual(versdict['requests']['img_/opt/venv1'][1]['VENV'],
                         '/opt/venv1')


if __name__ == '__main__':
    unittest.main()
