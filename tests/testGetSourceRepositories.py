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
"""DIBGetSourceRepositories with git, curl, tar and rsync mocked.

Python-3 conversion risks covered: ``file()`` became ``open(path, 'rb')`` for
the SHA1 of a pulled file or tarball (downloads are arbitrary bytes), the
rsync listing from ``Popen().communicate()`` must be read as text, and
``urllib.parse`` replaced ``urlparse``.  GitProvider is a mock, subprocess is
a type-faithful fake, and the downloaded payload is written to a real temp
file only so that the module's own binary read has something to hash.
"""
import hashlib
import itertools
import os
import tempfile
import unittest
from configparser import RawConfigParser
from unittest import mock

from tests import dibsupport
from tests.dibsupport import FakeFlowControl, FakeSubprocess, make_module

getmod = dibsupport.import_dib('DIBGetSourceRepositories')

# Not valid UTF-8 (0xff, 0x80, a lone 0x8b), with NUL and CRLF in it.
BINARY_PAYLOAD = b'\xff\xfe\x00\x80\x8b binary \r\n\x00\x01' * 3
RSYNC_LISTING = (u'sending incremental file list\n./\nREADME\ncafé/main.c\n'
                 u'\nsent 1,234 bytes  received 56 bytes\n')
NOW = '2020-01-02T03:04:05'


def write_payload(payload):
    """curl effect: write ``payload`` to the file named by ``-o``."""
    def effect(invocation):
        args = invocation.args
        if isinstance(args, str):
            dest = args.rsplit(' -o ', 1)[1]
        else:
            dest = args[args.index('-o') + 1]
        with open(dest, 'wb') as f:
            f.write(payload)
    return effect


class GetterTestCase(unittest.TestCase):
    URL = 'http://example.invalid/proj'

    def setUp(self):
        self.root = dibsupport.make_tmpdir(self)
        self.sub = FakeSubprocess(default_rc=0).install(self)
        # sudo mkdir -p really creates the directory curl then writes into
        self.sub.on(['sudo', 'mkdir', '-p'],
                    effect=lambda inv: os.makedirs(inv.args[3], exist_ok=True))
        self.sub.on(['sudo', '-E', 'curl'], effect=write_payload(b'downloaded'))
        self.hooksdir = os.path.join(self.root, 'hooks')
        os.mkdir(self.hooksdir)
        self.targetroot = os.path.join(self.root, 'target')
        self.targetpath = os.path.join(self.targetroot, 'opt')
        self.logfile = os.path.join(self.root, 'image.progresslog')

        counter = itertools.count(1)
        self.tempdirs = []

        def fake_mkdtemp(*args, **kwargs):
            path = os.path.join(self.root, 'tmp%d' % next(counter))
            os.mkdir(path)
            self.tempdirs.append(path)
            return path
        patcher = mock.patch.object(tempfile, 'mkdtemp', fake_mkdtemp)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.git = mock.MagicMock(name='GitProvider')
        self.git.determineRepoPath.side_effect = (
            lambda tempdir, repo: (repo, os.path.join(tempdir, repo)))
        self.git.fetchRepository.return_value = (True, ('oldsha', 'newsha'), 'ok')
        patcher = mock.patch.object(getmod, 'GitProvider', self.git)
        patcher.start()
        self.addCleanup(patcher.stop)

        clock = mock.MagicMock(name='datetime')
        clock.utcnow.return_value.isoformat.return_value = NOW
        patcher = mock.patch.object(getmod, 'datetime', clock)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.shellenv = {'PATH': '/usr/bin', 'HOME': '/root'}
        self.dibenv = {
            'source-repository-overrides': {},
            'hooksdir': self.hooksdir,
            'logconfig': RawConfigParser(),
            'logfile': self.logfile,
            'shellenv': self.shellenv}
        self.dibenv['logconfig'].optionxform = str
        self.getter = make_module(
            getmod.DIBGetSourceRepositories,
            env={'__DIBEnv__': self.dibenv, 'TARGET_ROOT': self.targetroot})

    def repo(self, line, filename='source-repository-test'):
        with open(os.path.join(self.hooksdir, filename), 'w') as f:
            f.write(line + '\n')

    def advise(self, **advice):
        self.getter.flowcontrol = FakeFlowControl(advice)

    def build(self, options=None):
        return self.getter.build({} if options is None else options)

    def joinpoints(self):
        return self.getter.engine.joinpoints()

    def launched(self, joinpoint):
        return [extra for (aspects, jp, phase, extra)
                in self.getter.engine.launches if jp == joinpoint]

    def pulled(self):
        passed = self.launched('script_passed')
        self.assertEqual(len(passed), 1)
        return passed[0]['pulled']


