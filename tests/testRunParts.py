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
"""DIBRunParts script execution with csmake core's ShellPassEnv mocked.

``ShellPassEnv.call`` is the boundary that actually runs a hook script under a
shell and reports (exit status, resulting environment); it is replaced by a
scripted fake that records exactly how it was called.  The ``in-image`` mode's
sudo mount/chroot/umount commands go through a type-faithful fake subprocess.
The scripts themselves are empty files in a temp hooks directory: only their
names and executable bits matter.
"""
import base64
import os
import pickle
import unittest
from configparser import RawConfigParser

from tests import dibsupport
from tests.dibsupport import FakeFlowControl, FakeSubprocess, make_module
from unittest import mock

runpartsmod = dibsupport.import_dib('DIBRunParts')


class FakeShellPassEnv(object):
    """Stand-in for the ShellPassEnv class: the module instantiates it, so
    the instance is shared and records every call()."""

    def __init__(self):
        self.results = {}   # script basename -> (rc, env), an Exception or a list of those
        self.default = (0, {})
        self.calls = []

    def __call__(self):
        return self

    def call(self, command, stdout, stderr, env, shell=True):
        script = os.path.basename(command[-1])
        self.calls.append({'command': list(command), 'env': dict(env),
                           'env_object': env, 'shell': shell,
                           'stdout': stdout, 'stderr': stderr})
        result = self.results.get(script, self.default)
        if isinstance(result, list):  # successive outcomes
            result = result.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def commands(self):
        return [call['command'] for call in self.calls]


class RunPartsTestCase(unittest.TestCase):

    def setUp(self):
        self.root = dibsupport.make_tmpdir(self)
        self.hooks = os.path.join(self.root, 'hooks')
        self.partsdir = os.path.join(self.hooks, 'install.d')
        os.makedirs(self.partsdir)
        self.mount = os.path.join(self.root, 'mnt')
        os.mkdir(self.mount)
        self.shell = FakeShellPassEnv()
        patcher = mock.patch.object(runpartsmod, 'ShellPassEnv', self.shell)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sub = FakeSubprocess(default_rc=0).install(self)

        config = RawConfigParser()
        config.optionxform = str
        config.add_section('environment')
        self.shellenv = {'HOME': '/home/builder', 'PATH': '/usr/bin:/bin',
                         'TMP_HOOKS_PATH': self.hooks,
                         'TMP_MOUNT_PATH': self.mount}
        self.logfile = os.path.join(self.root, 'image.progresslog')
        self.dibenv = {'hooksdir': self.hooks, 'logfile': self.logfile,
                       'logconfig': config, 'shellenv': self.shellenv}
        self.parts = make_module(
            runpartsmod.DIBRunParts, env={'__DIBEnv__': self.dibenv})

    def script(self, name, executable=True, directory=False):
        path = os.path.join(self.partsdir, name)
        if directory:
            os.mkdir(path)
        else:
            open(path, 'w').close()
            os.chmod(path, 0o755 if executable else 0o644)
        return path

    def build(self, **options):
        options.setdefault('scripts', 'install.d')
        return self.parts.build(options)

    def advise(self, **advice):
        self.parts.flowcontrol = FakeFlowControl(advice)

    def ran(self):
        return [os.path.basename(c[-1]) for c in self.shell.commands()]

    def joinpoints(self):
        return self.parts.engine.joinpoints()

    def stored_environment(self):
        stored = self.dibenv['logconfig'].get('environment', 'pickle')
        return pickle.loads(base64.b64decode(stored))


class ScriptDiscoveryTests(RunPartsTestCase):

    def test_build_runs_executable_scripts_in_numeric_order(self):
        for name in ('100-last', '2-first', '10-middle'):
            self.script(name)

        self.assertTrue(self.build())

        self.assertEqual(self.ran(), ['2-first', '10-middle', '100-last'])

    def test_build_orders_unnumbered_scripts_after_numbered_ones(self):
        for name in ('10-numbered', 'extra-hook', '05-early', 'another'):
            self.script(name)

        self.assertTrue(self.build())

        self.assertEqual(self.ran(), ['05-early', '10-numbered', 'another', 'extra-hook'])

    def test_build_skips_non_executable_files_and_directories(self):
        self.script('10-run')
        self.script('20-not-executable', executable=False)
        self.script('30-a-directory', directory=True)

        self.build()

        self.assertEqual(self.ran(), ['10-run'])

    def test_build_warns_about_executables_the_filter_rejects(self):
        self.script('10-run')
        self.script('bad name.sh')

        self.build()

        self.assertEqual(self.ran(), ['10-run'])
        self.assertTrue(self.parts.log.mentions('warning', 'Skipping executable file: bad name.sh'))

    def test_build_custom_filter_without_a_group_sorts_lexically(self):
        for name in ('beta', 'alpha', '10-numbered'):
            self.script(name)

        self.build(filter=' ^[a-z]+$ ')

        self.assertEqual(self.ran(), ['alpha', 'beta'])

    def test_build_source_mode_includes_non_executable_files_with_source_prefix(self):
        path = self.script('10-env', executable=False)

        self.build(source='True')

        self.assertEqual(self.shell.commands(), [['/bin/bash', '-xc', 'source %s' % path]])

    def test_build_with_no_scripts_directory_passes_without_running_anything(self):
        self.build(scripts='missing.d')

        self.assertEqual(self.shell.calls, [])
        self.assertEqual(self.parts.log.result, 'passed')
        self.assertTrue(self.parts.log.mentions('info', 'There are no scripts for section: missing.d'))


