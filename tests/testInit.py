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
"""DIBInit: element resolution, hook copying, mount cleanup and image setup.

Everything that would touch root, mounts or the DIB library is behind the
fake ``subprocess`` or a mocked helper (``_needRebuild``,
``_loadStepResultsIntoDict``...); element trees and hook copies use real temp
directories because reading and copying those files is the behaviour tested.
"""
import errno
import os
import unittest
from unittest import mock

from tests import dibsupport
from tests.dibsupport import FakeStepResult, FakeSubprocess, make_module, new_module

initmod = dibsupport.import_dib('DIBInit')


class InitTestCase(unittest.TestCase):

    def setUp(self):
        self.root = dibsupport.make_tmpdir(self)
        self.sub = FakeSubprocess(default_rc=0).install(self)
        self.init = make_module(initmod.DIBInit, usefattr=False,
                                buildAvoidanceIgnore={})
        initmod.DIBInit.exitCallbacks.clear()
        self.addCleanup(initmod.DIBInit.exitCallbacks.clear)

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def element(self, root, name, deps=None, provides=None):
        directory = self.path(root, name)
        os.makedirs(directory, exist_ok=True)
        for filename, items in (('element-deps', deps), ('element-provides', provides)):
            if items is not None:
                with open(os.path.join(directory, filename), 'w') as f:
                    f.write(''.join(item + '\n' for item in items))
        return directory


class ConstructionTests(InitTestCase):

    def test_init_detects_that_getfattr_is_installed(self):
        self.sub.on('which getfattr', rc=0)

        init = new_module(initmod.DIBInit)

        self.assertTrue(init.usefattr)
        self.assertEqual(self.sub.commands(), ['which getfattr'])
        self.assertTrue(self.sub.invocations[0].kwargs['shell'])

    def test_init_detects_that_getfattr_is_missing(self):
        self.sub.on('which getfattr', rc=1)

        init = new_module(initmod.DIBInit)

        self.assertFalse(init.usefattr)

    def test_setfattr_element_tags_the_file_with_its_element(self):
        self.assertTrue(self.init._setfattrElement('/hooks/10-a', 'base'))

        self.assertEqual(self.sub.commands(),
                         ['sudo setfattr -n user.element -v base /hooks/10-a'])

    def test_setfattr_element_reports_failure(self):
        self.sub.on('sudo setfattr', rc=1)

        self.assertFalse(self.init._setfattrElement('/hooks/10-a', 'base'))

    @unittest.skip("pre-existing bug (not a 2/3 issue): _getfattrElement hands "
                   "a StringIO to subprocess as stdout, which has no fileno; "
                   "the method is unused")
    def test_getfattr_element_reads_the_element_attribute(self):
        pass


class ElementDependencyTests(InitTestCase):

    def setUp(self):
        InitTestCase.setUp(self)
        self.init.dibenv = {'elements-path': [self.path('first'), self.path('second')]}

    def test_dependencies_reads_the_element_deps_file(self):
        self.element('first', 'app', deps=['db', ' web '])

        self.assertEqual(self.init._dependencies('app'), {'db', 'web'})

    def test_provides_reads_the_element_provides_file(self):
        self.element('first', 'real', provides=['virtual'])

        self.assertEqual(self.init._provides('real'), {'virtual'})

    def test_element_without_the_file_has_an_empty_set(self):
        self.element('first', 'app')

        self.assertEqual(self.init._dependencies('app'), set())

    def test_the_search_continues_to_the_next_path_when_the_element_is_missing(self):
        self.element('second', 'app', deps=['db'])

        self.assertEqual(self.init._dependencies('app'), {'db'})

    def test_an_unknown_element_is_logged_and_has_no_dependencies(self):
        os.makedirs(self.path('first'))
        os.makedirs(self.path('second'))

        self.assertEqual(self.init._dependencies('ghost'), set())

        self.assertTrue(self.init.log.mentions('error', "Element 'ghost' not found"))

    def test_other_read_errors_are_not_swallowed(self):
        self.element('first', 'app', deps=['db'])
        denied = IOError(errno.EACCES, 'Permission denied')

        with mock.patch('builtins.open', side_effect=denied):
            with self.assertRaises(IOError):
                self.init._dependencies('app')

    def test_expand_dependencies_follows_transitive_dependencies(self):
        self.element('first', 'app', deps=['lib'])
        self.element('first', 'lib', deps=['base'])
        self.element('first', 'base')

        self.assertEqual(self.init._expandDependencies(['app']), {'app', 'lib', 'base'})

    def test_expand_dependencies_leaves_out_elements_that_another_provides(self):
        self.element('first', 'app', deps=['virtual'])
        self.element('first', 'real', provides=['virtual'])

        self.assertEqual(self.init._expandDependencies(['app', 'real']), {'app', 'real'})

    def test_expand_dependencies_rejects_an_explicit_element_that_is_provided(self):
        self.element('first', 'real', provides=['virtual'])
        self.element('first', 'virtual')

        self.assertIsNone(self.init._expandDependencies(['real', 'virtual']))

        self.assertTrue(self.init.log.mentions('error', 'explicitly required but are provided'))


