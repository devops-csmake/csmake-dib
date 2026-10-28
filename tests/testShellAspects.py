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
"""The DIB aspects and shell modules.

DIBShell, DIBShellAspect and DIBShellToEnvironment extend csmake core's
Shell, ShellAspect and ShellToEnvironment (stand-ins are substituted for those
base modules, see dibsupport), so these tests check what the DIB classes add:
choosing the DIB shell environment, delegating to the right base, and the
RunParts aspect plumbing (registration, skipping, retry with a file hack).
"""
import os
import unittest
from unittest import mock

from tests import dibsupport
from tests.dibsupport import make_module, new_module

aspectmod = dibsupport.import_dib('DIBRunPartsAspect')
skipmod = dibsupport.import_dib('SkipDIBScript')
retrymod = dibsupport.import_dib('DIBRetryWithTemporaryFileHack')
hackmod = dibsupport.import_dib('DIBTemporaryFileHack')
shellmod = dibsupport.import_dib('DIBShell')
shellaspectmod = dibsupport.import_dib('DIBShellAspect')
toenvmod = dibsupport.import_dib('DIBShellToEnvironment')


class RunPartsAspectTests(unittest.TestCase):

    def setUp(self):
        self.aspect = make_module(aspectmod.DIBRunPartsAspect)

    def test_install_part_handler_registers_the_aspect_under_the_part_name(self):
        options = {'partOverride': ' 05-install-stuff '}
        stepoptions = {}

        self.aspect._installPartHandler(options, stepoptions)

        self.assertEqual(stepoptions, {'__05-install-stuff': [(self.aspect, options)]})

    def test_install_part_handler_appends_to_aspects_already_registered(self):
        other = make_module(aspectmod.DIBRunPartsAspect)
        stepoptions = {}
        other._installPartHandler({'partOverride': '*'}, stepoptions)

        self.aspect._installPartHandler({'partOverride': '*'}, stepoptions)

        self.assertEqual([a for a, _ in stepoptions['__*']], [other, self.aspect])

    def test_install_part_handler_requires_a_part_override(self):
        with self.assertRaises(KeyError):
            self.aspect._installPartHandler({}, {})

    def test_start_registers_the_handler_and_passes(self):
        options = {'partOverride': '10-run'}
        stepoptions = {}

        self.aspect.start('build', options, None, stepoptions)

        self.assertIs(self.aspect.options, options)
        self.assertEqual(stepoptions['__10-run'], [(self.aspect, options)])
        self.assertEqual(self.aspect.log.result, 'passed')


class SkipDIBScriptTests(unittest.TestCase):

    def test_script_start_tells_run_parts_not_to_start_the_script(self):
        skip = make_module(skipmod.SkipDIBScript)

        self.assertTrue(skip.script_start__build('build', {}, None, None))

        self.assertEqual(skip.flowcontrol.overrides, [('doNotStartScript', True, skip)])
        self.assertEqual(skip.log.result, 'passed')


class RetryWithTemporaryFileHackTests(unittest.TestCase):

    def setUp(self):
        self.retry = make_module(retrymod.DIBRetryWithTemporaryFileHack)
        for name in ('script_start__build', 'script_end__build'):
            patcher = mock.patch.object(
                hackmod.DIBTemporaryFileHack, name, return_value='hack-result')
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def test_script_start_only_resets_state_and_does_not_hack_the_file(self):
        self.assertTrue(self.retry.script_start__build('build', {}, None, None))

        self.assertFalse(self.retry.retried)
        self.assertIsNone(self.retry.dirpath)
        self.script_start__build.assert_not_called()
        self.assertEqual(self.retry.log.result, 'passed')

    def test_first_failure_asks_for_another_try_and_applies_the_hack(self):
        self.retry.script_start__build('build', {}, None, None)
        options = {'file': '/etc/x'}

        result = self.retry.script_failed__build('build', options, 'step', 'stepopts')

        self.assertEqual(result, 'hack-result')
        self.assertTrue(self.retry.retried)
        self.assertEqual(self.retry.flowcontrol.overrides, [('tryScriptAgain', True, self.retry)])
        self.script_start__build.assert_called_once_with(
            self.retry, 'build', options, 'step', 'stepopts')

    def test_second_failure_gives_up(self):
        self.retry.script_start__build('build', {}, None, None)
        self.retry.script_failed__build('build', {}, None, None)
        self.retry.flowcontrol.overrides[:] = []

        self.assertFalse(self.retry.script_failed__build('build', {}, None, None))

        self.assertEqual(self.retry.log.result, 'failed')
        self.assertEqual(self.retry.flowcontrol.overrides, [])
        self.assertEqual(self.script_start__build.call_count, 1)
        self.assertTrue(self.retry.log.mentions('info', 'Step failed on retry'))

    def test_failure_to_hack_the_file_fails_the_aspect(self):
        self.retry.script_start__build('build', {}, None, None)
        self.script_start__build.side_effect = IOError('read-only filesystem')

        self.assertFalse(self.retry.script_failed__build('build', {}, None, None))

        self.assertEqual(self.retry.log.result, 'failed')
        self.assertTrue(self.retry.log.mentions('exception', 'Failed to hack file'))

    def test_end_restores_the_file_only_if_a_retry_happened(self):
        self.retry.script_start__build('build', {}, None, None)
        self.assertTrue(self.retry.script_end__build('build', {}, None, None))
        self.script_end__build.assert_not_called()

        self.retry.script_failed__build('build', {}, None, None)
        result = self.retry.script_end__build('build', {'k': 'v'}, 's', 'so')

        self.assertEqual(result, 'hack-result')
        self.script_end__build.assert_called_once_with(
            self.retry, 'build', {'k': 'v'}, 's', 'so')


