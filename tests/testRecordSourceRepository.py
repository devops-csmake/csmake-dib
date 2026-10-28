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
"""DIBCsversionRecordSourceRepository: BUILD_INDEX encoding and bookkeeping.

``str.encode('base64')`` (a Python 2 only codec) became
``base64.b64encode(...).decode('ascii')``.  The expected strings below were
produced independently with base64(1), so they pin the exact bytes that
downstream tooling decodes: text (not bytes), no line wrapping, UTF-8.
"""
import base64
import unittest

from tests import dibsupport

recordmod = dibsupport.import_dib('DIBCsversionRecordSourceRepository')

SHORT_URL = 'http://example.com/pkg.tgz'
SHORT_B64 = 'aHR0cDovL2V4YW1wbGUuY29tL3BrZy50Z3o='
# 118 bytes -> 160 base64 characters, i.e. more than one 76 column line
LONG_URL = ('https://mirror.example.org/some/very/long/path/to/the/artifacts/'
            'release-1.2.3/cs-sources-1.2.3.tar.gz?token=abc123&x=y')
LONG_B64 = ('aHR0cHM6Ly9taXJyb3IuZXhhbXBsZS5vcmcvc29tZS92ZXJ5L2xvbmcvcGF0aC90by'
            '90aGUvYXJ0aWZhY3RzL3JlbGVhc2UtMS4yLjMvY3Mtc291cmNlcy0xLjIuMy50YXIu'
            'Z3o/dG9rZW49YWJjMTIzJng9eQ==')
UNICODE_URL = u'http://example.com/café/ü?q=a b'
UNICODE_B64 = 'aHR0cDovL2V4YW1wbGUuY29tL2NhZsOpL8O8P3E9YSBi'


class RecordSourceRepositoryTests(unittest.TestCase):

    def setUp(self):
        self.rec = dibsupport.make_module(
            recordmod.DIBCsversionRecordSourceRepository)

    def _source(self, repotype, url):
        return ('mysrc', repotype, '/opt/mysrc', url, '*')

    def test_build_index_is_exact_unwrapped_text_for_file_and_tar(self):
        for url, expected in ((SHORT_URL, SHORT_B64),
                              (LONG_URL, LONG_B64),
                              (UNICODE_URL, UNICODE_B64)):
            with self.subTest(repotype='file', url=url):
                options = {'pulled': ('/opt/mysrc', 'deadbeef', 'T0')}
                key, entry = self.rec._handleFile(
                    self._source('file', url), options, {})
                self.assertEqual(key, 'mysrc')
                kind, values = entry
                self.assertEqual(kind, 'file')
                self.assertIs(type(values['BUILD_INDEX']), str)
                self.assertEqual(values['BUILD_INDEX'], expected)
                self.assertEqual(
                    base64.b64decode(values['BUILD_INDEX']).decode('utf-8'),
                    url)
                self.assertEqual(values['SHA1'], 'deadbeef')
                self.assertEqual(values['PATH'], '/opt/mysrc')
                self.assertEqual(values['URL'], url)

            with self.subTest(repotype='tar', url=url):
                options = {'pulled': ('/img', ['a', 'b'], 'cafe', 'T1',
                                      '/tmp/x.tgz')}
                key, entry = self.rec._handleTar(
                    self._source('tar', url), options, {})
                kind, values = entry
                self.assertEqual(kind, 'tar')
                self.assertIs(type(values['BUILD_INDEX']), str)
                self.assertEqual(values['BUILD_INDEX'], expected)
                self.assertEqual(values['FILES'], ('/opt/mysrc', ['a', 'b']))
                self.assertEqual(values['SHA1'], 'cafe')

    def test_script_passed_files_each_repo_type_under_name_and_tag(self):
        def passed(tag, repotype, url, pulled):
            options = {
                'tag': tag, 'partOverride': '*', 'pulled': pulled,
                'source': {'name': 'mysrc', 'type': repotype,
                           'local': '/opt/mysrc', 'URL': url, 'ref': 'v1'}}
            return self.rec.script_passed('build', options, None, None)

        self.assertTrue(passed(
            'base', 'git', 'https://git.example.com/org/repo.git',
            (('oldsha', 'newsha'), 0, 'ok')))
        self.assertTrue(passed(
            'base', 'file', SHORT_URL, ('/opt/mysrc', 'f00d', 'T0')))
        self.assertTrue(passed(
            'other', 'tar', LONG_URL, ('/img', ['x'], 'beef', 'T1', '/t.tgz')))

        sources = self.rec.env.env['__Csversion__']['sources']
        # git repos are keyed by URL path (leading slash removed)
        git = sources['org/repo.git']['base']
        self.assertEqual(git[0], 'git')
        self.assertEqual(git[1]['REF'], 'newsha')
        self.assertEqual(git[1]['BRANCH'], 'v1')
        self.assertEqual(sources['mysrc']['base'][1]['BUILD_INDEX'], SHORT_B64)
        self.assertEqual(sources['mysrc']['other'][1]['BUILD_INDEX'], LONG_B64)

        # same repo + tag again: replaced, with a warning
        self.assertTrue(passed(
            'base', 'file', LONG_URL, ('/opt/mysrc', 'f00d', 'T9')))
        self.assertEqual(sources['mysrc']['base'][1]['BUILD_INDEX'], LONG_B64)
        self.assertEqual(len(self.rec.log.messages('warning')), 1)

    def test_script_passed_rejects_unknown_repo_type(self):
        options = {'tag': 't', 'partOverride': '*', 'pulled': None,
                   'source': {'name': 'n', 'type': 'svn', 'local': '/l',
                              'URL': 'http://example.com/r', 'ref': '*'}}
        self.assertFalse(self.rec.script_passed('build', options, None, None))
        self.assertEqual(self.rec.log.result, 'failed')


if __name__ == '__main__':
    unittest.main()