class ScriptExecutionTests(RunPartsTestCase):

    def test_build_runs_each_script_under_the_default_shell(self):
        a = self.script('10-a')
        b = self.script('20-b')

        self.assertTrue(self.build())

        self.assertEqual(self.shell.commands(), [
            ['/bin/bash', '-xc', a], ['/bin/bash', '-xc', b]])
        first = self.shell.calls[0]
        self.assertFalse(first['shell'])
        self.assertIs(first['env_object'], self.shellenv)
        self.assertIs(first['stdout'], self.parts.log.out() )
        self.assertEqual(self.parts.log.result, 'passed')

    def test_build_uses_the_requested_shell(self):
        path = self.script('10-a')
        self.parts.env.substitutions = {'%(SHELL)s': '/usr/bin/zsh'}

        self.build(shell='%(SHELL)s')

        self.assertEqual(self.shell.commands(), [['/usr/bin/zsh', '-xc', path]])

    def test_build_merges_the_environment_each_script_returns(self):
        self.script('10-a')
        self.script('20-b')
        self.shell.results['10-a'] = (0, {'FROM_A': '1', 'HOME': '/changed'})

        self.build()

        self.assertNotIn('FROM_A', self.shell.calls[0]['env'])
        self.assertEqual(self.shell.calls[1]['env']['FROM_A'], '1')
        self.assertEqual(self.shell.calls[1]['env']['HOME'], '/changed')

    def test_build_fires_the_aspect_joinpoints_around_each_script(self):
        self.script('10-a')
        self.script('20-b')

        self.build()

        self.assertEqual(self.joinpoints(), [
            'script_start', 'script_passed', 'script_end',
            'script_start', 'script_passed', 'script_end'])

    def test_build_gives_aspects_registered_for_everything_and_for_the_script(self):
        self.script('10-a')

        self.build(**{'__*': ['all'], '__10-a': ['mine'], '__20-b': ['other']})

        self.assertEqual(self.parts.engine.launches[0][0], ['all', 'mine'])

    def test_build_records_completed_scripts_in_the_progress_log(self):
        self.script('10-a')
        self.script('20-b')

        self.build()

        self.assertEqual(self.dibenv['logconfig'].options('install.d'), ['10-a', '20-b'])
        written = RawConfigParser()
        written.optionxform = str
        written.read(self.logfile)
        self.assertEqual(written.options('install.d'), ['10-a', '20-b'])

    def test_build_skips_scripts_the_progress_log_marks_completed(self):
        self.script('10-a')
        self.script('20-b')
        self.dibenv['logconfig'].add_section('install.d')
        self.dibenv['logconfig'].set('install.d', '10-a', '')

        self.build()

        self.assertEqual(self.ran(), ['20-b'])
        self.assertEqual(self.joinpoints()[0], 'script_skip')

    def test_build_ignore_progress_reruns_completed_scripts(self):
        self.script('10-a')
        self.dibenv['logconfig'].add_section('install.d')
        self.dibenv['logconfig'].set('install.d', '10-a', '')

        self.build(**{'ignore-progress': 'True'})

        self.assertEqual(self.ran(), ['10-a'])

    def test_build_repeats_a_completed_script_when_an_aspect_advises_it(self):
        self.script('10-a')
        self.dibenv['logconfig'].add_section('install.d')
        self.dibenv['logconfig'].set('install.d', '10-a', '')
        self.advise(doNotAvoidScript=True)

        self.build()

        self.assertEqual(self.ran(), ['10-a'])

    def test_build_skips_a_script_an_aspect_preempts(self):
        self.script('10-a')
        self.advise(doNotStartScript=True)

        self.assertTrue(self.build())

        self.assertEqual(self.shell.calls, [])
        self.assertEqual(self.joinpoints(), ['script_start'])


