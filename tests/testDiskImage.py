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
"""DIBPrepDisk and DIBMountImage against fake disk tools.

Python-3 conversion risks covered: ``dict.keys().sort()`` (a view has no
sort) became ``sorted()``, and ``losetup --show`` output read through
``communicate()`` must be text so it can be concatenated with the partition
suffix ('p1').  No loop devices are touched: ``sudo`` runs its arguments and
``losetup``/``parted``/``mkfs``/``mount`` are scripts that record their argv.
"""
import os
import unittest

from tests import dibsupport
from tests.dibsupport import calls

prepmod = dibsupport.import_dib('DIBPrepDisk')
mountmod = dibsupport.import_dib('DIBMountImage')

NOOP = ':\n'
DISK_TOOLS = {
    'truncate': ': > "$3"\n',
    'losetup': 'case "$1" in --show) echo /dev/loop99;; esac\n',
    'parted': NOOP, 'partprobe': NOOP, 'udevadm': NOOP, 'mkfs': NOOP,
    'mount': NOOP, 'umount': NOOP,
}


class DiskImageTests(unittest.TestCase):

    def setUp(self):
        _, self.logpath = dibsupport.install_fake_tools(self, DISK_TOOLS)
        self.root = dibsupport.make_tmpdir(self)

    def test_prepdisk_partitions_in_sorted_order_on_text_loop_device(self):
        dibenv = {'imagedir': os.path.join(self.root, 'disk.img'),
                  'shellenv': {'DIB_ROOT_LABEL': 'rootfs'}}
        prep = dibsupport.make_module(
            prepmod.DIBPrepDisk, env={'__DIBEnv__': dibenv})
        # deliberately not in part_N order
        options = {'disk-size': '10G',
                   'part_3': '1G, swap, swap0',
                   'part_1': '5G, ext4, rootfs',
                   'options_1': '-q',
                   'part_2': '4G, ext4, data'}

        prep.build(options)

        self.assertEqual(prep.log.result, 'passed')
        self.assertEqual(
            calls(self.logpath, 'truncate'),
            [('truncate', ['-s', '10G', dibenv['imagedir']])])
        mkparts = [args[args.index('mkpart'):]
                   for _, args in calls(self.logpath, 'parted')
                   if 'mkpart' in args]
        self.assertEqual(mkparts, [
            ['mkpart', 'primary', 'ext4', '0%', '50%', 'set', '1', 'boot', 'on'],
            ['mkpart', 'primary', 'ext4', '50%', '90%'],
            ['mkpart', 'primary', 'swap', '90%', '100%', 'set', '3', 'swap', 'on'],
        ])
        partitions = dibenv['partitions']
        self.assertEqual(
            [partitions[p]['lopart'] for p in ('part_1', 'part_2', 'part_3')],
            ['p1', 'p2', 'p3'])
        self.assertTrue(partitions['part_1']['root'])
        # filesystems go on <loop device><partition suffix>; swap is skipped
        mkfs = sorted(args for _, args in calls(self.logpath, 'mkfs'))
        self.assertEqual(mkfs, [
            ['', '-t', 'ext4', '-L', 'data', '/dev/loop99p2'],
            ['-q', '-t', 'ext4', '-L', 'rootfs', '/dev/loop99p1']])
        self.assertIn(('losetup', ['-d', '/dev/loop99']),
                      calls(self.logpath, 'losetup'))

    def test_mountimage_mounts_root_partition_on_text_loop_device(self):
        builddir = os.path.join(self.root, 'build')
        os.mkdir(builddir)
        dibenv = {
            'imagedir': os.path.join(self.root, 'disk.img'),
            'build-dir': builddir,
            'shellenv': {},
            'partitions': {
                'part_1': {'label': 'rootfs', 'lopart': 'p1', 'root': True},
                'part_2': {'label': 'data', 'lopart': 'p2'}}}
        mount = dibsupport.make_module(
            mountmod.DIBMountImage, env={'__DIBEnv__': dibenv})

        self.assertTrue(mount.build({}))

        self.assertEqual(mount.log.result, 'passed')
        self.assertEqual(dibenv['loopbackDevice'], '/dev/loop99')
        shellenv = dibenv['shellenv']
        self.assertEqual(shellenv['CSMAKE_IMAGE_BLOCK_DISK'], '/dev/loop99')
        self.assertEqual(shellenv['IMAGE_BLOCK_DEVICE'], '/dev/loop99p1')
        self.assertEqual(shellenv['TMP_BUILD_DIR'], builddir)
        mountpath = os.path.join(builddir, 'mnt')
        self.assertEqual(
            [args for _, args in calls(self.logpath, 'mount')],
            [['/dev/loop99p1', mountpath],
             ['-t', 'proc', 'none', os.path.join(mountpath, 'proc')],
             ['--bind', '/dev', os.path.join(mountpath, 'dev')],
             ['--bind', '/dev/pts', os.path.join(mountpath, 'dev', 'pts')],
             ['-t', 'sysfs', 'none', os.path.join(mountpath, 'sys')]])


if __name__ == '__main__':
    unittest.main()