class RepositoryDefinitionTests(GetterTestCase):

    def test_build_ignores_tilde_files_other_names_and_directories(self):
        self.repo('skipped file /opt/skipped %s' % self.URL, 'source-repository-old~')
        self.repo('ignored file /opt/ignored %s' % self.URL, 'README')
        os.mkdir(os.path.join(self.hooksdir, 'source-repository-dir'))
        self.repo('used file /opt/used %s' % self.URL)

        self.assertTrue(self.build())

        sources = [extra['source']['name'] for extra in self.launched('script_start')]
        self.assertEqual(sources, ['used'])

    def test_build_warns_about_blank_and_comment_lines(self):
        with open(os.path.join(self.hooksdir, 'source-repository-test'), 'w') as f:
            f.write('\n# a comment\nblob file /opt/blob %s\n' % self.URL)

        self.assertTrue(self.build())

        self.assertEqual(len(self.getter.log.messages('warning')), 4)
        self.assertTrue(self.getter.log.mentions('warning', 'Empty line or comment'))

    def test_build_four_field_line_gets_a_wildcard_ref(self):
        self.repo('blob file /opt/blob %s' % self.URL)

        self.build()

        source = self.launched('script_start')[0]['source']
        self.assertEqual(source, {'name': 'blob', 'type': 'file', 'local': '/opt/blob',
                                  'URL': self.URL, 'ref': '*'})

    def test_build_rejects_a_line_with_the_wrong_number_of_fields(self):
        self.repo('blob file /opt/blob')

        self.assertIsNone(self.build())

        self.assertEqual(self.getter.log.result, 'failed')
        self.assertTrue(self.getter.log.mentions('error', 'did not provide all of the required data'))
        self.assertEqual(self.sub.commands(), [])

    def test_build_fails_when_a_repository_file_cannot_be_read(self):
        self.repo('blob file /opt/blob %s' % self.URL)
        real_open = open

        def refuse(path, *args, **kwargs):
            if str(path).endswith('source-repository-test'):
                raise PermissionError(13, 'Permission denied', path)
            return real_open(path, *args, **kwargs)
        with mock.patch('builtins.open', refuse):
            self.assertIsNone(self.build())

        self.assertEqual(self.getter.log.result, 'failed')
        self.assertTrue(self.getter.log.mentions('exception', 'failed to open'))

    def test_build_default_url_base_replaces_scheme_and_host_only(self):
        self.repo('blob file /opt/blob http://old.example.com:8080/path/blob.bin?x=1')

        self.build({'default-url-base': 'https://mirror.example.org'})

        source = self.launched('script_start')[0]['source']
        self.assertEqual(source['URL'], 'https://mirror.example.org/path/blob.bin?x=1')

    def test_build_override_replaces_fields_and_url_base_by_alternate_name(self):
        self.repo('my-blob file /opt/blob http://old.example.com/path/blob.bin')
        self.dibenv['source-repository-overrides'] = {
            'my_blob': {'URLbase': 'https://mirror.example.org', 'type': 'tar', 'ref': 'v2'}}

        self.build()

        source = self.launched('script_start')[0]['source']
        self.assertEqual(source['URL'], 'https://mirror.example.org/path/blob.bin')
        self.assertEqual((source['type'], source['ref']), ('tar', 'v2'))
        self.assertEqual(source['URLbase'], 'https://mirror.example.org')


