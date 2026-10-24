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
"""DIBTemporaryFileHack with ``subprocess`` mocked.

Python-3 conversion risks covered: oct() now carries a two character '0o'
prefix (the old ``[1:]`` slice produced the unusable mode 'o640'), file() was
replaced by open(), and ``patch`` is fed text through a pipe.  ``sudo chmod``
is a scripted boundary whose effect applies the requested mode to the real
temp file (and, like chmod(1), rejects a non-octal mode such as 'o640').
"""
import errno
import os
import subprocess
import unittest
from unittest import mock

from tests import dibsupport
from tests.dibsupport import FakeSubprocess, file_mode, make_module

hackmod = dibsupport.import_dib('DIBTemporaryFileHack')


def chmod_effect(invocation):
    """Simulate ``sudo chmod <octal> <path>`` on a real path."""
    mode, path = invocation.args[2], invocation.args[3]
    try:
        os.chmod(path, int(mode, 8))
    except ValueError:
        return 1  # chmod: invalid mode
    return 0


class HackTestCase(unittest.TestCase):

    def setUp(self):
        self.sub = FakeSubprocess()
        self.sub.on(['sudo', 'chmod'], effect=chmod_effect)
        self.sub.install(self)
        self.root = dibsupport.make_tmpdir(self)
        self.hack = make_module(hackmod.DIBTemporaryFileHack)

    def _workdir(self, name='etc', mode=0o750):
        path = os.path.join(self.root, name)
        os.mkdir(path)
        os.chmod(path, mode)
        return path

    def _file(self, workdir, name='app.conf', text='original\n', mode=0o640):
        path = os.path.join(workdir, name)
        with open(path, 'w') as f:
            f.write(text)
        os.chmod(path, mode)
        return path

    def _start(self, **options):
        return self.hack.script_start__build('build', options, None, None)

    def _end(self, **options):
        return self.hack.script_end__build('build', options, None, None)

    def _read(self, path):
        with open(path) as f:
            return f.read()


