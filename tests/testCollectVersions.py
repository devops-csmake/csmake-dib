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
"""DIBCsversionCollectDpkgs / DIBCsversionCollectPips with ``subprocess`` mocked.

Popen().communicate() returns bytes on Python 3 unless universal_newlines is
set; the parsers then ``split('\\n')`` and match str regexes.  The fake Popen
returns bytes unless text mode was requested, so dropping universal_newlines
makes these tests fail just as it breaks the real code.
"""
import unittest

from tests import dibsupport
from tests.dibsupport import FakeSubprocess, make_module

dpkgmod = dibsupport.import_dib('DIBCsversionCollectDpkgs')
pipmod = dibsupport.import_dib('DIBCsversionCollectPips')

MOUNT = '/build/mnt'
DPKG_QUERY = ['sudo', '-E', 'chroot', MOUNT, 'dpkg-query', '--show',
              '-f', '${binary:Package} (${Version})\\n']
FIND_VENVS = 'sudo -E chroot %s find . | grep "bin/activate_this"' % MOUNT
PIP_LIST = ['sudo', '-E', 'chroot', MOUNT, 'pip', 'list']
DELIM = '=-=-=-=-=-=-=-=-='


class DpkgTestCase(unittest.TestCase):

    def setUp(self):
        self.sub = FakeSubprocess()
        self.sub.install(self)
        env = {'__DIBEnv__': {'shellenv': {'TMP_MOUNT_PATH': MOUNT}}}
        self.collect = make_module(dpkgmod.DIBCsversionCollectDpkgs, env=env)

    def _build(self, output, tag='img', **rule):
        self.sub.on(DPKG_QUERY, stdout=output, **rule)
        return self.collect.build({'tag': tag})


class CollectDpkgsTests(DpkgTestCase):

    def test_build_runs_dpkg_query_in_the_chroot_and_reads_it_as_text(self):
        self._build('bash (5.0-6)\n')

        self.assertEqual(self.sub.commands(), [DPKG_QUERY])
        invocation = self.sub.invocations[0]
        self.assertEqual(invocation.func, 'Popen')
        self.assertTrue(invocation.kwargs['universal_newlines'])

    def test_build_records_package_and_version(self):
        versdict = self._build('bash (5.0-6ubuntu1)\npython3-minimal (3.8.2-0ubuntu2)\n')

        self.assertEqual(versdict['bash'], {'img': ('dpkg', {
            'PACKAGE': 'bash', 'VERSION': '5.0-6ubuntu1', 'ARCH': ''})})
        self.assertEqual(sorted(versdict), ['bash', 'python3-minimal'])
        self.assertEqual(self.collect.log.result, 'passed')

    def test_build_tags_multiarch_packages_per_architecture(self):
        versdict = self._build('libc6:amd64 (2.31-0)\nlibc6:i386 (2.31-0)\n')

        self.assertEqual(sorted(versdict['libc6']), ['img_amd64', 'img_i386'])
        self.assertEqual(versdict['libc6']['img_i386'], ('dpkg', {
            'PACKAGE': 'libc6', 'VERSION': '2.31-0', 'ARCH': 'i386'}))

    def test_build_skips_blank_and_unrecognised_lines(self):
        versdict = self._build('\nnot a package line\n   \nbash (5.0)\n')

        self.assertEqual(sorted(versdict), ['bash'])
        self.assertTrue(self.collect.log.mentions('debug', "Didn't match: not a package line"))

    def test_build_with_no_output_records_nothing(self):
        versdict = self._build('', rc=1, stderr='chroot: failed to run command\n')

        self.assertEqual(versdict, {})
        self.assertEqual(self.collect.log.result, 'passed')

    def test_build_warns_when_a_package_is_listed_twice(self):
        versdict = self._build('bash (5.0)\nbash (5.1)\n')

        self.assertEqual(versdict['bash']['img'][1]['VERSION'], '5.1')
        self.assertTrue(self.collect.log.mentions('warning', 'Package: bash, Tag: img :: Overwriting'))

    def test_build_decodes_non_ascii_output(self):
        versdict = self._build(u'café-tools (1.0-☃)\n')

        self.assertEqual(versdict[u'café-tools']['img'][1]['VERSION'], u'1.0-☃')

    def test_build_adds_to_an_existing_version_record(self):
        self.collect.env.env['__Csversion__'] = {'sources': {'other': {'t': ('git', {})}}}

        versdict = self._build('bash (5.0)\n')

        self.assertEqual(sorted(versdict), ['bash', 'other'])
        self.assertIs(versdict, self.collect.env.env['__Csversion__']['sources'])


class PipTestCase(unittest.TestCase):

    def setUp(self):
        self.sub = FakeSubprocess()
        self.sub.install(self)
        env = {'__DIBEnv__': {'shellenv': {'TMP_MOUNT_PATH': MOUNT}}}
        self.collect = make_module(pipmod.DIBCsversionCollectPips, env=env)

    def _venv_command(self, venv):
        marker = 'source %s/bin/activate' % venv
        return lambda args: isinstance(args, str) and marker in args