class ScriptFailureTests(RunPartsTestCase):

    def test_build_fails_when_a_script_exits_non_zero(self):
        self.script('10-a')
        self.script('20-b')
        self.shell.results['10-a'] = (3, None)

        self.assertFalse(self.build())

        self.assertEqual(self.parts.log.result, 'failed')
        self.assertEqual(self.ran(), ['10-a'])
        self.assertTrue(self.parts.log.mentions('error', "Script '10-a' failed with error code: 3"))
        self.assertEqual(self.dibenv['logconfig'].options('install.d'), [])

    def test_build_tells_aspects_about_the_failure_in_order(self):
        self.script('10-a')
        self.shell.results['10-a'] = (3, None)

        self.build()

        self.assertEqual(self.joinpoints(), [
            'script_start', 'script_failed', 'script_exception', 'script_end'])

    def test_build_tries_a_failed_script_again_when_advised(self):
        self.script('10-a')
        self.shell.results['10-a'] = [(1, None), (0, {})]
        self.advise(tryScriptAgain=[True, False])

        self.assertTrue(self.build())

        self.assertEqual(self.ran(), ['10-a', '10-a'])
        self.assertEqual(self.joinpoints(), [
            'script_start', 'script_failed', 'script_passed', 'script_end'])
        self.assertEqual(self.dibenv['logconfig'].options('install.d'), ['10-a'])

    def test_build_reports_an_exception_from_the_shell_to_the_aspects(self):
        self.script('10-a')
        self.shell.results['10-a'] = RuntimeError('shell blew up')

        self.assertFalse(self.build())

        self.assertEqual(self.parts.log.result, 'failed')
        self.assertEqual(self.joinpoints(), ['script_start', 'script_exception', 'script_end'])
        extra = self.parts.engine.launches[1][3]
        self.assertIsInstance(extra['__ex__'], RuntimeError)
        self.assertTrue(self.parts.log.mentions('exception', 'DIBRunParts exited on exception'))

    def test_build_stops_at_the_first_failing_script(self):
        for name in ('10-a', '20-b', '30-c'):
            self.script(name)
        self.shell.results['20-b'] = (1, None)

        self.build()

        self.assertEqual(self.ran(), ['10-a', '20-b'])
        self.assertEqual(self.dibenv['logconfig'].options('install.d'), ['10-a'])


class PersistedEnvironmentTests(RunPartsTestCase):

    def test_build_persists_the_merged_environment_after_success(self):
        self.script('10-a')
        self.shell.results['10-a'] = (0, {'FROM_A': u'café'})

        self.build()

        stored = self.stored_environment()
        self.assertEqual(stored['FROM_A'], u'café')
        self.assertEqual(stored['HOME'], '/home/builder')

    def test_build_persists_the_environment_even_when_a_script_fails(self):
        self.script('10-a')
        self.shell.results['10-a'] = (1, None)

        self.build()

        self.assertEqual(self.stored_environment()['PATH'], '/usr/bin:/bin')
        self.assertIsInstance(self.dibenv['logconfig'].get('environment', 'pickle'), str)

    def test_build_points_dib_hooks_at_the_hooks_directory_afterwards(self):
        self.script('10-a')

        self.build()

        self.assertEqual(self.shellenv['DIB_HOOKS'], self.hooks)
        # ... after the environment was saved
        self.assertNotIn('DIB_HOOKS', self.stored_environment())


