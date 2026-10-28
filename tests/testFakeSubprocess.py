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
"""The subprocess fake must honour the real API's type contract.

If it handed back str regardless of the arguments, the module tests could no
longer catch a Python 2 -> 3 bytes/str bug.  These tests pin the fake to the
behaviour of the real subprocess module.
"""
import io
import subprocess
import unittest

from tests.dibsupport import FakeSubprocess


class FakeSubprocessContractTests(unittest.TestCase):

    def setUp(self):
        self.sub = FakeSubprocess()
        self.sub.on(['tool'], stdout=b'out\r\nline\xff', stderr=b'err\n')

    def _popen(self, **kwargs):
        return self.sub.Popen(['tool'], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, **kwargs)

    def test_communicate_returns_bytes_by_default(self):
        out, err = self._popen().communicate()
        self.assertEqual((out, err), (b'out\r\nline\xff', b'err\n'))

    def test_communicate_returns_str_for_universal_newlines(self):
        self.sub.rules[0].stdout = b'out\r\nline\n'
        out, err = self._popen(universal_newlines=True).communicate()
        self.assertEqual((out, err), ('out\nline\n', 'err\n'))

    def test_communicate_returns_str_for_text_and_encoding(self):
        self.sub.rules[0].stdout = u'café'.encode('utf-8')
        self.assertEqual(self._popen(text=True).communicate()[0], u'café')
        self.assertEqual(
            self._popen(encoding='utf-8').communicate()[0], u'café')

    def test_text_mode_decoding_of_invalid_bytes_raises(self):
        with self.assertRaises(UnicodeDecodeError):
            self._popen(universal_newlines=True).communicate()

    def test_unpiped_streams_come_back_as_none(self):
        proc = self.sub.Popen(['tool'], stdout=subprocess.PIPE)
        self.assertEqual(proc.communicate(), (b'out\r\nline\xff', None))

    def test_stderr_to_stdout_merges_the_streams(self):
        proc = self.sub.Popen(['tool'], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)
        self.assertEqual(proc.communicate(), (b'out\r\nline\xfferr\n', None))

    def test_returncode_is_none_until_the_process_is_waited_for(self):
        proc = self._popen()
        self.assertIsNone(proc.returncode)
        proc.communicate()
        self.assertEqual(proc.returncode, 0)

    def test_binary_stdin_rejects_str(self):
        proc = self.sub.Popen(['tool'], stdin=subprocess.PIPE)
        with self.assertRaises(TypeError):
            proc.stdin.write('text')
        proc.stdin.write(b'bytes')
        self.assertEqual(proc.stdin.written(), b'bytes')

    def test_text_stdin_rejects_bytes(self):
        proc = self.sub.Popen(['tool'], stdin=subprocess.PIPE,
                              universal_newlines=True)
        with self.assertRaises(TypeError):
            proc.stdin.write(b'bytes')
        proc.stdin.write('text')
        self.assertEqual(proc.stdin.written(), 'text')

    def test_file_like_stdout_needs_a_real_file_descriptor(self):
        with self.assertRaises(io.UnsupportedOperation):
            self.sub.call(['tool'], stdout=io.StringIO())

    def test_non_string_arguments_are_rejected(self):
        with self.assertRaises(TypeError):
            self.sub.call(['tool', 5])
        with self.assertRaises(TypeError):
            self.sub.call(['tool', None])

    def test_environment_values_must_be_strings(self):
        with self.assertRaises(TypeError):
            self.sub.call(['tool'], env={'COUNT': 3})
        self.assertEqual(self.sub.call(['tool'], env={'COUNT': '3'}), 0)

    def test_check_output_returns_bytes_or_str_and_raises_on_failure(self):
        self.sub.rules[0].stdout = b'ok\n'
        self.assertEqual(self.sub.check_output(['tool']), b'ok\n')
        self.assertEqual(
            self.sub.check_output(['tool'], universal_newlines=True), 'ok\n')
        self.sub.on(['bad'], rc=3, stdout=b'partial')
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            self.sub.check_output(['bad'])
        self.assertEqual(caught.exception.returncode, 3)
        self.assertEqual(caught.exception.output, b'partial')

    def test_unscripted_commands_fail_unless_a_default_is_given(self):
        with self.assertRaises(AssertionError):
            self.sub.call(['other'])
        self.sub.default_rc = 7
        self.assertEqual(self.sub.call(['other']), 7)

    def test_rules_match_argv_prefixes_command_lines_and_predicates(self):
        sub = FakeSubprocess()
        sub.on(['sudo', 'rm'], rc=1)
        sub.on('sudo -E curl', rc=2)
        sub.on(lambda args: 'special' in args, rc=3)
        self.assertEqual(sub.call(['sudo', 'rm', '-rf', 'x']), 1)
        self.assertEqual(sub.call('sudo -E curl http://x -o y', shell=True), 2)
        self.assertEqual(sub.call(['a', 'special']), 3)

    def test_times_limits_how_often_a_rule_applies(self):
        sub = FakeSubprocess(default_rc=0)
        sub.on(['mount'], rc=1, times=1)
        self.assertEqual([sub.call(['mount']), sub.call(['mount'])], [1, 0])

    def test_effect_can_override_the_exit_status(self):
        sub = FakeSubprocess()
        sub.on(['t'], rc=0, effect=lambda inv: 9)
        self.assertEqual(sub.call(['t']), 9)


if __name__ == '__main__':
    unittest.main()