class ModeHandlingTests(HackTestCase):

    def test_start_saves_modes_as_bare_octal_digits(self):
        workdir = self._workdir(mode=0o750)
        target = self._file(workdir, mode=0o640)

        self.assertTrue(self._start(file=target, append='x\n'))

        self.assertEqual(self.hack.realFileMode, '640')
        self.assertEqual(self.hack.realDirMode, '750')
        self.assertEqual(self.sub.commands(), [
            ['sudo', 'chmod', '777', workdir],
            ['sudo', 'chmod', '777', target]])
        self.assertEqual(file_mode(target), 0o777)
        self.assertEqual(file_mode(workdir), 0o777)

    def test_start_saves_single_digit_mode_without_prefix(self):
        workdir = self._workdir()
        target = self._file(workdir, mode=0o007)

        self.assertTrue(self._start(file=target))

        self.assertEqual(self.hack.realFileMode, '7')

    def test_end_restores_content_and_original_modes(self):
        workdir = self._workdir(mode=0o750)
        target = self._file(workdir, text='original\n', mode=0o604)
        self._start(file=target, append='added\n')
        self.sub.invocations[:] = []

        self.assertTrue(self._end())

        self.assertEqual(self.sub.commands(), [
            ['sudo', 'chmod', '604', target],
            ['sudo', 'chmod', '750', workdir]])
        self.assertEqual(file_mode(target), 0o604)
        self.assertEqual(file_mode(workdir), 0o750)
        self.assertEqual(self._read(target), 'original\n')
        self.assertFalse(os.path.exists(self.hack.tempFile))
        self.assertEqual(self.hack.log.messages('error'), [])
        self.assertEqual(self.hack.log.result, 'passed')

    def test_start_fails_when_directory_cannot_be_made_permissive(self):
        workdir = self._workdir()
        target = self._file(workdir)
        self.sub.on(
            ['sudo', 'chmod', '777', workdir], rc=1, times=1, first=True)

        self.assertIsNone(self._start(file=target, append='x\n'))

        self.assertEqual(self.hack.log.result, 'failed')
        self.assertIsNone(self.hack.dirpath)
        self.assertTrue(self.hack.log.mentions('error', 'Could not set permissive'))
        self.assertEqual(self._read(target), 'original\n')

    def test_start_only_warns_when_target_file_mode_cannot_be_changed(self):
        workdir = self._workdir()
        target = self._file(workdir)
        self.sub.on(
            ['sudo', 'chmod', '777', target], rc=1, times=1, first=True)

        self.assertTrue(self._start(file=target, append='x\n'))

        self.assertTrue(self.hack.log.mentions('warning', 'change the mode'))
        self.assertEqual(self.hack.log.result, 'passed')

    def test_set_file_mode_reports_failure(self):
        sub = self.sub
        sub.on(['sudo', 'chmod', '640'], rc=1, first=True)

        self.assertFalse(self.hack._setFileMode('/x/y', '640'))

        self.assertTrue(self.hack.log.mentions('error', "Could not set mode (640) on '/x/y'"))

    def test_end_logs_security_error_when_file_mode_cannot_be_restored(self):
        workdir = self._workdir()
        target = self._file(workdir)
        self._start(file=target, append='x\n')
        self.sub.on(
            ['sudo', 'chmod', '640', target], rc=1, first=True)

        self.assertTrue(self._end())

        self.assertTrue(self.hack.log.mentions('error', 'Could not reset mode for the target file - SECURITY'))

    def test_end_warns_when_directory_mode_cannot_be_restored(self):
        workdir = self._workdir()
        target = self._file(workdir)
        self._start(file=target, append='x\n')
        self.sub.on(
            ['sudo', 'chmod', '750', workdir], rc=1, first=True)

        self.assertTrue(self._end())

        self.assertTrue(self.hack.log.mentions('warning', 'Could not reset mode for the target file directory'))


