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
"""The shell environment is carried between csmake runs in the DIB progress
log: DIBRunParts pickles it, DIBInit unpickles it on the next run.

On Python 3 pickle.dumps() returns bytes while configparser only stores str,
so the pickle is base64 encoded in between.  These tests drive the real
write side (DIBRunParts.build) and the real read side (DIBInit._setupLogConfig)
against a real progress log file on disk.
"""
import os
import unittest

from tests import dibsupport

runpartsmod = dibsupport.import_dib('DIBRunParts')
initmod = dibsupport.import_dib('DIBInit')

SHELL_ENV = {
    'HOME': '/root',
    'PATH': '/usr/local/bin:/usr/bin',
    'TMP_HOOKS_PATH': '/tmp/hooks',
    'DIB_UNICODE': u'caf\u00e9 \u2603',
    'DIB_PERCENT': '100% of 50%% done',
    'DIB_MULTILINE': 'line one\nline two\n',
    'DIB_EMPTY': '',
}


class ProgressLogEnvironmentTests(unittest.TestCase):

    def setUp(self):
        self.builddir = dibsupport.make_tmpdir(self)
        self.init = dibsupport.make_module(initmod.DIBInit)

    def _run_parts_and_save(self, logfile, shellenv):
        """Run a (script-less) DIBRunParts so it persists shellenv."""
        dibenv = {'logfile': logfile, 'build-dir': self.builddir,
                  'hooksdir': os.path.join(self.builddir, 'hooks')}
        self.init._setupLogConfig(dibenv)
        dibenv['shellenv'] = dict(shellenv)
        runparts = dibsupport.make_module(
            runpartsmod.DIBRunParts, env={'__DIBEnv__': dibenv})
        self.assertTrue(runparts.build({'scripts': 'install.d'}))
        self.assertEqual(runparts.log.result, 'passed')
        return dibenv

    def test_shellenv_survives_runparts_write_then_init_read(self):
        logfile = os.path.join(self.builddir, 'image.progresslog')
        written = self._run_parts_and_save(logfile, SHELL_ENV)

        stored = written['logconfig'].get('environment', 'pickle')
        self.assertIs(type(stored), str)
        stored.encode('ascii')  # plain text, safe for an ini-style value
        with open(logfile) as f:
            self.assertIn('[environment]', f.read())

        reader = {'logfile': logfile, 'build-dir': self.builddir}
        self.init._setupLogConfig(reader)
        self.assertEqual(self.init.log.messages('exception'), [])
        self.assertEqual(reader['shellenv'], SHELL_ENV)

    def test_start_image_environment_seeds_the_next_build(self):
        startlog = os.path.join(self.builddir, 'base.img.progresslog')
        startenv = dict(SHELL_ENV)
        startenv.update({
            'IMAGE_ELEMENT': 'ubuntu vm',
            'ELEMENT_PATH': '/elements/a:/elements/b',
            'ARCH': 'amd64',
            'DIB_RELEASE': 'xenial',
            'DIB_ROOT_LABEL': 'cloudimg-rootfs',
        })
        self._run_parts_and_save(startlog, startenv)

        reader = {'build-dir': self.builddir,
                  'start-logfilename': 'base.img.progresslog',
                  'logfile': os.path.join(self.builddir, 'next.progresslog')}
        self.init._setupLogConfig(reader)

        self.assertEqual(self.init.log.messages('exception'), [])
        self.assertEqual(reader['startenv'], startenv)
        self.assertEqual(reader['start-elements'], ['ubuntu', 'vm'])
        self.assertEqual(
            reader['start-elementpath'], ['/elements/a', '/elements/b'])
        self.assertEqual(reader['arch'], 'amd64')
        self.assertEqual(reader['release'], 'xenial')
        self.assertEqual(reader['root-label'], 'cloudimg-rootfs')
        self.assertEqual(reader['shellenv'], startenv)


if __name__ == '__main__':
    unittest.main()