class BuildAvoidanceIgnoreTests(InitTestCase):

    def test_setup_parses_element_and_path_pairs(self):
        self.init.env.substitutions = {'%(WORKING)s': '/work'}

        self.init._setupBuildAvoidanceIgnoreDictionary({
            'build-avoidance-ignore': 'base: %(WORKING)s/a\n\n base :/b\nother:/c'})

        self.assertEqual(self.init.buildAvoidanceIgnore, {
            'base': ['/work/a', '/b'], 'other': ['/c']})

    def test_setup_warns_about_malformed_lines_with_their_line_number(self):
        self.init._setupBuildAvoidanceIgnoreDictionary({
            'build-avoidance-ignore': 'base:/a\nno colon here\na:b:c'})

        self.assertEqual(self.init.buildAvoidanceIgnore, {'base': ['/a']})
        warnings = self.init.log.messages('warning')
        self.assertEqual(len(warnings), 2)
        self.assertIn('line 2', warnings[0])
        self.assertIn('line 3', warnings[1])

    def test_setup_without_the_option_ignores_nothing(self):
        self.init._setupBuildAvoidanceIgnoreDictionary({})

        self.assertEqual(self.init.buildAvoidanceIgnore, {})


class ElementRunTests(InitTestCase):

    def setUp(self):
        InitTestCase.setUp(self)
        self.hooks = self.path('hooks')
        self.paths = [self.path('first'), self.path('second')]
        self.seen = []

    def op(self, result=False):
        def operation(element, elementpath, hooksdir):
            self.seen.append((element, elementpath, hooksdir))
            return result
        return operation

    def test_run_through_elements_applies_the_operation_to_the_first_match(self):
        self.element('second', 'app')
        self.element('first', 'app')

        self.assertTrue(self.init._runThroughElements(
            ['app'], self.paths, self.hooks, self.op()))

        self.assertEqual(self.seen, [('app', self.path('first', 'app'), self.hooks)])

    def test_run_through_elements_skips_blank_names(self):
        self.element('first', 'app')

        self.assertTrue(self.init._runThroughElements(
            ['', ' ', 'app'], self.paths, self.hooks, self.op()))

        self.assertEqual([s[0] for s in self.seen], ['app'])

    def test_run_through_elements_stops_when_the_operation_says_so(self):
        self.element('first', 'a')
        self.element('first', 'b')

        self.assertFalse(self.init._runThroughElements(
            ['a', 'b'], self.paths, self.hooks, self.op(result=True)))

        self.assertEqual([s[0] for s in self.seen], ['a'])

    def test_run_through_elements_warns_about_a_file_named_like_an_element(self):
        os.makedirs(self.paths[0])
        with open(os.path.join(self.paths[0], 'app'), 'w') as f:
            f.write('not a directory')
        self.element('second', 'app')

        self.assertTrue(self.init._runThroughElements(
            ['app'], self.paths, self.hooks, self.op()))

        self.assertTrue(self.init.log.mentions('warning', "had a file instead of a well-formed DIB"))
        self.assertEqual(self.seen[0][1], self.path('second', 'app'))

    def test_run_through_elements_fails_when_a_required_element_is_missing(self):
        self.element('first', 'present')

        with self.assertRaises(KeyError):
            self.init._runThroughElements(
                ['ghost', 'present'], self.paths, self.hooks, self.op())

        self.assertEqual(self.init.log.result, 'failed')
        self.assertTrue(self.init.log.mentions('error', "Required element 'ghost' not found"))

    @unittest.skip("pre-existing bug (not a 2/3 issue): a missing element is "
                   "only detected when another element follows it, so a "
                   "missing last element goes unnoticed")
    def test_run_through_elements_fails_when_the_last_element_is_missing(self):
        pass

    def test_is_element_dirty_checks_each_subdirectory_against_the_hooks(self):
        elementpath = self.element('first', 'app')
        for name in ('install.d', 'post-install.d'):
            os.makedirs(os.path.join(elementpath, name))
        with open(os.path.join(elementpath, 'README'), 'w') as f:
            f.write('files are not compared')
        self.init.buildAvoidanceIgnore = {'app': ['/skip/me']}
        self.init._needRebuild = mock.MagicMock(return_value=False)

        self.assertFalse(self.init._isElementDirty('app', elementpath, self.hooks))

        self.assertEqual(sorted(c[0] for c in self.init._needRebuild.call_args_list), [
            (os.path.join(elementpath, 'install.d'), os.path.join(self.hooks, 'install.d'), ['/skip/me']),
            (os.path.join(elementpath, 'post-install.d'), os.path.join(self.hooks, 'post-install.d'), ['/skip/me'])])

    def test_is_element_dirty_reports_the_first_change(self):
        elementpath = self.element('first', 'app')
        os.makedirs(os.path.join(elementpath, 'install.d'))
        self.init._needRebuild = mock.MagicMock(return_value=True)

        self.assertTrue(self.init._isElementDirty('app', elementpath, self.hooks))

        self.assertTrue(self.init.log.mentions('info', 'image rebuild required'))