class GitRepositoryTests(GetterTestCase):

    def setUp(self):
        GetterTestCase.setUp(self)
        self.repo('proj git /opt/proj https://git.example.com/org/proj.git feature')

    def test_build_fetches_into_a_temp_dir_then_rsyncs_to_the_target(self):
        self.assertTrue(self.build())

        tempdir = self.tempdirs[0]
        self.git.determineRepoPath.assert_called_once_with(tempdir, 'proj')
        self.git.fetchRepository.assert_called_once_with(
            self.getter.log, 'proj', os.path.join(tempdir, 'proj'),
            'https://git.example.com/org/proj.git', 'feature', None, self.shellenv)
        self.assertEqual(self.sub.commands(), [
            ['sudo', 'mkdir', '-p', self.targetpath],
            ['sudo', '-E', 'rsync', '-a', '--remove-source-files',
             os.path.join(tempdir, 'proj'), self.targetpath]])
        self.assertIs(self.sub.invocations[1].kwargs['env'], self.shellenv)
        self.assertEqual(self.getter.log.result, 'passed')

    def test_build_hands_the_fetch_result_to_the_passed_aspects(self):
        self.build()

        self.assertEqual(self.pulled(), (True, ('oldsha', 'newsha'), 'ok'))
        self.assertEqual(self.joinpoints(), ['script_start', 'script_passed', 'script_end'])

    def test_build_treats_a_wildcard_ref_as_master(self):
        self.repo('proj git /opt/proj https://git.example.com/org/proj.git')

        self.build()

        self.assertEqual(self.git.fetchRepository.call_args[0][4], 'master')

    def test_build_passes_an_overridden_ref_type_to_the_provider(self):
        self.dibenv['source-repository-overrides'] = {'proj': {'reftype': 'tag', 'ref': 'v1.0'}}

        self.build()

        args = self.git.fetchRepository.call_args[0]
        self.assertEqual((args[4], args[5]), ('v1.0', 'tag'))

    def test_build_fails_without_injecting_when_the_fetch_fails(self):
        self.git.fetchRepository.return_value = (False, 128, 'fatal: repository not found')

        self.assertFalse(self.build())

        self.assertEqual(self.getter.log.result, 'failed')
        self.assertTrue(self.getter.log.mentions('error', 'fatal: repository not found'))
        self.assertEqual(self.sub.count('sudo', '-E', 'rsync'), 0)
        self.assertEqual(self.joinpoints(), ['script_start', 'script_failed', 'script_end'])

    def test_build_fails_when_the_checkout_cannot_be_injected(self):
        self.sub.on(['sudo', '-E', 'rsync'], rc=23)

        self.assertFalse(self.build())

        self.assertTrue(self.getter.log.mentions('error', 'GIT repo failed to inject'))
        self.assertEqual(self.joinpoints()[-2:], ['script_failed', 'script_end'])

    def test_build_reports_a_provider_exception_to_the_aspects(self):
        self.git.fetchRepository.side_effect = RuntimeError('network down')

        self.assertFalse(self.build())

        self.assertEqual(self.getter.log.result, 'failed')
        self.assertEqual(self.joinpoints(), ['script_start', 'script_exception', 'script_failed'])
        self.assertIsInstance(self.launched('script_exception')[0]['__ex__'], RuntimeError)
        self.assertTrue(self.getter.log.mentions('exception', 'Repo failed to load with exception'))

    def test_build_tries_again_after_an_exception_when_advised(self):
        self.git.fetchRepository.side_effect = [
            RuntimeError('flaky'), (True, ('a', 'b'), 'ok')]
        self.advise(tryScriptAgain=[True, False])

        self.assertTrue(self.build())

        self.assertEqual(self.git.fetchRepository.call_count, 2)
        self.assertEqual(self.joinpoints(), [
            'script_start', 'script_exception', 'script_passed', 'script_end'])


