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
"""DIBGetSourceRepositories: checksums of downloaded (binary) content.

``file()`` became ``open(path, 'rb')`` for the SHA1 of a pulled file or
tarball.  Downloads are arbitrary bytes, so a text-mode open would raise
UnicodeDecodeError (or hand str to hashlib) on real content.  The module's
``build`` runs for real; ``sudo`` runs its arguments, ``curl`` is a script that
"downloads" a local fixture, and ``rsync`` reports a canned file listing.
"""
import hashlib
import io
import os
import tarfile
import tempfile
import unittest
from unittest import mock

from configparser import RawConfigParser

from tests import dibsupport

getmod = dibsupport.import_dib('DIBGetSourceRepositories')

# Not valid UTF-8 (0xff, 0x80, lone 0x8b) and contains NUL and CRLF.
BINARY_PAYLOAD = b'\xff\xfe\x00\x80\x8b binary \r\n\x00\x01' * 3

TOOLS = {
    # curl <url> -o <dest>   (flags in any order; copy $FAKE_PAYLOAD to dest)
    'curl': ('while [ $# -gt 0 ]; do\n'
             '  if [ "$1" = "-o" ]; then dest="$2"; shift; fi\n'
             '  shift\n'
             'done\n'
             'cp "$FAKE_PAYLOAD" "$dest"\n'),
    # rsync -av --remove-source-files <src>/. <dest>: only "report" if the
    # tarball really was extracted where the module says it was
    'rsync': '[ -f "$3/README" ] || exit 3\n'
             'printf "sending incremental file list\\n./\\nREADME\\nsrc/main.c\\n\\nsent 1 bytes\\n"\n',
}


class GetSourceRepositoriesTests(unittest.TestCase):

    def setUp(self):
        bindir, self.logpath = dibsupport.install_fake_tools(self, TOOLS)
        self.root = dibsupport.make_tmpdir(self)
        # the module makes scratch dirs with tempfile; keep them inside root
        patcher = mock.patch.object(tempfile, 'tempdir', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.payload = os.path.join(self.root, 'payload.bin')
        self.hooksdir = os.path.join(self.root, 'hooks')
        os.mkdir(self.hooksdir)
        self.targetroot = os.path.join(self.root, 'target')
        os.mkdir(self.targetroot)
        self.shellenv = {
            'PATH': os.environ['PATH'],
            'FAKE_LOG': self.logpath,
            'FAKE_PAYLOAD': self.payload}
        self.dibenv = {
            'source-repository-overrides': {},
            'hooksdir': self.hooksdir,
            'logconfig': RawConfigParser(),
            'logfile': os.path.join(self.root, 'image.progresslog'),
            'shellenv': self.shellenv}
        self.dibenv['logconfig'].optionxform = str
        self.getter = dibsupport.make_module(
            getmod.DIBGetSourceRepositories,
            env={'__DIBEnv__': self.dibenv, 'TARGET_ROOT': self.targetroot})

    def _passed(self):
        """The 'pulled' value handed to the script_passed aspects."""
        passed = [extra for (joinpoint, _, extra)
                  in self.getter.engine.aspect_calls
                  if joinpoint == 'script_passed']
        self.assertEqual(len(passed), 1)
        return passed[0]['pulled']

    def _define_repo(self, line):
        with open(os.path.join(self.hooksdir, 'source-repository-test'), 'w') as f:
            f.write(line + '\n')

    def test_file_source_is_checksummed_as_binary(self):
        with open(self.payload, 'wb') as f:
            f.write(BINARY_PAYLOAD)
        self._define_repo('blob file /opt/blob.bin http://example.invalid/blob.bin')

        self.assertTrue(self.getter.build({}), self.getter.log.records)

        self.assertEqual(self.getter.log.result, 'passed')
        path, sha, downloadtime = self._passed()
        self.assertEqual(path, os.path.join(self.targetroot, 'opt', 'blob.bin'))
        self.assertEqual(sha, hashlib.sha1(BINARY_PAYLOAD).hexdigest())
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), BINARY_PAYLOAD)
        self.assertEqual(
            self.getter.env.env['__DIBEnv__']['logconfig'].options(
                'DIBGetSourceRepositories'), ['blob'])

    def test_tar_source_is_checksummed_as_binary_and_rsync_listing_parsed(self):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w:gz') as tar:
            member = tarfile.TarInfo('proj-1.0/README')
            data = b'hello\n'
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
        tarball = archive.getvalue()
        self.assertEqual(tarball[:2], b'\x1f\x8b')  # gzip magic: not UTF-8
        with open(self.payload, 'wb') as f:
            f.write(tarball)
        self._define_repo('proj tar /opt/proj http://example.invalid/proj.tgz proj-1.0')

        self.assertTrue(self.getter.build({}), self.getter.log.records)

        self.assertEqual(self.getter.log.result, 'passed')
        targetdrop, repofiles, sha, downloadtime, temptarball = self._passed()
        self.assertEqual(sha, hashlib.sha1(tarball).hexdigest())
        # rsync -av output is text: lines after './' up to the blank line
        self.assertEqual(repofiles, ['README', 'src/main.c'])
        self.assertEqual(targetdrop, os.path.join(self.targetroot, 'opt', 'proj'))


if __name__ == '__main__':
    unittest.main()