class CopyHookTests(InitTestCase):

    def make_tree(self):
        src = self.path('src')
        os.makedirs(os.path.join(src, 'install.d'))
        for name in ('install.d/10-a', 'install.d/20-b', 'top-file'):
            with open(os.path.join(src, name), 'w') as f:
                f.write(name)
        return src

    def test_copy_files_copies_recursively_creating_directories(self):
        src = self.make_tree()
        dst = self.path('dst')

        self.assertTrue(self.init._copyFilesMarkLeaves('base', src, dst))

        with open(os.path.join(dst, 'install.d', '20-b')) as f:
            self.assertEqual(f.read(), 'install.d/20-b')
        self.assertTrue(os.path.isfile(os.path.join(dst, 'top-file')))
        self.assertEqual(self.sub.commands(), [])

    def test_copy_files_tags_each_copied_file_with_its_element_when_attr_is_available(self):
        self.init.usefattr = True
        src = self.make_tree()
        dst = self.path('dst')

        self.init._copyFilesMarkLeaves('base', src, dst)

        self.assertEqual(sorted(self.sub.commands()), sorted(
            'sudo setfattr -n user.element -v base %s' % os.path.join(dst, name)
            for name in ('install.d/10-a', 'install.d/20-b', 'top-file')))

    def test_copy_files_fails_when_a_file_is_in_the_way_of_a_directory(self):
        src = self.make_tree()
        dst = self.path('dst')
        os.makedirs(dst)
        with open(os.path.join(dst, 'install.d'), 'w') as f:
            f.write('a file')

        self.assertFalse(self.init._copyFilesMarkLeaves('base', src, dst))

        self.assertTrue(self.init.log.mentions('error', 'Destination is a file'))

    def test_copy_files_fails_for_a_source_that_is_neither_file_nor_directory(self):
        self.assertFalse(self.init._copyFilesMarkLeaves(
            'base', self.path('missing'), self.path('dst')))

        self.assertTrue(self.init.log.mentions('error', 'Source path is not a file or dir'))

    def test_copy_element_hooks_copies_every_entry_and_asks_to_continue(self):
        src = self.make_tree()
        dst = self.path('hooks')
        os.makedirs(dst)

        self.assertFalse(self.init._copyElementHooks('base', src, dst))

        self.assertTrue(os.path.isfile(os.path.join(dst, 'install.d', '10-a')))

    def test_copy_element_hooks_stops_processing_on_a_copy_error(self):
        src = self.make_tree()
        dst = self.path('hooks')
        os.makedirs(dst)
        with open(os.path.join(dst, 'install.d'), 'w') as f:
            f.write('a file')

        self.assertTrue(self.init._copyElementHooks('base', src, dst))

    def test_copy_element_hooks_ignores_a_blank_element_name(self):
        self.assertFalse(self.init._copyElementHooks('  ', self.path('nowhere'), self.path('hooks')))


