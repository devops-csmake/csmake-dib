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
"""DIBPrepDisk with ``subprocess`` mocked.

Python-3 conversion risks covered: ``dict.keys().sort()`` (a view has no
sort) became ``sorted()``, and ``losetup --show`` output read through
``communicate()`` must be text (``universal_newlines``) so it can be
concatenated with the partition suffix ('p1').  The fake Popen returns bytes
unless text mode was requested, exactly like the real one.
"""
import unittest

from tests import dibsupport
from tests.dibsupport import FakeSubprocess, make_module

prepmod = dibsupport.import_dib('DIBPrepDisk')

IMAGE = '/build/image'
LOOP = '/dev/loop7'
PARTED = ['sudo', 'parted', '-s', '-a', 'optimal', LOOP, '--']


class PrepDiskTestCase(unittest.TestCase):

    def setUp(self):
        self.sub = FakeSubprocess(default_rc=0)
        self.sub.on(['sudo', 'losetup', '--show', '-f'], stdout=LOOP + '\n')
        self.sub.install(self)

    def _prep(self, shellenv=None):
        self.dibenv = {'imagedir': IMAGE,
                       'shellenv': {'DIB_ROOT_LABEL': 'rootfs'}
                       if shellenv is None else shellenv}
        self.prep = make_module(
            prepmod.DIBPrepDisk, env={'__DIBEnv__': self.dibenv})
        return self.prep

    OPTIONS = {'disk-size': '10G',
               # deliberately not in part_N order
               'part_3': '1G, swap, swap0',
               'part_1': '5G, ext4, rootfs',
               'options_1': '-q',
               'part_2': '4G, ext4, data'}

    def _build(self, options=None, shellenv=None):
        prep = self._prep(shellenv)
        return prep, prep.build(dict(self.OPTIONS if options is None else options))

    def _mkparts(self):
        return [cmd[len(PARTED):] for cmd in self.sub.commands()
                if cmd[:len(PARTED)] == PARTED and 'mkpart' in cmd]


class BuildSuccessTests(PrepDiskTestCase):

    def test_build_removes_old_image_then_truncates_to_requested_size(self):
        prep, _ = self._build()

        self.assertEqual(self.sub.commands()[:2], [
            ['sudo', 'rm', '-rf', IMAGE],
            ['truncate', '-s', '10G', IMAGE]])
        self.assertEqual(prep.log.result, 'passed')

    def test_build_partitions_in_part_number_order_not_option_order(self):
        self._build()

        self.assertEqual(self._mkparts(), [
            ['mkpart', 'primary', 'ext4', '0%', '50%', 'set', '1', 'boot', 'on'],
            ['mkpart', 'primary', 'ext4', '50%', '90%'],
            ['mkpart', 'primary', 'swap', '90%', '100%', 'set', '3', 'swap', 'on']])

    def test_build_labels_the_disk_msdos_before_partitioning(self):
        self._build()

        commands = self.sub.commands()
        self.assertEqual(commands[3], ['sudo', 'parted', '-s', LOOP, '--', 'mklabel', 'msdos'])
        self.assertTrue(all('mkpart' not in c for c in commands[:4]))

    def test_build_records_partition_table_in_the_dib_environment(self):
        self._build()

        partitions = self.dibenv['partitions']
        self.assertEqual([partitions[p]['lopart'] for p in ('part_1', 'part_2', 'part_3')],
                         ['p1', 'p2', 'p3'])
        self.assertTrue(partitions['part_1']['root'])
        self.assertNotIn('root', partitions['part_2'])
        self.assertEqual(partitions['part_1']['options'], '-q')
        self.assertEqual(partitions['part_2']['options'], '')

    def test_build_reads_loop_device_as_text(self):
        self._build()

        losetup = [i for i in self.sub.invocations
                   if i.args[:3] == ['sudo', 'losetup', '--show']]
        self.assertEqual(len(losetup), 1)
        self.assertEqual(losetup[0].func, 'Popen')
        self.assertTrue(losetup[0].kwargs['universal_newlines'])
        self.assertEqual(losetup[0].args, ['sudo', 'losetup', '--show', '-f', IMAGE])

    def test_build_makes_filesystems_on_loop_device_plus_partition_suffix(self):
        self._build()

        mkfs = [c for c in self.sub.commands() if c[:2] == ['sudo', 'mkfs']]
        self.assertEqual(sorted(mkfs), [
            ['sudo', 'mkfs', '', '-t', 'ext4', '-L', 'data', LOOP + 'p2'],
            ['sudo', 'mkfs', '-q', '-t', 'ext4', '-L', 'rootfs', LOOP + 'p1']])

    def test_build_settles_udev_and_detaches_the_loop_device_last(self):
        self._build()

        commands = self.sub.commands()
        self.assertIn(['sudo', 'partprobe', LOOP], commands)
        self.assertIn(['sudo', 'udevadm', 'settle'], commands)
        self.assertEqual(commands[-1], ['sudo', 'losetup', '-d', LOOP])

    def test_build_uses_root_label_from_the_shell_environment(self):
        self._build(shellenv={'DIB_ROOT_LABEL': 'data'})

        self.assertTrue(self.dibenv['partitions']['part_2']['root'])
        mkparts = self._mkparts()
        self.assertEqual(mkparts[0], ['mkpart', 'primary', 'ext4', '0%', '50%'])
        self.assertEqual(mkparts[1], ['mkpart', 'primary', 'ext4', '50%', '90%',
                                      'set', '2', 'boot', 'on'])

    def test_build_truncates_a_partition_that_overruns_the_disk(self):
        prep, _ = self._build(options={
            'disk-size': '10G', 'part_1': '8G, ext4, rootfs',
            'part_2': '4G, ext4, data'})

        self.assertEqual(self._mkparts()[1][:5],
                         ['mkpart', 'primary', 'ext4', '80%', '100%'])
        self.assertTrue(prep.log.mentions('warning', 'Last partition was truncated'))


