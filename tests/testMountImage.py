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
"""DIBMountImage / DIBUmountImage with ``subprocess`` mocked.

Python-3 conversion risk covered: ``losetup --show`` output goes through
``communicate()`` and is concatenated with the partition suffix, so the fake
Popen (bytes unless text mode was requested) must be read in text mode.
Mount order and the cleanup order after a failure are asserted exactly.
"""
import os
import types
import unittest
from unittest import mock

from tests import dibsupport
from tests.dibsupport import FakeSubprocess, make_module

mountmod = dibsupport.import_dib('DIBMountImage')
umountmod = dibsupport.import_dib('DIBUmountImage')

LOOP = '/dev/loop7'
UMOUNT = ['sudo', 'umount']


class MountTestCase(unittest.TestCase):

    def setUp(self):
        self.sub = FakeSubprocess(default_rc=0)
        self.sub.on(['sudo', 'losetup', '--show', '-f'], stdout=LOOP + '\n')
        self.sub.install(self)
        self.builddir = dibsupport.make_tmpdir(self)
        self.mnt = os.path.join(self.builddir, 'mnt')
        self.dibenv = {
            'imagedir': '/build/image',
            'build-dir': self.builddir,
            'shellenv': {},
            'partitions': {
                'part_1': {'label': 'rootfs', 'lopart': 'p1', 'root': True},
                'part_2': {'label': 'data', 'lopart': 'p2'}}}
        self.mount = make_module(
            mountmod.DIBMountImage, env={'__DIBEnv__': self.dibenv})

    def _cleanup_commands(self):
        m = self.mnt
        return [UMOUNT + [os.path.join(m, 'sys')],
                UMOUNT + [os.path.join(m, 'dev', 'pts')],
                UMOUNT + [os.path.join(m, 'dev')],
                UMOUNT + [os.path.join(m, 'proc')],
                UMOUNT + [m],
                ['sudo', 'losetup', '-d', LOOP]]

    def _mounts(self):
        return [c for c in self.sub.commands() if c[:2] == ['sudo', 'mount']]


class MountBuildTests(MountTestCase):

    def test_build_attaches_the_image_reading_losetup_as_text(self):
        self.assertTrue(self.mount.build({}))

        first = self.sub.invocations[0]
        self.assertEqual(first.args, ['sudo', 'losetup', '--show', '-f', '/build/image'])
        self.assertEqual(first.func, 'Popen')
        self.assertTrue(first.kwargs['universal_newlines'])

    def test_build_records_loop_device_and_root_block_device(self):
        self.assertTrue(self.mount.build({}))

        self.assertEqual(self.mount.log.result, 'passed')
        self.assertEqual(self.dibenv['loopbackDevice'], LOOP)
        self.assertEqual(self.dibenv['shellenv'], {
            'CSMAKE_IMAGE_BLOCK_DISK': LOOP,
            'IMAGE_BLOCK_DEVICE': LOOP + 'p1',
            'TMP_BUILD_DIR': self.builddir})

    def test_build_mounts_root_then_proc_dev_pts_and_sys_in_order(self):
        self.assertTrue(self.mount.build({}))

        m = self.mnt
        self.assertEqual(self._mounts(), [
            ['sudo', 'mount', LOOP + 'p1', m],
            ['sudo', 'mount', '-t', 'proc', 'none', os.path.join(m, 'proc')],
            ['sudo', 'mount', '--bind', '/dev', os.path.join(m, 'dev')],
            ['sudo', 'mount', '--bind', '/dev/pts', os.path.join(m, 'dev', 'pts')],
            ['sudo', 'mount', '-t', 'sysfs', 'none', os.path.join(m, 'sys')]])

    def test_build_creates_the_missing_mount_points(self):
        self.assertTrue(self.mount.build({}))

        m = self.mnt
        self.assertTrue(os.path.isdir(m))
        self.assertEqual(
            [c for c in self.sub.commands() if c[:2] == ['sudo', 'mkdir']],
            [['sudo', 'mkdir', os.path.join(m, 'proc')],
             ['sudo', 'mkdir', os.path.join(m, 'dev')],
             ['sudo', 'mkdir', os.path.join(m, 'dev', 'pts')],
             ['sudo', 'mkdir', os.path.join(m, 'sys')]])

    def test_build_leaves_existing_mount_points_alone(self):
        for sub in ('proc', 'dev', os.path.join('dev', 'pts'), 'sys'):
            os.makedirs(os.path.join(self.mnt, sub))

        self.assertTrue(self.mount.build({}))

        self.assertEqual(
            [c for c in self.sub.commands() if c[:2] == ['sudo', 'mkdir']], [])

    def test_build_does_not_clean_up_after_success(self):
        self.mount.build({})

        self.assertEqual(self.sub.count('sudo', 'umount'), 0)
        self.assertEqual(self.sub.count('sudo', 'losetup', '-d'), 0)