class CleanTests(InitTestCase):

    def setUp(self):
        InitTestCase.setUp(self)
        self.dibenv = {'build-dir': '/b/image.build', 'result-dir': '/r'}

    def mount_points(self):
        b = '/b/image.build'
        return [b + '/build/tmp/in_target.d', b + '/build/proc', b + '/mnt/sys',
                b + '/mnt/dev/pts', b + '/mnt/dev', b + '/mnt/proc', b + '/mnt',
                b + '/cache/ccache']

    def test_clean_mounts_unmounts_then_removes_each_mount_point_in_order(self):
        self.init._cleanMounts(self.dibenv)

        expected = []
        for point in self.mount_points():
            expected.extend([['sudo', 'umount', '-f', point], ['sudo', 'rmdir', point]])
        self.assertEqual(self.sub.commands(), expected)

    def test_clean_mounts_keeps_going_when_unmounts_fail(self):
        self.sub.default_rc = 1

        self.init._cleanMounts(self.dibenv)

        self.assertEqual(len(self.sub.commands()), 16)

    def test_clean_mounts_registers_a_minimal_dib_environment(self):
        self.init._cleanMounts(self.dibenv)

        self.assertIs(self.init.env.env['__DIBEnv__'], self.dibenv)
        self.assertEqual(self.dibenv['source-repository-overrides'], {})
        self.assertEqual(self.dibenv['shellenv'], {})

    def test_clean_build_unmounts_and_wipes_the_build_directory(self):
        self.init._cleanBuild(self.dibenv)

        self.assertEqual(self.sub.commands()[-1], ['sudo', 'rm', '-rf', '/b/image.build'])
        self.assertEqual(len(self.sub.commands()), 17)

    def test_clean_build_warns_when_the_build_directory_cannot_be_deleted(self):
        self.sub.on(['sudo', 'rm'], rc=1)

        self.init._cleanBuild(self.dibenv)

        self.assertTrue(self.init.log.mentions('warning', "Build target '/b/image.build' could not be deleted"))

    def test_clean_build_reloads_repos_and_env_into_the_environment(self):
        self.dibenv.update({'repos': 'my-repos', 'env': 'my-env'})
        self.init.engine.steps = {
            'my-repos': FakeStepResult(value={'DIB_REPOTYPE_x': 'tar'}),
            'my-env': FakeStepResult(value={'EXTRA': '1'})}

        self.init._cleanBuild(self.dibenv)

        self.assertEqual(self.init.engine.step_calls, [('my-repos', 'build'), ('my-env', 'build')])
        self.assertEqual(self.init.env.env['DIB_REPOTYPE_x'], 'tar')
        self.assertEqual(self.init.env.env['EXTRA'], '1')

    def test_clean_results_deletes_the_result_directory(self):
        self.init._cleanResults(self.dibenv)

        self.assertEqual(self.sub.commands(), [['sudo', 'rm', '-rf', '/r']])

    def test_clean_results_warns_when_it_cannot_delete(self):
        self.sub.default_rc = 1

        self.init._cleanResults(self.dibenv)

        self.assertTrue(self.init.log.mentions('warning', "Build target '/r' could not be deleted"))

    def test_setup_cleanup_environment_derives_the_standard_paths(self):
        options = {'build-dir': '/b', 'result-dir': '/r', 'extra': 'kept'}

        dibenv = self.init._setupCleanupDibEnv(options)

        self.assertEqual(dibenv, {
            'build-dir': '/b', 'result-dir': '/r', 'extra': 'kept',
            'TARGET_ROOT': '/b/build', 'DIB_HOOKS': '/b/hooks',
            'TMP_MOUNT_PATH': '/b/build'})
        env = self.init.env.env
        self.assertEqual(
            (env['TARGET_ROOT'], env['TMP_MOUNT_PATH'], env['DIB_HOOKS'],
             env['BUILD_DIB_DIR'], env['RESULT_DIB_DIR']),
            ('/b/build', '/b/build', '/b/hooks', '/b', '/r'))

    def test_clean_removes_build_and_results_and_unregisters_the_exit_callback(self):
        self.init._registerOnExitCallback('_onExit')

        dibenv = self.init.clean({'build-dir': '/b', 'result-dir': '/r'})

        commands = self.sub.commands()
        self.assertIn(['sudo', 'rm', '-rf', '/b'], commands)
        self.assertEqual(commands[-1], ['sudo', 'rm', '-rf', '/r'])
        self.assertEqual(self.init.engine.unregistered, [1])
        self.assertEqual(self.init.log.result, 'passed')
        self.assertEqual(dibenv['TARGET_ROOT'], '/b/build')

    def test_clean_without_a_registered_exit_callback_still_passes(self):
        self.init.clean({'build-dir': '/b', 'result-dir': '/r'})

        self.assertEqual(self.init.log.result, 'passed')

    def test_clean_build_leaves_the_results_alone(self):
        self.init.clean_build({'build-dir': '/b', 'result-dir': '/r'})

        self.assertNotIn(['sudo', 'rm', '-rf', '/r'], self.sub.commands())
        self.assertIn(['sudo', 'rm', '-rf', '/b'], self.sub.commands())
        self.assertEqual(self.init.log.result, 'passed')

    def test_clean_results_is_clean(self):
        self.init.clean_results({'build-dir': '/b', 'result-dir': '/r'})

        self.assertIn(['sudo', 'rm', '-rf', '/r'], self.sub.commands())

    def test_on_exit_unmounts_whatever_is_still_mounted(self):
        self.init.options = {'build-dir': '/b', 'result-dir': '/r'}

        self.init._onExit()

        self.assertEqual(self.sub.commands()[0], ['sudo', 'umount', '-f', '/b/build/tmp/in_target.d'])
        self.assertEqual(len(self.sub.commands()), 16)

    def test_default_phase_only_sets_up_the_cleanup_environment(self):
        self.init.default({'build-dir': '/b', 'result-dir': '/r'})

        self.assertEqual(self.init.log.result, 'passed')
        self.assertEqual(self.init.env.env['BUILD_DIB_DIR'], '/b')
        self.assertEqual(self.sub.commands(), [])

    def test_the_test_phase_is_not_supported(self):
        with self.assertRaises(Exception):
            self.init.test({})