class StartTests(HackTestCase):

    def test_start_skips_everything_when_temp_file_already_exists(self):
        workdir = self._workdir()
        target = self._file(workdir)
        self._file(workdir, name='app.conf.csmake-DIBTemporaryFileHack-save',
                   text='saved\n')

        self.assertIsNone(self._start(file=target, append='x\n'))

        self.assertEqual(self.hack.log.result, 'passed')
        self.assertTrue(self.hack.log.mentions('warning', 'already applied'))
        self.assertEqual(self._read(target), 'original\n')
        self.assertEqual(self.sub.commands(), [])

    def test_start_honours_the_temp_file_option(self):
        workdir = self._workdir()
        target = self._file(workdir, text='original\n')
        saved = os.path.join(self.root, 'elsewhere.save')

        self.assertTrue(self._start(**{'file': target, 'temp-file': saved,
                                       'append': 'x\n'}))

        self.assertEqual(self._read(saved), 'original\n')
        self.assertEqual(self._read(target), 'original\nx\n')

    def test_start_creates_missing_file_and_directory_then_end_removes_them(self):
        base = self._workdir('base', mode=0o755)
        self._file(base, name='sentinel')  # keeps base from being pruned
        target = os.path.join(base, 'newdir', 'fresh.conf')

        self.assertTrue(self._start(file=target, replace='fresh\n'))
        self.assertEqual(self._read(target), 'fresh\n')
        self.assertEqual(self.hack.realDirMode, '755')

        self.assertTrue(self._end())
        self.assertFalse(os.path.exists(os.path.join(base, 'newdir')))
        self.assertEqual(self.sub.commands()[-1], ['sudo', 'chmod', '755', base])
        self.assertEqual(file_mode(base), 0o755)

    @unittest.skip("pre-existing bug (not a 2/3 issue): _removeCreatedPath's "
                   "os.removedirs() fails with 'Directory not empty' for a "
                   "multi-level created path and can prune empty parents")
    def test_end_removes_multi_level_created_directories(self):
        pass

    def test_start_for_bare_filename_uses_current_directory(self):
        previous = os.getcwd()
        workdir = self._workdir(mode=0o755)
        os.chdir(workdir)
        self.addCleanup(os.chdir, previous)

        self.assertTrue(self._start(file='local.conf', replace='x\n'))

        self.assertEqual(self.hack.dirpath, '.')
        self.assertEqual(self.hack.realDirMode, '755')
        self.assertEqual(self._read('local.conf'), 'x\n')

    def test_start_replace_file_copies_the_substituted_path(self):
        workdir = self._workdir()
        target = self._file(workdir)
        self._file(self.root, name='new.conf', text='from file\n')
        self.hack.env.substitutions = {'%(SRC)s': self.root}

        self.assertTrue(self._start(**{'file': target,
                                       'replace-file': '%(SRC)s/new.conf'}))

        self.assertEqual(self._read(target), 'from file\n')

    def test_start_appends_inline_text_then_file_lines(self):
        workdir = self._workdir()
        target = self._file(workdir, text='base\n')
        extra = self._file(self.root, name='extra.txt', text='line a\nline b\n')

        self.assertTrue(self._start(**{'file': target, 'append-file': extra,
                                       'append': 'inline\n'}))

        self.assertEqual(self._read(target),
                         'base\ninline\nline a\nline b\n')

    def test_start_replaces_non_ascii_text(self):
        workdir = self._workdir()
        target = self._file(workdir)
        text = u'café ☃\n'

        self.assertTrue(self._start(file=target, replace=text))

        with open(target, 'rb') as f:
            self.assertEqual(f.read(), text.encode('utf-8'))

    def test_filter_line_pad_removes_pad_after_each_newline(self):
        text = '\n|alpha\n|beta\n'
        self.assertEqual(
            self.hack._filterLinePad({'line-pad': ' | '}, text),
            '\nalpha\nbeta\n')

    def test_filter_line_pad_without_option_is_identity(self):
        self.assertEqual(self.hack._filterLinePad({}, '\n|a\n'), '\n|a\n')


class PatchTests(HackTestCase):
    PATCH = ('\n|--- a/app.conf\n|+++ b/app.conf\n|@@ -2,3 +2,3 @@\n'
             '| alpha\n|-beta\n|+BETA\n| gamma\n')
    DIFF = ('\n--- a/app.conf\n+++ b/app.conf\n@@ -2,3 +2,3 @@\n'
            ' alpha\n-beta\n+BETA\n gamma\n')

    def setUp(self):
        HackTestCase.setUp(self)
        workdir = self._workdir()
        self.target = self._file(workdir, text='old\n')
        self.hack.targetFile = self.target
        self.seen_at_patch = []

        def record_target(invocation):
            self.seen_at_patch.append(self._read(self.target))
            return 0
        self.sub.on(['patch'], effect=record_target)

    def test_apply_patch_runs_patch_on_the_target_in_text_mode(self):
        self.hack._applyPatch({'patch': self.PATCH, 'line-pad': '|'})

        popens = self.sub.commands('Popen')
        self.assertEqual(popens, [['patch', self.target]])
        kwargs = self.sub.invocations[0].kwargs
        self.assertIs(kwargs['universal_newlines'], True)
        self.assertEqual(kwargs['stdin'], subprocess.PIPE)
        self.assertNotEqual(self.hack.log.result, 'failed')

    def test_apply_patch_writes_the_unpadded_diff_to_stdin_and_closes_it(self):
        self.hack._applyPatch({'patch': self.PATCH, 'line-pad': '|'})

        stdin = self.sub.processes[0].stdin
        self.assertEqual(stdin.written(), self.DIFF)
        self.assertTrue(stdin.closed)

    def test_apply_patch_failure_is_reported(self):
        self.sub.on(['patch'], rc=1, first=True)

        self.assertFalse(
            self.hack._applyPatch({'patch': self.PATCH, 'line-pad': '|'}))

        self.assertEqual(self.hack.log.result, 'failed')
        self.assertTrue(self.hack.log.mentions('error', 'File was not patched'))

    def test_start_replaces_before_patching_and_appends_after(self):
        self.assertTrue(self._start(**{
            'file': self.target, 'line-pad': '|',
            'replace': '\n|alpha\n|beta\n|gamma\n',
            'patch': self.PATCH, 'append': 'tail\n'}))

        # patch ran against the replaced content, before the append
        self.assertEqual(self.seen_at_patch, ['\nalpha\nbeta\ngamma\n'])
        self.assertEqual(self._read(self.target),
                         '\nalpha\nbeta\ngamma\ntail\n')


