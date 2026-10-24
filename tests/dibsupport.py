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
"""Shared helpers for the csmake-dib tests.

The DIB modules shell out (sudo, losetup, parted, mount, curl, ...) and need a
csmake engine.  Rather than mock their internals, the tests put small fake
executables first on PATH (a ``sudo`` that just runs its arguments, tools that
record how they were called) and call the real module methods on a minimal
stand-in for ``self``.
"""
import contextlib
import importlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from tests import csmake_alias

# Top-level packages provided by csmake core / other plugin repos / PyPI.
EXTERNAL_PACKAGES = ('Csmake', 'CsmakeCore', 'CsmakeProviders', 'git')

# Runs its arguments directly (dropping sudo's own flags such as -E) so that
# "sudo chmod ..." exercises the real chmod without needing root.
SUDO = 'while [ "${1#-}" != "$1" ]; do shift; done\nexec "$@"\n'
# Stand-in for chroot(8): "enter" the directory and run the command there.
CHROOT = 'cd "$1" || exit 1\nshift\nexec "$@"\n'

_RECORDER = (
    'rec() { printf "%s" "$(basename "$0")" >> "$FAKE_LOG"; '
    'for a in "$@"; do printf " [%s]" "$a" >> "$FAKE_LOG"; done; '
    'echo >> "$FAKE_LOG"; }\n'
    'rec "$@"\n')


def make_tmpdir(testcase):
    path = tempfile.mkdtemp(prefix='csmake-dib-test-')
    testcase.addCleanup(shutil.rmtree, path, True)
    return path


def install_fake_tools(testcase, tools):
    """Put fake executables first on PATH for the duration of a test.

    ``tools`` maps a tool name to the body of a /bin/sh script.  Every tool
    except sudo and chroot records its argv to the file named by $FAKE_LOG.
    Returns (bindir, logpath); see ``calls`` to read the log.
    """
    root = make_tmpdir(testcase)
    bindir = os.path.join(root, 'bin')
    os.mkdir(bindir)
    logpath = os.path.join(root, 'fake-tools.log')
    open(logpath, 'w').close()
    scripts = dict(tools)
    scripts.setdefault('sudo', SUDO)
    for name, body in scripts.items():
        if name not in ('sudo', 'chroot'):
            body = _RECORDER + body
        path = os.path.join(bindir, name)
        with open(path, 'w') as script:
            script.write('#!/bin/sh\n' + body)
        os.chmod(path, 0o755)
    patcher = mock.patch.dict(os.environ, {
        'PATH': bindir + os.pathsep + os.environ.get('PATH', os.defpath),
        'FAKE_LOG': logpath})
    patcher.start()
    testcase.addCleanup(patcher.stop)
    return bindir, logpath


def calls(logpath, tool=None):
    """Recorded fake-tool invocations as (tool, [args...]) tuples."""
    result = []
    with open(logpath) as log:
        for line in log:
            line = line.rstrip('\n')
            name, _, rest = line.partition(' ')
            args = []
            while rest.startswith('['):
                end = rest.index('] [') if '] [' in rest else len(rest) - 1
                args.append(rest[1:end])
                rest = rest[end + 2:]
            if tool is None or name == tool:
                result.append((name, args))
    return result


class FakeLog(object):
    """Records csmake log calls; unknown methods (info, devdebug...) record."""

    def __init__(self):
        self.records = []
        self.result = None

    def __getattr__(self, level):
        if level.startswith('__'):
            raise AttributeError(level)

        def record(message='', *args):
            try:
                message = message % args if args else message
            except (TypeError, ValueError):
                message = '%s %r' % (message, args)
            self.records.append((level, str(message)))
        return record

    def out(self):
        return subprocess.DEVNULL

    def err(self):
        return subprocess.DEVNULL

    def passed(self):
        self.result = 'passed'

    def failed(self):
        self.result = 'failed'

    def messages(self, level):
        return [m for (lvl, m) in self.records if lvl == level]


class FakeEnv(object):
    def __init__(self, env=None):
        self.env = {} if env is None else env

    def doSubstitutions(self, text):
        return text


class FakeEngine(object):
    def __init__(self):
        self.aspect_calls = []

    def launchAspects(self, aspects, joinpoint, phase, module, options, extra=None):
        self.aspect_calls.append((joinpoint, phase, extra))


class FakeFlowControl(object):
    def initFlowControlIssue(self, name, description):
        pass

    def advice(self, name):
        return False


def make_module(cls, env=None, **attrs):
    """An instance of a CsmakeModule subclass without running its __init__
    (which needs a whole csmake engine), wired to the fakes above."""
    module = object.__new__(cls)
    module.log = FakeLog()
    module.env = FakeEnv(env)
    module.engine = FakeEngine()
    module.settings = {'keep-going': False}
    module.flowcontrol = FakeFlowControl()
    for name, value in attrs.items():
        setattr(module, name, value)
    return module


@contextlib.contextmanager
def _stubbed_git_dependencies():
    """DIBGetSourceRepositories/DIBRepo import GitPython and csmake-providers'
    GitProvider at import time purely for their git code paths.  When those are
    not installed, stand-ins let the (unrelated) rest of the module import."""
    added = []

    def stub(name, module):
        sys.modules[name] = module
        added.append(name)
    try:
        import git  # noqa: F401
    except ImportError:
        stub('git', types.ModuleType('git'))
    try:
        from CsmakeProviders.GitProvider import GitProvider  # noqa: F401
    except ImportError:
        for leftover in ('CsmakeProviders', 'CsmakeProviders.GitProvider'):
            sys.modules.pop(leftover, None)
        package = types.ModuleType('CsmakeProviders')
        package.__path__ = []
        provider = types.ModuleType('CsmakeProviders.GitProvider')
        provider.GitProvider = type('GitProvider', (object,), {})
        stub('CsmakeProviders', package)
        stub('CsmakeProviders.GitProvider', provider)
    try:
        yield
    finally:
        for name in added:
            sys.modules.pop(name, None)


def import_dib(name):
    """Import CsmakeModules.<name>, SkipTest-ing if csmake core (or another
    plugin repo's base class) is unavailable."""
    csmake_alias.install()
    try:
        with _stubbed_git_dependencies():
            return importlib.import_module('CsmakeModules.' + name)
    except ImportError as e:
        # Only a dependency living outside this repo justifies skipping; any
        # other ImportError (e.g. a Python 2 module name) is a real failure.
        missing = (e.name or '').split('.')[0]
        if missing in EXTERNAL_PACKAGES or (e.name or '').startswith(
                ('CsmakeModules.Shell', 'CsmakeModules.DIBShell')):
            raise unittest.SkipTest(
                "cannot import CsmakeModules.%s: %s" % (name, e))
        raise


def file_mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)