class MountFailureTests(MountTestCase):

    def _assert_cleaned_up(self):
        self.assertEqual(self.sub.commands()[-6:], self._cleanup_commands())
        self.assertNotIn('loopbackDevice', self.dibenv)
        self.assertEqual(self.dibenv['shellenv'].get('CSMAKE_IMAGE_BLOCK_DISK'), None)
        self.assertEqual(self.dibenv['shellenv'].get('IMAGE_BLOCK_DEVICE'), None)

    def test_build_refuses_an_image_that_was_never_prepped(self):
        del self.dibenv['partitions']

        self.assertIsNone(self.mount.build({}))

        self.assertEqual(self.mount.log.result, 'failed')
        self.assertTrue(self.mount.log.mentions('error', 'cannot be mounted until the image is prepped'))
        self.assertEqual(self.sub.commands(), [])

    def test_build_fails_when_the_loop_device_cannot_be_attached(self):
        self.sub.on(['sudo', 'losetup', '--show'], rc=1,
                    stderr=u'losetup: cannot find an unused loop device\n',
                    first=True)

        self.assertIsNone(self.mount.build({}))

        self.assertEqual(self.mount.log.result, 'failed')
        self.assertTrue(self.mount.log.mentions('error', 'cannot find an unused loop device'))
        self.assertEqual(len(self.sub.commands()), 1)
        self.assertNotIn('loopbackDevice', self.dibenv)

    def test_build_fails_and_cleans_up_when_mount_point_is_a_file(self):
        with open(self.mnt, 'w') as f:
            f.write('not a directory')

        self.assertIsNone(self.mount.build({}))

        self.assertEqual(self.mount.log.result, 'failed')
        self.assertTrue(self.mount.log.mentions('error', 'is not a directory'))
        self._assert_cleaned_up()

    def test_build_fails_and_cleans_up_when_the_root_mount_fails(self):
        self.sub.on(['sudo', 'mount', LOOP + 'p1'], rc=32, first=True)

        self.assertIsNone(self.mount.build({}))

        self.assertEqual(self.mount.log.result, 'failed')
        self.assertTrue(self.mount.log.mentions('error', 'Mount failed'))
        self.assertEqual(len(self._mounts()), 1)
        self._assert_cleaned_up()

    def test_build_fails_and_cleans_up_when_the_proc_mount_point_cannot_be_made(self):
        self.sub.on(['sudo', 'mkdir'], rc=1, first=True)

        self.assertIsNone(self.mount.build({}))

        self.assertTrue(self.mount.log.mentions('error', 'Create of procfs mount point failed'))
        self._assert_cleaned_up()

    def test_build_fails_and_cleans_up_when_procfs_cannot_be_mounted(self):
        self.sub.on(['sudo', 'mount', '-t', 'proc'], rc=1, first=True)

        self.assertIsNone(self.mount.build({}))

        self.assertTrue(self.mount.log.mentions('error', 'Mount of procfs failed'))
        self._assert_cleaned_up()

    def test_build_fails_and_cleans_up_when_the_dev_mount_point_cannot_be_made(self):
        self.sub.on(['sudo', 'mkdir', os.path.join(self.mnt, 'dev')], rc=1, first=True)

        self.assertIsNone(self.mount.build({}))

        self.assertTrue(self.mount.log.mentions('error', 'Create of dev mount point failed'))
        self._assert_cleaned_up()

    def test_build_fails_and_cleans_up_when_dev_cannot_be_bind_mounted(self):
        self.sub.on(['sudo', 'mount', '--bind', '/dev', os.path.join(self.mnt, 'dev')],
                    rc=32, first=True)

        self.assertIsNone(self.mount.build({}))

        self.assertTrue(self.mount.log.mentions('error', 'Mount of dev failed'))
        self._assert_cleaned_up()

    def test_build_fails_and_cleans_up_when_the_pts_mount_point_cannot_be_made(self):
        self.sub.on(['sudo', 'mkdir', os.path.join(self.mnt, 'dev', 'pts')], rc=1, first=True)

        self.assertIsNone(self.mount.build({}))

        self.assertTrue(self.mount.log.mentions('error', 'Create of the pts mount point failed'))
        self._assert_cleaned_up()

    def test_build_fails_and_cleans_up_when_dev_pts_cannot_be_bind_mounted(self):
        self.sub.on(['sudo', 'mount', '--bind', '/dev/pts'], rc=32, first=True)

        self.assertIsNone(self.mount.build({}))

        self.assertTrue(self.mount.log.mentions('error', 'Mount of dev/pts failed'))
        self._assert_cleaned_up()

    def test_build_fails_and_cleans_up_when_the_sys_mount_point_cannot_be_made(self):
        self.sub.on(['sudo', 'mkdir', os.path.join(self.mnt, 'sys')], rc=1, first=True)

        self.assertIsNone(self.mount.build({}))

        self.assertTrue(self.mount.log.mentions('error', 'Create of the sys mount point failed'))
        self._assert_cleaned_up()

    def test_build_fails_and_cleans_up_when_sysfs_cannot_be_mounted(self):
        self.sub.on(['sudo', 'mount', '-t', 'sysfs'], rc=1, first=True)

        self.assertIsNone(self.mount.build({}))

        self.assertTrue(self.mount.log.mentions('error', 'Mount of sysfs failed'))
        self.assertEqual(len(self._mounts()), 5)
        self._assert_cleaned_up()