class FileRepositoryTests(GetterTestCase):

    def setUp(self):
        GetterTestCase.setUp(self)
        self.repo('blob file /opt/blob.bin http://example.invalid/blob.bin')
        self.dest = os.path.join(self.targetpath, 'blob.bin')
        self.sub.on(['sudo', '-E', 'curl'], effect=write_payload(BINARY_PAYLOAD),
                    first=True)

    def test_build_downloads_with_curl_into_the_target(self):
        self.assertTrue(self.build())

        self.assertEqual(self.sub.commands(), [
            ['sudo', 'mkdir', '-p', self.targetpath],
            ['sudo', '-E', 'curl', 'http://example.invalid/blob.bin', '-o', self.dest]])
        self.assertIs(self.sub.invocations[1].kwargs['env'], self.shellenv)

    def test_build_checksums_non_utf8_content_as_binary(self):
        self.build()

        self.assertEqual(self.pulled(), (
            self.dest, hashlib.sha1(BINARY_PAYLOAD).hexdigest(), NOW))

    def test_build_checksums_empty_content(self):
        self.sub.on(['sudo', '-E', 'curl'], effect=write_payload(b''), first=True)

        self.build()

        self.assertEqual(self.pulled()[1], hashlib.sha1(b'').hexdigest())

    def test_build_handles_non_ascii_target_names(self):
        self.repo(u'blob file /opt/café.bin http://example.invalid/blob.bin')

        self.assertTrue(self.build())

        self.assertEqual(self.pulled()[0], os.path.join(self.targetpath, u'café.bin'))

    def test_build_fails_when_the_download_fails(self):
        self.sub.on(['sudo', '-E', 'curl'], rc=22, first=True)

        self.assertFalse(self.build())

        self.assertEqual(self.getter.log.result, 'failed')
        self.assertTrue(self.getter.log.mentions('error', 'FILE repo failed to download and inject'))
        self.assertEqual(self.launched('script_passed'), [])

    def test_build_fails_when_the_downloaded_file_is_missing(self):
        self.sub.on(['sudo', '-E', 'curl'], rc=0, first=True)  # "succeeds", writes nothing

        self.assertFalse(self.build())

        self.assertTrue(self.getter.log.mentions('exception', 'Repo failed to load with exception'))
        self.assertEqual(self.launched('script_passed'), [])

    def test_build_logs_the_proxy_settings_it_downloads_with(self):
        self.shellenv.update({'http_proxy': 'http://proxy:3128',
                              'HTTPS_PROXY': 'http://proxy:3129', 'NO_PROXY': 'localhost'})

        self.build()

        debug = self.getter.log.messages('debug')
        self.assertIn('http_proxy is: http://proxy:3128', debug)
        self.assertIn('HTTPS_PROXY is: http://proxy:3129', debug)
        self.assertIn('NO_PROXY is: localhost', debug)


