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
"""DIBRepo: source-repository overrides, with GitProvider mocked.

The module only records overrides (for DIBGetSourceRepositories) and returns
DIB_REPO* shell variables.  ``GitProvider.splitRepoReference`` is the one
outside call; it is replaced by a mock so no git library is needed.  The
module's ``print(ref, reftype)`` (converted from a Python 2 print statement)
is checked on captured stdout.
"""
import contextlib
import io
import unittest
from unittest import mock

from tests import dibsupport
from tests.dibsupport import make_module

repomod = dibsupport.import_dib('DIBRepo')


class RepoTestCase(unittest.TestCase):

    def setUp(self):
        self.overrides = {}
        self.provider = mock.MagicMock(name='GitProvider')
        # same contract as GitProvider.splitRepoReference: 'type:ref' -> (ref, type)
        self.provider.splitRepoReference.side_effect = (
            lambda reference: tuple(reversed(reference.split(':', 1)))
            if ':' in reference else (reference, None))
        patcher = mock.patch.object(repomod, 'GitProvider', self.provider)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = make_module(repomod.DIBRepo, env={
            '__DIBEnv__': {'source-repository-overrides': self.overrides}})

    def default(self, **options):
        options.setdefault('name', 'proj')
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = self.repo.default(options)
        self.printed = stdout.getvalue()
        return result


class DefaultTests(RepoTestCase):

    def test_default_with_only_a_name_registers_an_empty_override(self):
        self.assertEqual(self.default(), {})

        self.assertEqual(self.overrides, {'proj': {}})
        self.assertEqual(self.repo.log.result, 'passed')

    def test_default_type_sets_the_variable_and_the_override(self):
        result = self.default(type='tar')

        self.assertEqual(result, {'DIB_REPOTYPE_proj': 'tar'})
        self.assertEqual(self.overrides['proj'], {'type': 'tar'})

    def test_default_url_sets_the_location_variable_and_the_override(self):
        result = self.default(URL='https://git.example.com/org/proj.git')

        self.assertEqual(result, {'DIB_REPOLOCATION_proj': 'https://git.example.com/org/proj.git'})
        self.assertEqual(self.overrides['proj'],
                         {'URL': 'https://git.example.com/org/proj.git'})

    def test_default_url_base_is_only_an_override_not_a_shell_variable(self):
        result = self.default(URLbase='https://mirror.example.org')

        self.assertEqual(result, {})
        self.assertEqual(self.overrides['proj'], {'URLbase': 'https://mirror.example.org'})
        self.assertTrue(self.repo.log.mentions('info', 'this will not change DIB_REPOLOCATION'))

    def test_default_ref_is_split_into_reference_and_reference_type(self):
        result = self.default(ref='tag:v1.0')

        self.provider.splitRepoReference.assert_called_once_with('tag:v1.0')
        self.assertEqual(result, {'DIB_REPOREF_proj': 'v1.0'})
        self.assertEqual(self.overrides['proj'], {'ref': 'v1.0', 'reftype': 'tag'})

    def test_default_ref_without_a_type_prefix_has_no_reference_type(self):
        self.default(ref='feature-branch')

        self.assertEqual(self.overrides['proj'], {'ref': 'feature-branch', 'reftype': None})

    def test_default_ref_prints_the_split_reference_as_two_values(self):
        self.default(ref='tag:v1.0')

        self.assertEqual(self.printed, 'v1.0 tag\n')

    def test_default_applies_substitutions_to_type_url_url_base_and_ref(self):
        self.repo.env.substitutions = {'%(HOST)s': 'git.example.com', '%(REF)s': 'v2'}

        result = self.default(
            type='git', URL='https://%(HOST)s/proj.git',
            URLbase='https://%(HOST)s', ref='%(REF)s')

        self.assertEqual(self.overrides['proj'], {
            'type': 'git', 'URL': 'https://git.example.com/proj.git',
            'URLbase': 'https://git.example.com', 'ref': 'v2', 'reftype': None})
        self.assertEqual(result['DIB_REPOLOCATION_proj'], 'https://git.example.com/proj.git')

    def test_default_replaces_an_earlier_override_for_the_same_repository(self):
        self.default(URL='http://old.example.com/proj')
        self.default(type='file')

        self.assertEqual(self.overrides, {'proj': {'type': 'file'}})

    def test_default_keeps_overrides_for_different_repositories_apart(self):
        self.default(name='one', type='tar')
        self.default(name='two', type='file')

        self.assertEqual(self.overrides, {'one': {'type': 'tar'}, 'two': {'type': 'file'}})

    def test_default_handles_non_ascii_names_and_urls(self):
        result = self.default(name=u'café', URL=u'http://example.com/café')

        self.assertEqual(result, {u'DIB_REPOLOCATION_café': u'http://example.com/café'})
        self.assertIn(u'café', self.overrides)

    def test_default_requires_a_name(self):
        with self.assertRaises(KeyError):
            self.repo.default({'type': 'tar'})


if __name__ == '__main__':
    unittest.main()