class StartImageTests(InitTestCase):

    def test_prepare_start_image_is_a_noop_without_a_start_image(self):
        self.assertTrue(self.init._prepareStartImage({'build-dir': '/b'}))

        self.assertEqual(self.sub.commands(), [])

    def test_prepare_start_image_unpacks_the_saved_image_into_the_build_dir(self):
        self.assertTrue(self.init._prepareStartImage(
            {'start-image': '/s/base.tgz', 'build-dir': '/b'}))

        self.assertEqual(self.sub.commands(), [
            ['sudo', 'tar', '--numeric-owner', '-xzpvf', '/s/base.tgz', '-C', '/b']])

    def test_prepare_start_image_reports_a_failed_unpack(self):
        self.sub.on(['sudo', 'tar'], rc=2)

        self.assertFalse(self.init._prepareStartImage(
            {'start-image': '/s/base.tgz', 'build-dir': '/b'}))

    def test_append_start_elements_adds_elements_and_existing_paths(self):
        existing = self.path('elements')
        os.makedirs(existing)
        dibenv = {'start-elements': ['ubuntu', 'vm'],
                  'start-elementpath': [existing, existing, self.path('gone')]}

        elements, paths = self.init._appendStartElements(dibenv, {'app'}, ['/p'])

        self.assertEqual(elements, {'app', 'ubuntu', 'vm'})
        self.assertEqual(paths, ['/p', existing])
        self.assertTrue(self.init.log.mentions('warning', "doesn't exist in this environment"))

    def test_append_start_elements_notes_a_fresh_build_without_start_elements(self):
        elements, paths = self.init._appendStartElements({}, {'app'}, ['/p'])

        self.assertEqual((elements, paths), ({'app'}, ['/p']))
        self.assertTrue(self.init.log.mentions('info', 'No start image elements were found'))

    def test_create_fresh_image_makes_the_build_layout_and_rereads_the_log(self):
        dibenv = {'build-dir': self.path('b'), 'buildworkingdir': self.path('b', 'build'),
                  'hooksdir': self.path('b', 'hooks'), 'result-dir': self.path('r'),
                  'cache-path': self.path('b', 'cache')}
        self.init._setupLogConfig = mock.MagicMock()

        self.init._createFreshImage(dibenv)

        for key in ('build-dir', 'buildworkingdir', 'hooksdir', 'result-dir', 'cache-path'):
            self.assertTrue(os.path.isdir(dibenv[key]), key)
        self.init._setupLogConfig.assert_called_once_with(dibenv)

    def test_create_fresh_image_unpacks_the_start_image_first(self):
        dibenv = {'build-dir': self.path('b'), 'buildworkingdir': self.path('b', 'build'),
                  'hooksdir': self.path('b', 'hooks'), 'result-dir': self.path('r'),
                  'cache-path': self.path('b', 'cache'), 'start-image': '/s/base.tgz'}
        self.init._setupLogConfig = mock.MagicMock()

        self.init._createFreshImage(dibenv)

        self.assertEqual(self.sub.commands(), [
            ['sudo', 'tar', '--numeric-owner', '-xzpvf', '/s/base.tgz', '-C', self.path('b')]])

    def test_create_fresh_image_fails_when_the_start_image_cannot_be_unpacked(self):
        dibenv = {'build-dir': self.path('b'), 'start-image': '/s/base.tgz'}
        self.sub.on(['sudo', 'tar'], rc=2)
        self.init._setupLogConfig = mock.MagicMock()

        self.assertIsNone(self.init._createFreshImage(dibenv))

        self.assertEqual(self.init.log.result, 'failed')
        self.assertTrue(self.init.log.mentions('error', "Could not load start image from '/s/base.tgz'"))
        self.init._setupLogConfig.assert_not_called()


class LoadStepResultsTests(InitTestCase):

    def test_load_step_results_runs_each_listed_step_and_merges_return_values(self):
        self.init.engine.steps = {
            'one': FakeStepResult(value={'A': '1'}),
            'two': FakeStepResult(value={'B': '2'}),
            'three': FakeStepResult(value=None)}

        result = self.init._loadStepResultsIntoDict('build', 'one, two\n three,,')

        self.assertEqual(result, {'A': '1', 'B': '2'})
        self.assertEqual([c[0] for c in self.init.engine.step_calls], ['one', 'two', 'three'])
        self.assertTrue(self.init.log.mentions('info', 'A value was not returned'))

    def test_load_step_results_applies_substitutions_to_the_step_list(self):
        self.init.env.substitutions = {'%(REPOS)s': 'one,two'}
        self.init.engine.steps = {'one': FakeStepResult(), 'two': FakeStepResult()}

        self.init._loadStepResultsIntoDict('build', '%(REPOS)s')

        self.assertEqual([c[0] for c in self.init.engine.step_calls], ['one', 'two'])

    def test_load_step_results_fails_when_a_step_fails(self):
        self.init.engine.steps = {'bad': FakeStepResult(passed=False)}

        with self.assertRaises(RuntimeError):
            self.init._loadStepResultsIntoDict('build', 'bad')

        self.assertEqual(self.init.log.result, 'failed')
        self.assertTrue(self.init.log.mentions('error', 'Execution of "bad" failed in DIBInit'))

    def test_load_step_results_fails_when_a_step_returns_nothing(self):
        with self.assertRaises(RuntimeError):
            self.init._loadStepResultsIntoDict('build', 'unknown-step')