class CollectPipsTests(PipTestCase):

    def _globals_only(self, pip_output):
        self.sub.on(FIND_VENVS, rc=1, stderr='')
        self.sub.on(PIP_LIST, stdout=pip_output)
        return self.collect.build({'tag': 'img'})

    def test_build_searches_for_virtualenvs_then_lists_global_pips_as_text(self):
        self._globals_only('requests (2.22.0)\n')

        self.assertEqual(self.sub.commands(), [FIND_VENVS, PIP_LIST])
        for invocation in self.sub.invocations:
            self.assertEqual(invocation.func, 'Popen')
            self.assertTrue(invocation.kwargs['universal_newlines'])
        self.assertTrue(self.sub.invocations[0].kwargs['shell'])

    def test_build_records_global_pips_under_the_global_tag(self):
        versdict = self._globals_only('requests (2.22.0)\nsetuptools (45.2.0)\n\n')

        self.assertEqual(versdict['setuptools'], {'img___global': ('pip', {
            'VENV': '__global', 'PACKAGE': 'setuptools', 'VERSION': '45.2.0'})})
        self.assertEqual(self.collect.log.result, 'passed')

    def test_build_logs_that_no_virtualenv_was_found(self):
        self._globals_only('requests (2.22.0)\n')

        self.assertTrue(self.collect.log.mentions('info', 'Search for virtual environments failed'))

    def test_build_reports_stderr_text_when_the_virtualenv_search_fails(self):
        self.sub.on(FIND_VENVS, rc=1, stderr=u'find: café\n')
        self.sub.on(PIP_LIST, stdout='')

        self.collect.build({'tag': 'img'})

        self.assertEqual(self.collect.log.messages('debug'), [u'find: café\n'])

    def test_build_skips_unrecognised_pip_lines(self):
        versdict = self._globals_only('WARNING: pip is old\nrequests (2.22.0)\n')

        self.assertEqual(sorted(versdict), ['requests'])
        self.assertTrue(self.collect.log.mentions('info', 'WARNING: pip is old was not recognized'))

    def test_build_warns_when_a_pip_is_listed_twice(self):
        self._globals_only('requests (1.0)\nrequests (2.0)\n')

        self.assertTrue(self.collect.log.mentions('warning', 'Package: requests, Tag: img___global :: Overwriting'))


class CollectPipsVirtualenvTests(PipTestCase):

    def _with_venvs(self, find_output, venv_outputs):
        self.sub.on(FIND_VENVS, stdout=find_output)
        self.sub.on(PIP_LIST, stdout='requests (2.22.0)\n')
        for venv, output in venv_outputs.items():
            if isinstance(output, int):
                self.sub.on(self._venv_command(venv), rc=output, stderr='activate: not found\n')
            else:
                self.sub.on(self._venv_command(venv), stdout=output)
        return self.collect.build({'tag': 'img'})

    def test_build_derives_virtualenv_roots_from_activate_this_paths(self):
        self._with_venvs(
            './opt/venv1/bin/activate_this.py\n./srv/app/env/bin/activate_this.py\n',
            {'/opt/venv1': DELIM + '\n', '/srv/app/env': DELIM + '\n'})

        sources = [c for c in self.sub.commands() if isinstance(c, str) and 'bin/activate;' in c]
        self.assertEqual(len(sources), 2)
        self.assertIn('source /opt/venv1/bin/activate', sources[0])
        self.assertIn('source /srv/app/env/bin/activate', sources[1])
        self.assertIn('chroot %s /bin/bash -c' % MOUNT, sources[0])

    def test_build_reads_virtualenv_listings_as_text_after_the_delimiter(self):
        versdict = self._with_venvs(
            './opt/venv1/bin/activate_this.py\n',
            {'/opt/venv1': 'noise before delimiter\n' + DELIM + '\nflask (1.1.2)\nrequests (2.25.0)\n'})

        self.assertEqual(sorted(versdict['requests']), ['img_/opt/venv1', 'img___global'])
        self.assertEqual(versdict['flask'], {'img_/opt/venv1': ('pip', {
            'VENV': '/opt/venv1', 'PACKAGE': 'flask', 'VERSION': '1.1.2'})})
        self.assertEqual(versdict['requests']['img_/opt/venv1'][1]['VERSION'], '2.25.0')
        self.assertNotIn('noise', versdict)
        venv_invocation = self.sub.invocations[2]
        self.assertTrue(venv_invocation.kwargs['universal_newlines'])
        self.assertTrue(venv_invocation.kwargs['shell'])

    def test_build_skips_a_virtualenv_that_cannot_be_queried(self):
        versdict = self._with_venvs(
            './opt/venv1/bin/activate_this.py\n./opt/venv2/bin/activate_this.py\n',
            {'/opt/venv1': 1, '/opt/venv2': DELIM + '\nflask (1.1.2)\n'})

        self.assertTrue(self.collect.log.mentions('info', 'Could not get information on /opt/venv1'))
        self.assertEqual(sorted(versdict['flask']), ['img_/opt/venv2'])
        self.assertEqual(self.collect.log.result, 'passed')

    def test_build_ignores_blank_lines_in_the_search_output(self):
        self._with_venvs('\n./opt/venv1/bin/activate_this.py\n\n', {'/opt/venv1': DELIM + '\n'})

        self.assertEqual(
            len([c for c in self.sub.commands() if isinstance(c, str) and 'bin/activate;' in c]), 1)


if __name__ == '__main__':
    unittest.main()