class ShellTests(unittest.TestCase):

    def test_starting_environment_is_the_dib_shell_environment(self):
        shellenv = {'PATH': '/image/bin'}
        shell = make_module(shellmod.DIBShell, env={'__DIBEnv__': {'shellenv': shellenv}})

        result = shell._getStartingEnvironment({})

        self.assertIs(result, shellenv)
        self.assertIs(shell.newenv, shellenv)

    def test_starting_environment_defers_to_shell_outside_a_dib_run(self):
        shell = make_module(shellmod.DIBShell, env={})

        result = shell._getStartingEnvironment({})

        self.assertIs(result, shellmod.Shell.STARTING_ENVIRONMENT)


class ShellAspectTests(unittest.TestCase):

    def setUp(self):
        self.aspect = new_module(shellaspectmod.DIBShellAspect)

    def test_construction_initialises_both_aspect_bases_and_a_dib_shell(self):
        self.assertFalse(self.aspect.optionSubstitutionsDone)   # CsmakeAspect
        self.assertTrue(self.aspect.shellaspect_initialised)    # ShellAspect
        self.assertIsInstance(self.aspect.sheller, shellmod.DIBShell)
        self.assertIs(self.aspect.sheller.env, self.aspect.env)

    def test_subclass_joinpoints_are_found_the_run_parts_aspect_way(self):
        self.assertEqual(
            self.aspect._lookupSubclassImplementedJoinPoints('start', 'build', {}),
            self.aspect.start)
        self.assertIsNone(
            self.aspect._lookupSubclassImplementedJoinPoints('script_passed', 'build', {}))

    def test_join_point_lookup_is_the_shell_aspects(self):
        self.assertEqual(
            self.aspect._joinPointLookup('script_start', 'build', {}),
            ('ShellAspect._joinPointLookup', 'script_start', 'build'))


class ShellToEnvironmentTests(unittest.TestCase):

    def setUp(self):
        self.shellenv = {'FROM_IMAGE': 'yes'}
        self.step = make_module(
            toenvmod.DIBShellToEnvironment,
            env={'__DIBEnv__': {'shellenv': self.shellenv}})
        self.seen = []

        def record_environ(instance, options):
            self.seen.append(os.environ)
            return True
        patcher = mock.patch.object(
            toenvmod.ShellToEnvironment, 'default', record_environ)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_starting_environment_is_the_dib_shell_environment(self):
        self.assertIs(self.step._getStartingEnvironment(), self.shellenv)

    def test_starting_environment_is_the_process_environment_outside_a_dib_run(self):
        step = make_module(toenvmod.DIBShellToEnvironment, env={})

        self.assertIs(step._getStartingEnvironment(), os.environ)

    def test_build_reads_the_variables_from_the_dib_environment_then_restores_it(self):
        saved = os.environ

        self.assertTrue(self.step.build({'var': 'FROM_IMAGE'}))

        self.assertEqual(len(self.seen), 1)
        self.assertIs(self.seen[0], self.shellenv)
        self.assertIs(os.environ, saved)

    def test_build_restores_the_environment_when_the_base_step_raises(self):
        saved = os.environ
        with mock.patch.object(toenvmod.ShellToEnvironment, 'default',
                               side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self.step.build({})

        self.assertIs(os.environ, saved)

    def test_build_outside_a_dib_run_reads_the_process_environment(self):
        step = make_module(toenvmod.DIBShellToEnvironment, env={})

        step.build({})

        self.assertIs(self.seen[0], os.environ)

    def test_default_skips_execution_but_declares_the_variables(self):
        env = self.step.env.env

        self.assertTrue(self.step.default({'build-id': 'BUILD_ID', 'branch': 'BRANCH:master'}))

        self.assertEqual((env['build-id'], env['branch']), (None, None))
        self.assertEqual(self.step.log.result, 'passed')
        self.assertTrue(self.step.log.mentions('debug', 'Skipping actual execution'))


if __name__ == '__main__':
    unittest.main()