class SetupDibEnvTests(InitTestCase):

    def options(self, **extra):
        options = {'result-dir': self.path('results'), 'result-image': 'img',
                   'build-dir': self.path('b')}
        options.update(extra)
        return options

    def test_setup_derives_the_image_log_and_cache_locations(self):
        dibenv = self.init._setupDibEnv(self.options())

        self.assertEqual(dibenv['result-image'], self.path('results', 'img'))
        self.assertEqual(dibenv['result-image-name'], 'img')
        self.assertEqual(dibenv['logfile'], self.path('b', 'img.progresslog'))
        self.assertEqual(dibenv['cache-path'], self.path('b', 'cache'))
        self.assertEqual(dibenv['imagedir'], self.path('b', 'image'))
        self.assertEqual(dibenv['buildworkingdir'], self.path('b', 'build'))
        self.assertEqual(dibenv['hooksdir'], self.path('b', 'hooks'))
        self.assertEqual(dibenv['source-repository-overrides'], {})
        self.assertIs(self.init.env.env['__DIBEnv__'], dibenv)

    def test_setup_loads_the_progress_log_and_shell_environment(self):
        dibenv = self.init._setupDibEnv(self.options())

        self.assertEqual(dibenv['logconfig'].sections(), ['environment'])
        self.assertEqual(dibenv['shellenv'], dict(os.environ))

    def test_setup_strips_root_label_arch_and_release(self):
        dibenv = self.init._setupDibEnv(self.options(
            **{'root-label': ' cloudimg-rootfs ', 'arch': ' amd64\n', 'release': ' xenial '}))

        self.assertEqual((dibenv['root-label'], dibenv['arch'], dibenv['release']),
                         ('cloudimg-rootfs', 'amd64', 'xenial'))

    def test_setup_substitutes_directories_and_image_names(self):
        self.init.env.substitutions = {'%(OUT)s': self.path('out'), '%(NAME)s': 'final.img'}

        dibenv = self.init._setupDibEnv(self.options(**{
            'result-dir': '%(OUT)s', 'result-image': '%(NAME)s', 'build-dir': '%(OUT)s/b'}))

        self.assertEqual(dibenv['result-image'], self.path('out', 'final.img'))
        self.assertEqual(dibenv['logfile'], self.path('out', 'b', 'final.img.progresslog'))

    def test_setup_locates_the_start_image_and_its_progress_log(self):
        startdir = self.path('saved')
        os.makedirs(startdir)
        open(os.path.join(startdir, 'base.img'), 'w').close()

        dibenv = self.init._setupDibEnv(self.options(**{
            'start-dir': startdir, 'start-image': 'base.img'}))

        self.assertEqual(dibenv['start-image'], os.path.join(startdir, 'base.img'))
        self.assertEqual(dibenv['start-logfilename'], 'base.img.progresslog')

    def test_setup_fails_when_the_start_image_is_missing(self):
        result = self.init._setupDibEnv(self.options(**{
            'start-dir': self.path('saved'), 'start-image': 'base.img'}))

        self.assertIsNone(result)
        self.assertEqual(self.init.log.result, 'failed')
        self.assertTrue(self.init.log.mentions('error', 'Did not find image'))

    @unittest.skip("pre-existing bug (not a 2/3 issue): without a build-dir "
                   "option _setupDibEnv formats an undefined name "
                   "(resultImageName), raising NameError")
    def test_setup_defaults_the_build_dir_from_the_result_dir(self):
        pass