class MountCleanTests(MountTestCase):

    def test_clean_unmounts_in_reverse_order_and_detaches_the_loop_device(self):
        self.dibenv['shellenv'] = {'CSMAKE_IMAGE_BLOCK_DISK': LOOP,
                                   'IMAGE_BLOCK_DEVICE': LOOP + 'p1'}
        self.dibenv['loopbackDevice'] = LOOP

        self.assertTrue(self.mount.clean({}))

        self.assertEqual(self.sub.commands(), self._cleanup_commands())
        self.assertEqual(self.dibenv['shellenv'], {})
        self.assertNotIn('loopbackDevice', self.dibenv)
        self.assertEqual(self.mount.log.result, 'passed')

    def test_clean_skips_detaching_when_no_loop_device_was_recorded(self):
        self.assertTrue(self.mount.clean({}))

        self.assertEqual(self.sub.commands(), self._cleanup_commands()[:-1])

    def test_clean_only_warns_when_unmounts_fail(self):
        self.sub.default_rc = 1

        self.assertTrue(self.mount.clean({}))

        warnings = self.mount.log.messages('warning')
        self.assertEqual(len(warnings), 5)
        self.assertIn('Unmount of sysfs failed', warnings[0])
        self.assertIn('Unmount of image failed', warnings[4])
        self.assertEqual(self.mount.log.result, 'passed')


class UmountBuildTests(MountTestCase):

    def setUp(self):
        MountTestCase.setUp(self)
        self.umount = make_module(
            umountmod.DIBUmountImage, env={'__DIBEnv__': self.dibenv})
        self.statvfs = types.SimpleNamespace(
            f_frsize=4096, f_blocks=1000, f_bsize=4096, f_bfree=250)

    def test_build_records_used_size_and_unmounts(self):
        self.umount.yieldsfiles = [{}]
        with mock.patch('os.statvfs', return_value=self.statvfs) as statvfs:
            self.assertTrue(self.umount.build({}))

        statvfs.assert_called_once_with(self.mnt)
        self.assertEqual(self.umount.yieldsfiles[0]['used-size'], 4096 * 750)
        self.assertEqual(self.sub.commands(), self._cleanup_commands()[:-1])
        self.assertEqual(self.umount.log.result, 'passed')

    def test_build_without_yielded_files_still_unmounts(self):
        self.umount.yieldsfiles = None
        with mock.patch('os.statvfs', return_value=self.statvfs):
            self.assertTrue(self.umount.build({}))

        self.assertEqual(len(self.sub.commands()), 5)

    def test_build_propagates_a_missing_mount_point(self):
        with mock.patch('os.statvfs', side_effect=OSError(2, 'No such file')):
            with self.assertRaises(OSError):
                self.umount.build({})

        self.assertEqual(self.sub.commands(), [])


if __name__ == '__main__':
    unittest.main()