class InImageTests(RunPartsTestCase):
    HOOKS_IN_TARGET = '/tmp/in_target.d'

    def setUp(self):
        RunPartsTestCase.setUp(self)
        self.sub.on(['sudo', 'mkdir'], effect=lambda inv: os.makedirs(inv.args[2], exist_ok=True))
        self.target = os.path.join(self.mount, 'tmp', 'in_target.d')
        self.proc = os.path.join(self.mount, 'proc')
        self.shellenv['TMPDIR'] = '/var/tmp/build'

    def _build(self, **options):
        return self.build(**dict({'in-image': 'True'}, **options))

    def test_build_prepares_the_chroot_mounts_before_running_scripts(self):
        self.script('10-a')

        self._build()

        self.assertEqual(self.sub.commands()[:4], [
            ['sudo', 'mkdir', self.target],
            ['sudo', 'mount', '--bind', self.hooks, self.target],
            ['sudo', 'mount', '-o', 'remount,ro,bind', self.hooks, self.target],
            ['sudo', 'mkdir', self.proc]])
        self.assertEqual(self.sub.commands()[4],
                         ['sudo', 'mount', '--bind', '/proc', self.proc])

    def test_build_runs_scripts_inside_the_chroot_with_an_image_environment(self):
        self.script('10-a')

        self._build()

        call = self.shell.calls[0]
        self.assertEqual(call['command'], [
            'sudo', '-E', 'chroot', self.mount, '/bin/bash', '-xc',
            'PATH=/usr/bin:/bin:/usr/local/bin; %s/install.d/10-a' % self.HOOKS_IN_TARGET])
        self.assertEqual(call['env']['HOME'], '/root')
        self.assertEqual(call['env']['DIB_HOOKS'], self.HOOKS_IN_TARGET)
        self.assertNotIn('TMPDIR', call['env'])

    def test_build_tears_down_the_chroot_mounts_and_restores_the_environment(self):
        self.script('10-a')

        self.assertTrue(self._build())

        self.assertEqual(self.sub.commands()[5:], [
            ['sudo', 'umount', '-f', self.target],
            ['sudo', 'rmdir', self.target],
            ['sudo', 'umount', self.proc],
            ['sudo', 'rmdir', self.proc]])
        self.assertEqual(self.shellenv['HOME'], '/home/builder')
        self.assertEqual(self.shellenv['TMPDIR'], '/var/tmp/build')
        self.assertEqual(self.shellenv['DIB_HOOKS'], self.hooks)

    def test_build_tears_down_even_when_a_script_fails(self):
        self.script('10-a')
        self.shell.results['10-a'] = (1, None)

        self.assertFalse(self._build())

        self.assertEqual(self.sub.commands()[5], ['sudo', 'umount', '-f', self.target])

    def test_build_fails_when_the_hooks_mount_point_cannot_be_made(self):
        self.script('10-a')
        self.sub.on(['sudo', 'mkdir'], rc=1, first=True)

        self.assertIsNone(self._build())

        self.assertEqual(self.parts.log.result, 'failed')
        self.assertTrue(self.parts.log.mentions('error', 'mkdir for special mountpoint failed'))
        self.assertEqual(self.shell.calls, [])

    def test_build_fails_when_the_hooks_cannot_be_bind_mounted(self):
        self.script('10-a')
        self.sub.on(['sudo', 'mount', '--bind', self.hooks], rc=32, first=True)

        self.assertIsNone(self._build())

        self.assertTrue(self.parts.log.mentions('error', 'mount bind for special mountpoint failed'))
        self.assertEqual(self.shell.calls, [])

    def test_build_fails_when_the_hooks_cannot_be_remounted_read_only(self):
        self.script('10-a')
        self.sub.on(['sudo', 'mount', '-o', 'remount,ro,bind'], rc=32, first=True)

        self.assertIsNone(self._build())

        self.assertTrue(self.parts.log.mentions('error', 'mount for special mountpoint failed'))

    def test_build_fails_when_proc_cannot_be_bind_mounted(self):
        self.script('10-a')
        self.sub.on(['sudo', 'mount', '--bind', '/proc'], rc=32, first=True)

        self.assertIsNone(self._build())

        self.assertTrue(self.parts.log.mentions('error', 'mount bind for the proc mountpoint failed'))
        self.assertEqual(self.shell.calls, [])

    def test_build_reports_failure_when_the_chroot_cannot_be_torn_down(self):
        self.script('10-a')
        self.sub.on(['sudo', 'umount', '-f'], rc=1, first=True)

        self.assertFalse(self._build())

        self.assertEqual(self.parts.log.result, 'failed')
        self.assertTrue(self.parts.log.mentions('error', 'umount for special mountpoint failed'))

    def test_build_reports_failure_when_the_hooks_mount_point_cannot_be_removed(self):
        self.script('10-a')
        self.sub.on(['sudo', 'rmdir', self.target], rc=1, first=True)

        self.assertFalse(self._build())

        self.assertTrue(self.parts.log.mentions('error', 'rmdir of special mountpoint failed'))

    def test_build_reports_failure_when_proc_cannot_be_unmounted(self):
        self.script('10-a')
        self.sub.on(['sudo', 'umount', self.proc], rc=1, first=True)

        self.assertFalse(self._build())

        self.assertTrue(self.parts.log.mentions('error', 'umount bind for the proc mountpoint failed (1)'))

    def test_build_reuses_an_existing_proc_directory(self):
        self.script('10-a')
        os.mkdir(self.proc)

        self._build()

        self.assertNotIn(['sudo', 'mkdir', self.proc], self.sub.commands())

    def test_build_without_tmpdir_does_not_invent_one_afterwards(self):
        del self.shellenv['TMPDIR']
        self.script('10-a')

        self._build()

        self.assertNotIn('TMPDIR', self.shellenv)


if __name__ == '__main__':
    unittest.main()
