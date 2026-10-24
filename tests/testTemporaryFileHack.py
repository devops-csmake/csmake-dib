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
"""DIBTemporaryFileHack: file-mode save/restore, text I/O and patching.

Python-3 conversion risks covered: oct() now carries a two character '0o'
prefix (the old ``[1:]`` slice produced the unusable mode 'o640'), file() was
replaced by open(), and ``patch`` is fed text through a pipe.  The module's
``sudo chmod`` calls run the real chmod via a fake ``sudo`` on PATH.
"""
import os
import shutil
import unittest

from tests import dibsupport
from tests.dibsupport import file_mode

hackmod = dibsupport.import_dib('DIBTemporaryFileHack')


class TemporaryFileHackTests(unittest.TestCase):

    def setUp(self):
        dibsupport.install_fake_tools(self, {})
        self.root = dibsupport.make_tmpdir(self)
        self.hack = dibsupport.make_module(hackmod.DIBTemporaryFileHack)

    def _start(self, **options):
        return self.hack.script_start__build('build', options, None, None)

    def _end(self, **options):
        return self.hack.script_end__build('build', options, None, None)

    def test_modes_are_saved_as_plain_octal_and_restored(self):
        for original in (0o640, 0o604, 0o007):
            with self.subTest(mode=oct(original)):
                workdir = os.path.join(self.root, 'm%o' % original)
                os.mkdir(workdir)
                os.chmod(workdir, 0o750)
                target = os.path.join(workdir, 'conf')
                with open(target, 'w') as f:
                    f.write('original\n')
                os.chmod(target, original)

                self.assertTrue(self._start(file=target, append='added\n'))
                # chmod expects bare octal digits, not python's '0o' form
                self.assertEqual(self.hack.realFileMode, '%o' % original)
                self.assertEqual(self.hack.realDirMode, '750')
                self.assertEqual(file_mode(target), 0o777)
                self.assertEqual(file_mode(workdir), 0o777)
                with open(target) as f:
                    self.assertEqual(f.read(), 'original\nadded\n')

                self.assertTrue(self._end())
                self.assertEqual(file_mode(target), original)
                self.assertEqual(file_mode(workdir), 0o750)
                os.chmod(target, 0o600)  # 0o007 is unreadable by its owner
                with open(target) as f:
                    self.assertEqual(f.read(), 'original\n')
                self.assertFalse(os.path.exists(self.hack.tempFile))
                self.assertEqual(self.hack.log.messages('error'), [])

    def test_new_file_in_new_directory_is_removed_again(self):
        base = os.path.join(self.root, 'base')
        os.mkdir(base)
        os.chmod(base, 0o755)
        with open(os.path.join(base, 'sentinel'), 'w') as f:
            f.write('keeps base from being pruned\n')
        target = os.path.join(base, 'newdir', 'fresh.conf')

        self.assertTrue(self._start(file=target, replace='fresh\n'))
        with open(target) as f:
            self.assertEqual(f.read(), 'fresh\n')
        self.assertEqual(self.hack.realDirMode, '755')

        self.assertTrue(self._end())
        self.assertFalse(os.path.exists(os.path.join(base, 'newdir')))
        self.assertEqual(file_mode(base), 0o755)
        self.assertEqual(self.hack.log.messages('error'), [])

    @unittest.skipUnless(shutil.which('patch'), "patch(1) not installed")
    def test_replace_patch_append_run_in_documented_order(self):
        workdir = os.path.join(self.root, 'etc')
        os.mkdir(workdir)
        target = os.path.join(workdir, 'app.conf')
        with open(target, 'w') as f:
            f.write('will be replaced\n')
        extra = os.path.join(self.root, 'extra.txt')
        with open(extra, 'w') as f:
            f.write('from file\n')
        # csmakefile values are indented; line-pad marks where text starts
        replace = '\n|alpha\n|beta\n|gamma\n'
        patch = ('\n|--- a/app.conf\n|+++ b/app.conf\n|@@ -2,3 +2,3 @@\n'
                 '| alpha\n|-beta\n|+BETA\n| gamma\n')

        self.assertTrue(self._start(**{
            'file': target, 'line-pad': '|',
            'append-file': extra, 'append': 'inline\n',
            'patch': patch, 'replace': replace}))

        with open(target) as f:
            self.assertEqual(
                f.read(), '\nalpha\nBETA\ngamma\ninline\nfrom file\n')
        self.assertEqual(self.hack.log.messages('error'), [])

        self.assertTrue(self._end())
        with open(target) as f:
            self.assertEqual(f.read(), 'will be replaced\n')


if __name__ == '__main__':
    unittest.main()
