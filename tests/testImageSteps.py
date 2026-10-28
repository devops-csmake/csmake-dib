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
"""DIBCopyBuildIntoMount and DIBPackagePhasedImage with ``subprocess`` mocked."""
import os
import unittest
from unittest import mock

from tests import dibsupport
from tests.dibsupport import FakeSubprocess, make_module

copymod = dibsupport.import_dib('DIBCopyBuildIntoMount')
packmod = dibsupport.import_dib('DIBPackagePhasedImage')


class CopyBuildTestCase(unittest.TestCase):

    def setUp(self):
        self.sub = FakeSubprocess(default_rc=0).install(self)
        self.root = dibsupport.make_tmpdir(self)
        self.working = os.path.join(self.root, 'build')
        self.builddir = self.root
        self.mnt = os.path.join(self.builddir, 'mnt')
        os.mkdir(self.working)
        self.dibenv = {'buildworkingdir': self.working, 'build-dir': self.builddir,
                       'shellenv': {}}
        self.copy = make_module(copymod.DIBCopyBuildIntoMount,
                                env={'__DIBEnv__': self.dibenv})
        self.rebuild = mock.MagicMock(return_value=True)
        self.copy._needRebuild = self.rebuild


class CopyBuildTests(CopyBuildTestCase):

    def test_build_copies_the_build_tree_into_the_mount(self):
        self.assertTrue(self.copy.build({}))

        self.assertEqual(self.sub.commands(), [
            'sudo cp -t %s -a %s/*' % (self.mnt, self.working)])
        self.assertTrue(self.sub.invocations[0].kwargs['shell'])
        self.assertEqual(self.copy.log.result, 'passed')

    def test_build_asks_whether_a_copy_is_needed_ignoring_the_special_mounts(self):
        self.copy.build({})

        self.rebuild.assert_called_once_with(
            self.working, self.mnt,
            [os.path.join(self.mnt, 'dev'), os.path.join(self.mnt, 'proc'),
             os.path.join(self.mnt, 'sys')])

    def test_build_moves_the_mount_and_target_root_to_the_image(self):
        self.copy.build({})

        for env in (self.dibenv['shellenv'], self.copy.env.env):
            self.assertEqual(env['TMP_MOUNT_PATH'], self.mnt)
            self.assertEqual(env['TARGET_ROOT'], self.mnt)

    def test_build_skips_the_copy_when_build_avoidance_says_it_is_current(self):
        self.rebuild.return_value = False

        self.assertTrue(self.copy.build({}))

        self.assertEqual(self.sub.commands(), [])
        self.assertTrue(self.copy.log.mentions('info', 'Skipping build copy'))
        self.assertEqual(self.dibenv['shellenv']['TARGET_ROOT'], self.mnt)

    def test_build_fails_when_the_copy_fails(self):
        self.sub.on('sudo cp', rc=1)

        self.assertIsNone(self.copy.build({}))

        self.assertEqual(self.copy.log.result, 'failed')
        self.assertTrue(self.copy.log.mentions('error', 'Transfer of the image to the mounted filesystem failed'))
        self.assertEqual(self.dibenv['shellenv'], {})
        self.assertEqual(self.copy.env.env.get('TARGET_ROOT'), None)

    def test_build_clears_dev_shm_from_the_built_image_when_present(self):
        shm = os.path.join(self.working, 'dev', 'shm')
        os.makedirs(shm)

        self.copy.build({})

        self.assertEqual(self.sub.commands()[0], ['sudo', 'rm', '-rf', shm])

    def test_build_does_not_touch_dev_shm_when_absent(self):
        self.copy.build({})

        self.assertEqual(self.sub.count('sudo', 'rm'), 0)

    def test_build_only_warns_when_dev_shm_cannot_be_removed(self):
        os.makedirs(os.path.join(self.working, 'dev', 'shm'))
        self.sub.on(['sudo', 'rm'], rc=1)

        self.assertTrue(self.copy.build({}))

        self.assertTrue(self.copy.log.mentions('warning', 'Could not remove /dev/shm'))


class PackagePhasedImageTests(unittest.TestCase):

    def setUp(self):
        self.sub = FakeSubprocess(default_rc=0).install(self)
        self.dibenv = {'result-image': '/results/base.img.tgz', 'build-dir': '/build'}
        self.pack = make_module(packmod.DIBPackagePhasedImage,
                                env={'__DIBEnv__': self.dibenv})

    def test_build_tars_the_build_dir_into_the_result_image(self):
        self.assertTrue(self.pack.build({}))

        self.assertEqual(self.sub.commands(), [
            ['sudo', 'tar', '-czvf', '/results/base.img.tgz', '-C', '/build', '.']])
        self.assertEqual(self.pack.log.result, 'passed')

    def test_build_fails_when_tar_fails(self):
        self.sub.on(['sudo', 'tar'], rc=2)

        self.assertFalse(self.pack.build({}))

        self.assertEqual(self.pack.log.result, 'failed')

    def test_clean_removes_the_result_image(self):
        self.assertTrue(self.pack.clean({}))

        self.assertEqual(self.sub.commands(), [['sudo', 'rm', '/results/base.img.tgz']])
        self.assertEqual(self.pack.log.result, 'passed')

    def test_archive_is_not_implemented_but_passes_with_a_warning(self):
        self.assertTrue(self.pack.archive({}))

        self.assertTrue(self.pack.log.mentions('warning', 'NOT IMPLEMENTED'))
        self.assertEqual(self.pack.log.result, 'passed')
        self.assertEqual(self.sub.commands(), [])


if __name__ == '__main__':
    unittest.main()