class TarRepositoryTests(GetterTestCase):

    def setUp(self):
        GetterTestCase.setUp(self)
        self.repo('proj tar /opt/proj http://example.invalid/proj.tgz proj-1.0')
        self.sub.on('sudo -E curl', effect=write_payload(BINARY_PAYLOAD), first=True)
        self.sub.on(['sudo', '-E', 'rsync', '-av'], stdout=RSYNC_LISTING)
        self.tempdir = os.path.join(self.root, 'tmp1')
        self.tarball = os.path.join(self.root, 'tmp2', 'temp.tgz')
        self.targetdrop = os.path.join(self.targetpath, 'proj')

    def test_build_downloads_extracts_and_rsyncs_in_order(self):
        self.assertTrue(self.build())

        self.assertEqual(self.sub.commands(), [
            ['sudo', 'mkdir', '-p', self.targetpath],
            'sudo -E curl http://example.invalid/proj.tgz -o %s' % self.tarball,
            'sudo -E tar -C %s -xzf %s' % (self.tempdir, self.tarball),
            ['sudo', '-E', 'rsync', '-av', '--remove-source-files',
             os.path.join(self.tempdir, 'proj-1.0', '.'), self.targetdrop],
            ['sudo', '-E', 'rm', '-rf', self.tarball]])
        for index in (1, 2):
            self.assertTrue(self.sub.invocations[index].kwargs['shell'])
            self.assertIs(self.sub.invocations[index].kwargs['env'], self.shellenv)

    def test_build_checksums_the_tarball_as_binary(self):
        self.build()

        _, _, sha, downloaded, tarball = self.pulled()
        self.assertEqual(sha, hashlib.sha1(BINARY_PAYLOAD).hexdigest())
        self.assertEqual((downloaded, tarball), (NOW, self.tarball))

    def test_build_reads_the_rsync_listing_as_text(self):
        self.build()

        targetdrop, repofiles = self.pulled()[:2]
        self.assertEqual(targetdrop, self.targetdrop)
        self.assertEqual(repofiles, ['README', u'café/main.c'])
        rsync = self.sub.invocations[3]
        self.assertEqual(rsync.func, 'Popen')
        self.assertTrue(rsync.kwargs['universal_newlines'])

    def test_build_reports_no_files_when_rsync_lists_none(self):
        self.sub.on(['sudo', '-E', 'rsync', '-av'], stdout=u'sending incremental file list\n',
                    first=True)

        self.build()

        self.assertEqual(self.pulled()[1], [])

    def test_build_removes_the_downloaded_tarball_after_the_aspects_ran(self):
        self.build()

        self.assertEqual(self.sub.commands()[-1], ['sudo', '-E', 'rm', '-rf', self.tarball])
        self.assertEqual(self.getter.log.result, 'passed')

    def test_build_still_passes_when_the_tarball_cannot_be_removed(self):
        self.sub.on(['sudo', '-E', 'rm'], rc=1, first=True)

        self.assertTrue(self.build())

        self.assertTrue(self.getter.log.mentions('exception', 'Failed to delete temporary tar'))

    def test_build_fails_when_the_download_fails(self):
        self.sub.on('sudo -E curl', rc=7, first=True)

        self.assertFalse(self.build())

        self.assertTrue(self.getter.log.mentions('error', 'TAR repo failed to download'))
        self.assertEqual(self.sub.count('sudo', '-E', 'rsync'), 0)

    def test_build_fails_when_the_tarball_cannot_be_extracted(self):
        self.sub.on('sudo -E tar', rc=2, first=True)

        self.assertFalse(self.build())

        self.assertTrue(self.getter.log.mentions('error', 'TAR repo failed to download'))
        self.assertEqual(self.sub.count('sudo', '-E', 'rsync'), 0)

    def test_build_fails_when_the_content_cannot_be_injected(self):
        self.sub.on(['sudo', '-E', 'rsync', '-av'], rc=23, stderr=u'rsync error', first=True)

        self.assertFalse(self.build())

        self.assertTrue(self.getter.log.mentions('error', 'TAR repo failed to inject'))
        self.assertEqual(self.launched('script_passed'), [])