class BuildFailureTests(PrepDiskTestCase):

    def test_build_fails_when_the_image_cannot_be_created(self):
        self.sub.on(['truncate'], rc=1, first=True)

        prep, result = self._build()

        self.assertIsNone(result)
        self.assertEqual(prep.log.result, 'failed')
        self.assertTrue(prep.log.mentions('error', 'Creating the image failed'))
        self.assertEqual(self.sub.count('sudo', 'losetup'), 0)

    def test_build_fails_when_no_loop_device_can_be_attached(self):
        self.sub.on(['sudo', 'losetup', '--show'], rc=1,
                    stderr=u'losetup: no free loop devices\n', first=True)

        prep, result = self._build()

        self.assertIsNone(result)
        self.assertEqual(prep.log.result, 'failed')
        self.assertTrue(prep.log.mentions('error', 'Creating loopback device for image failed'))
        self.assertTrue(prep.log.mentions('error', 'losetup: no free loop devices'))
        # nothing was attached, so nothing is detached
        self.assertNotIn(['sudo', 'losetup', '-d', LOOP], self.sub.commands())
        self.assertNotIn('partitions', self.dibenv)

    def test_build_fails_and_detaches_when_partition_table_cannot_be_made(self):
        self.sub.on(['sudo', 'parted', '-s', LOOP, '--', 'mklabel'], rc=1, first=True)

        prep, result = self._build()

        self.assertIsNone(result)
        self.assertEqual(prep.log.result, 'failed')
        self.assertTrue(prep.log.mentions('error', 'Creating the partition table failed'))
        self.assertEqual(self.sub.commands()[-1], ['sudo', 'losetup', '-d', LOOP])

    def test_build_fails_and_detaches_when_a_partition_cannot_be_made(self):
        self.sub.on(lambda a: a[:len(PARTED)] == PARTED and '50%' in a and '90%' in a,
                    rc=1, first=True)

        prep, result = self._build()

        self.assertIsNone(result)
        self.assertEqual(prep.log.result, 'failed')
        self.assertTrue(prep.log.mentions('error', "Partition 'part_2' failed"))
        self.assertEqual(self.sub.commands()[-1], ['sudo', 'losetup', '-d', LOOP])
        self.assertEqual(self.sub.count('sudo', 'mkfs'), 0)

    def test_build_fails_when_partitions_exceed_the_disk(self):
        prep, result = self._build(options={
            'disk-size': '10G', 'part_1': '10G, ext4, rootfs',
            'part_2': '1G, ext4, data'})

        self.assertIsNone(result)
        self.assertEqual(prep.log.result, 'failed')
        self.assertTrue(prep.log.mentions('error', 'larger than the disk'))
        self.assertEqual(len(self._mkparts()), 1)

    def test_build_only_warns_when_probe_or_settle_fail(self):
        self.sub.on(['sudo', 'partprobe'], rc=1, first=True)
        self.sub.on(['sudo', 'udevadm'], rc=1, first=True)

        prep, _ = self._build()

        self.assertEqual(prep.log.result, 'passed')
        self.assertTrue(prep.log.mentions('warning', 'Part probe failed'))
        self.assertTrue(prep.log.mentions('warning', 'settle failed'))

    def test_build_logs_an_error_when_no_partition_has_the_root_label(self):
        prep, _ = self._build(shellenv={'DIB_ROOT_LABEL': 'nonexistent'})

        self.assertTrue(prep.log.mentions('error', 'The root partition was not found'))
        self.assertTrue(all('boot' not in cmd for cmd in self._mkparts()))

    @unittest.skip("pre-existing bug (not a 2/3 issue): with DIB_ROOT_LABEL "
                   "unset, build() reads partitions['1'] but the keys are "
                   "'part_N', so it raises KeyError")
    def test_build_defaults_the_root_label_from_the_first_partition(self):
        pass


class HelperTests(PrepDiskTestCase):

    def test_get_size_in_bytes_understands_units(self):
        prep = self._prep()
        self.assertEqual(
            [prep._getSizeInBytes(s) for s in ('7', '2K', '3M', '4G')],
            [7, 2048, 3 * 1024 ** 2, 4 * 1024 ** 3])

    @unittest.skip("pre-existing bug (not a 2/3 issue): _getSizeInBytes' "
                   "error path logs with undefined names partcount/part")
    def test_get_size_in_bytes_rejects_unparsable_sizes(self):
        pass

    def test_grock_partitions_splits_fields_and_pairs_mkfs_options(self):
        prep = self._prep()

        partitions = prep._grockPartitions({
            'disk-size': '1G', 'part_1': ' 1G ,ext4 , root ', 'options_1': '-O x',
            'part_2': '2G, xfs, data'})

        self.assertEqual(partitions, {
            'part_1': {'size': '1G', 'fs': 'ext4', 'label': 'root', 'options': '-O x'},
            'part_2': {'size': '2G', 'fs': 'xfs', 'label': 'data', 'options': ''}})

    def test_clean_removes_the_image(self):
        prep = self._prep()

        self.assertTrue(prep.clean({}))

        self.assertEqual(self.sub.commands(), [['sudo', 'rm', '-rf', IMAGE]])
        self.assertEqual(prep.log.result, 'passed')


if __name__ == '__main__':
    unittest.main()