class SetupLogConfigFailureTests(InitTestCase):

    def dibenv(self, **extra):
        dibenv = {'logfile': self.path('image.progresslog'), 'build-dir': self.root}
        dibenv.update(extra)
        return dibenv

    def write_log(self, path, pickle_value):
        with open(path, 'w') as f:
            f.write('[environment]\npickle = %s\n' % pickle_value)

    def test_setup_log_config_without_a_log_file_starts_fresh_from_the_process_environment(self):
        dibenv = self.dibenv()

        self.init._setupLogConfig(dibenv)

        self.assertEqual(dibenv['logconfig'].sections(), ['environment'])
        self.assertEqual(dibenv['shellenv'], dict(os.environ))
        self.assertIsNot(dibenv['shellenv'], os.environ)

    def test_setup_log_config_keeps_the_process_environment_when_the_stored_one_is_corrupt(self):
        self.write_log(self.path('image.progresslog'), 'this is not base64 or a pickle!')
        dibenv = self.dibenv()

        self.init._setupLogConfig(dibenv)

        self.assertEqual(dibenv['shellenv'], dict(os.environ))
        self.assertTrue(self.init.log.mentions('exception', "Couldn't load the previous build shell environment"))

    def test_setup_log_config_reports_a_corrupt_start_image_environment(self):
        self.write_log(self.path('base.progresslog'), 'AAAA')
        dibenv = self.dibenv(**{'start-logfilename': 'base.progresslog'})

        self.init._setupLogConfig(dibenv)

        self.assertNotIn('startenv', dibenv)
        self.assertTrue(self.init.log.mentions('exception', "Couldn't load the start image environment"))

    def test_setup_log_config_tolerates_a_missing_start_log(self):
        dibenv = self.dibenv(**{'start-logfilename': 'absent.progresslog'})

        self.init._setupLogConfig(dibenv)

        self.assertNotIn('startenv', dibenv)
        self.assertEqual(self.init.log.messages('exception'), [])


class CalculateElementsTests(InitTestCase):

    def setUp(self):
        InitTestCase.setUp(self)
        self.init.env.env['WORKING'] = '/work'
        self.init.options = {}
        self.init._expandDependencies = mock.MagicMock(side_effect=lambda e: set(e) | {'dep'})

    def dibenv(self, **extra):
        dibenv = {'elements-path': '/e1 /e2', 'elements': 'app,db\n web'}
        dibenv.update(extra)
        return dibenv

    def test_calculate_elements_splits_paths_and_elements(self):
        dibenv = self.dibenv()

        elements, paths = self.init._calculateElements(dibenv)

        self.assertEqual(paths, ['/e1', '/e2'])
        self.assertEqual(dibenv['elements-path'], ['/e1', '/e2'])
        self.assertEqual(dibenv['elements'], ['app', 'db', 'web'])
        self.assertEqual(elements, {'app', 'db', 'web', 'dep'})
        self.init._expandDependencies.assert_called_once_with(['app', 'db', 'web'])

    def test_calculate_elements_leading_colon_means_the_working_directory_elements(self):
        dibenv = self.dibenv(**{'elements-path': ':/e1'})

        _, paths = self.init._calculateElements(dibenv)

        self.assertEqual(paths, ['/work/Elements', '/e1'])

    def test_calculate_elements_without_dependency_expansion_when_disabled(self):
        self.init.options = {'no-deps': 'True'}

        elements, _ = self.init._calculateElements(self.dibenv())

        self.assertEqual(elements, ['app', 'db', 'web'])
        self.init._expandDependencies.assert_not_called()

    def test_calculate_elements_expands_when_no_deps_is_false(self):
        self.init.options = {'no-deps': 'False'}

        self.init._calculateElements(self.dibenv())

        self.init._expandDependencies.assert_called_once()

    def test_calculate_elements_applies_substitutions(self):
        self.init.env.substitutions = {'%(P)s': '/subst', '%(E)s': 'only'}

        elements, paths = self.init._calculateElements(
            self.dibenv(**{'elements-path': '%(P)s', 'elements': '%(E)s'}))

        self.assertEqual((paths, elements), (['/subst'], {'only', 'dep'}))