class FlowTests(GetterTestCase):

    def test_build_rejects_an_unknown_repository_type(self):
        self.repo('weird svn /opt/weird http://example.invalid/weird')

        self.assertFalse(self.build())

        self.assertTrue(self.getter.log.mentions('error', 'Incorrect repo type'))
        self.assertEqual(self.launched('script_passed'), [])

    def test_build_reports_the_failure_when_the_target_directory_cannot_be_made(self):
        self.repo('blob file /opt/blob http://example.invalid/blob')
        self.sub.on(['sudo', 'mkdir'], rc=1, first=True)

        self.assertFalse(self.build())

        self.assertTrue(self.getter.log.mentions('error', 'Could not make directory for repo'))
        self.assertEqual(self.sub.count('sudo', '-E', 'curl'), 0)

    def test_build_skips_repositories_the_progress_log_marks_completed(self):
        self.repo('blob file /opt/blob http://example.invalid/blob')
        self.dibenv['logconfig'].add_section('DIBGetSourceRepositories')
        self.dibenv['logconfig'].set('DIBGetSourceRepositories', 'blob', '')

        self.assertTrue(self.build())

        self.assertEqual(self.joinpoints(), ['script_skip'])
        self.assertEqual(self.sub.commands(), [])

    def test_build_repeats_a_completed_repository_when_advised_to(self):
        self.repo('blob file /opt/blob http://example.invalid/blob')
        self.dibenv['logconfig'].add_section('DIBGetSourceRepositories')
        self.dibenv['logconfig'].set('DIBGetSourceRepositories', 'blob', '')
        self.advise(doNotAvoidScript=True)

        self.assertTrue(self.build())

        self.assertEqual(self.joinpoints(), [
            'script_skip', 'script_start', 'script_passed', 'script_end'])

    def test_build_honours_an_aspect_that_preempts_a_repository(self):
        self.repo('blob file /opt/blob http://example.invalid/blob')
        self.advise(doNotStartScript=True)

        self.assertTrue(self.build())

        self.assertEqual(self.joinpoints(), ['script_start'])
        self.assertEqual(self.sub.commands(), [])
        self.assertTrue(self.getter.log.mentions('info', 'preempted by an aspect'))

    def test_build_stops_after_the_first_failure_by_default(self):
        self.repo('a file /opt/a http://example.invalid/a\nb file /opt/b http://example.invalid/b')
        self.sub.on(['sudo', '-E', 'curl'], rc=1, times=1, first=True)

        self.assertFalse(self.build())

        self.assertEqual(self.sub.count('sudo', '-E', 'curl'), 1)
        self.assertTrue(self.getter.log.mentions('info', 'Bailing out'))

    def test_build_continues_after_a_failure_when_keep_going_is_set(self):
        self.getter.settings['keep-going'] = True
        self.repo('a file /opt/a http://example.invalid/a\nb file /opt/b http://example.invalid/b')
        self.sub.on(['sudo', '-E', 'curl'], rc=1, times=1, first=True)

        self.assertFalse(self.build())

        self.assertEqual(self.sub.count('sudo', '-E', 'curl'), 2)
        self.assertEqual(self.getter.log.result, 'failed')

    def test_build_records_each_completed_repository_in_the_progress_log(self):
        self.repo('blob file /opt/blob http://example.invalid/blob')

        self.build()

        config = self.dibenv['logconfig']
        self.assertEqual(config.options('DIBGetSourceRepositories'), ['blob'])
        written = RawConfigParser()
        written.optionxform = str
        written.read(self.logfile)
        self.assertEqual(written.options('DIBGetSourceRepositories'), ['blob'])

    def test_build_does_not_record_a_failed_repository(self):
        self.repo('blob file /opt/blob http://example.invalid/blob')
        self.sub.on(['sudo', '-E', 'curl'], rc=1, first=True)

        self.build()

        self.assertEqual(self.dibenv['logconfig'].options('DIBGetSourceRepositories'), [])

    def test_build_removes_its_temp_dir_unless_an_aspect_asks_to_keep_it(self):
        self.repo('blob file /opt/blob http://example.invalid/blob')
        self.build()
        self.assertFalse(os.path.exists(self.tempdirs[0]))

    def test_build_keeps_the_temp_dir_when_advised_to(self):
        self.repo('blob file /opt/blob http://example.invalid/blob')
        self.advise(doNotDeleteTemp=True)

        self.build()

        self.assertTrue(os.path.exists(self.tempdirs[0]))

    def test_build_gives_the_aspects_registered_for_the_repository_name(self):
        self.repo('blob file /opt/blob http://example.invalid/blob')

        self.build({'__*': ['everywhere'], '__blob': ['just-blob'], '__other': ['x']})

        aspects = self.getter.engine.launches[0][0]
        self.assertEqual(aspects, ['everywhere', 'just-blob'])


if __name__ == '__main__':
    unittest.main()