class CreatedPathTests(HackTestCase):

    def test_remove_created_path_does_nothing_when_nothing_was_created(self):
        self.hack.createdParts = []

        self.assertFalse(self.hack._removeCreatedPath())

    def test_remove_created_path_removes_the_first_created_directory(self):
        base = self._workdir('base')
        self._file(base, name='sentinel')
        os.mkdir(os.path.join(base, 'made'))
        self.hack.dirpath = base
        self.hack.createdParts = ['made']

        self.assertTrue(self.hack._removeCreatedPath())

        self.assertFalse(os.path.exists(os.path.join(base, 'made')))


class EndTests(HackTestCase):

    def test_end_fails_when_start_never_found_a_directory(self):
        self.hack.dirpath = None

        self.assertFalse(self._end())

        self.assertEqual(self.hack.log.result, 'failed')
        self.assertTrue(self.hack.log.mentions('error', 'Directory for target file does not exist'))

    def test_end_keeps_the_changes_when_asked_to(self):
        workdir = self._workdir()
        target = self._file(workdir)
        self._start(file=target, append='kept\n')
        self.sub.invocations[:] = []

        self.assertTrue(self._end(keepChanges='True'))

        self.assertEqual(self._read(target), 'original\nkept\n')
        self.assertEqual(self.sub.commands(), [])
        self.assertEqual(self.hack.log.result, 'passed')

    def test_end_warns_but_restores_when_hacked_file_is_already_gone(self):
        workdir = self._workdir()
        target = self._file(workdir)
        self._start(file=target, append='x\n')
        os.remove(target)

        self.assertTrue(self._end())

        self.assertTrue(self.hack.log.mentions('warning', 'Removal of hacked file failed'))
        self.assertEqual(self._read(target), 'original\n')

    def test_end_warns_when_the_created_directory_cannot_be_removed(self):
        base = self._workdir('base')
        self._file(base, name='sentinel')
        target = os.path.join(base, 'newdir', 'fresh.conf')
        self._start(file=target, replace='x\n')

        with mock.patch('os.removedirs', side_effect=OSError(errno.ENOTEMPTY, 'busy')):
            self.assertTrue(self._end())

        self.assertTrue(self.hack.log.mentions('warning', 'Could not remove created path'))

    def test_start_accepts_patch_file_which_is_not_implemented(self):
        workdir = self._workdir()
        target = self._file(workdir)

        self.assertTrue(self._start(**{'file': target, 'patch-file': '/some/patch'}))

        self.assertEqual(self._read(target), 'original\n')

    def test_end_reports_failure_when_the_restore_raises(self):
        workdir = self._workdir()
        target = self._file(workdir)
        self._start(file=target, append='x\n')

        with mock.patch('os.rename', side_effect=OSError('read-only')):
            self._end()

        self.assertEqual(self.hack.log.result, 'failed')
        self.assertTrue(self.hack.log.mentions('exception', 'Failed to restore files'))


if __name__ == '__main__':
    unittest.main()