class BuildTests(InitTestCase):
    """DIBInit.build orchestration; each helper it calls is mocked."""

    def setUp(self):
        InitTestCase.setUp(self)
        self.calls = []
        self.dibenv = {
            'imagedir': '/b/image', 'buildworkingdir': '/b/build',
            'hooksdir': '/b/hooks', 'cache-path': '/b/cache',
            'arch': 'amd64', 'release': 'xenial', 'root-label': 'rootfs',
            'build-dir': '/b', 'result-dir': '/r',
            'elements-path': ['/e1', '/e2'], 'shellenv': {}}
        self.init.env.env['RESULTS'] = '/res'
        self.dirty = []   # scripted answers for the dirty check, then the copy
        self.fakes = {}

        def fake(name, side_effect=None, return_value=None):
            def record(*args, **kwargs):
                self.calls.append(name)
                if side_effect is not None:
                    return side_effect(*args, **kwargs)
                return return_value
            mocked = mock.MagicMock(side_effect=record)
            setattr(self.init, name, mocked)
            self.fakes[name] = mocked
            return mocked

        fake('_setupDibEnv', return_value=self.dibenv)
        fake('_calculateElements', return_value=(['app', 'db'], ['/e1', '/e2']))
        fake('_runThroughElements', side_effect=lambda *a: self.dirty.pop(0))
        fake('_cleanBuild')
        fake('_cleanResults')
        fake('_createFreshImage')
        fake('_appendStartElements', side_effect=lambda dibenv, e, p: (e, p))
        fake('_loadStepResultsIntoDict', return_value={})
        self.dirty = [True]   # elements are up to date

    def build(self, **options):
        return self.init.build(options)

    def test_build_registers_the_exit_cleanup_and_remembers_the_options(self):
        options = {'result-dir': '/r'}

        self.init.build(options)

        self.assertIs(self.init.options, options)
        self.assertEqual(len(self.init.engine.exit_callbacks), 1)
        self.fakes['_setupDibEnv'].assert_called_once_with(options)

    def test_build_leaves_an_up_to_date_image_alone(self):
        self.assertIs(self.build(), self.dibenv)

        self.assertEqual(self.calls, [
            '_setupDibEnv', '_calculateElements', '_runThroughElements',
            '_appendStartElements'])
        self.assertEqual(self.init.log.result, 'passed')
        check = self.fakes['_runThroughElements'].call_args[0]
        self.assertEqual(check[:3], (['app', 'db'], ['/e1', '/e2'], '/b/hooks'))
        self.assertEqual(check[3], self.init._isElementDirty)

    def test_build_exports_the_computed_environment_to_the_shell_and_csmake(self):
        self.build()

        expected = {
            'TARGET_ROOT': '/b/build', 'TMP_MOUNT_PATH': '/b/build',
            'DIB_IMAGE_CACHE': '/b/cache', 'ARCH': 'amd64',
            'TMP_HOOKS_PATH': '/b/hooks', 'DIB_HOOKS': '/b/hooks',
            'DIB_RELEASE': 'xenial', 'IMAGE_ELEMENT': 'app db',
            'ELEMENT_PATH': '/e1:/e2', 'DIB_ROOT_LABEL': 'rootfs',
            'BUILD_DIB_DIR': '/b', 'RESULT_DIB_DIR': '/r',
            '_LIB': os.path.join('/res', 'diskimage-builder', 'lib')}
        self.assertEqual(self.dibenv['shellenv'], expected)
        for key, value in expected.items():
            self.assertEqual(self.init.env.env[key], value, key)

    def test_build_makes_a_relative_cache_path_absolute(self):
        self.dibenv['cache-path'] = 'rel/cache'

        self.build()

        self.assertEqual(self.dibenv['shellenv']['DIB_IMAGE_CACHE'],
                         os.path.join(os.getcwd(), 'rel/cache'))

    def test_build_adds_start_image_elements_to_the_image_element_list(self):
        self.fakes['_appendStartElements'].side_effect = (
            lambda dibenv, e, p: (e + ['ubuntu'], p))

        self.build()

        self.assertEqual(self.dibenv['shellenv']['IMAGE_ELEMENT'], 'app db ubuntu')

    def test_build_rebuilds_from_scratch_when_an_element_changed(self):
        self.dirty = [False, True]

        self.assertIs(self.build(), self.dibenv)

        self.assertEqual(self.calls, [
            '_setupDibEnv', '_calculateElements', '_runThroughElements',
            '_cleanBuild', '_cleanResults', '_createFreshImage',
            '_runThroughElements', '_appendStartElements'])
        copy = self.fakes['_runThroughElements'].call_args_list[1][0]
        self.assertEqual(copy[3], self.init._copyElementHooks)

    def test_build_fails_when_the_element_hooks_cannot_be_copied(self):
        self.dirty = [False, False]

        self.assertIsNone(self.build())

        self.assertEqual(self.init.log.result, 'failed')
        self.assertTrue(self.init.log.mentions('error', 'Failed to copy element hooks to image'))
        self.assertNotIn('_appendStartElements', self.calls)
        self.assertEqual(self.dibenv['shellenv'], {})

    def test_build_fails_when_the_dib_environment_cannot_be_set_up(self):
        self.fakes['_setupDibEnv'].side_effect = lambda options: None

        self.assertIsNone(self.build())

        self.assertEqual(self.init.log.result, 'failed')
        self.assertNotIn('_calculateElements', self.calls)

    def test_build_adds_repo_and_env_step_results_to_the_environment(self):
        self.dibenv.update({'repos': 'my-repos', 'env': 'my-env'})
        self.fakes['_loadStepResultsIntoDict'].side_effect = (
            lambda phase, option: {'FROM_' + option.upper().replace('-', '_'): '1'})

        self.build()

        self.assertEqual(
            [c[0] for c in self.fakes['_loadStepResultsIntoDict'].call_args_list],
            [('build', 'my-repos'), ('build', 'my-env')])
        self.assertEqual(self.dibenv['shellenv']['FROM_MY_REPOS'], '1')
        self.assertEqual(self.dibenv['shellenv']['FROM_MY_ENV'], '1')


if __name__ == '__main__':
    unittest.main()
