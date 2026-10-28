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
"""Shared helpers for the csmake-dib unit tests.

The DIB modules shell out (sudo, losetup, parted, mount, tar, curl, ...), run
scripts through csmake core's ShellPassEnv and talk to git.  The tests mock all
of those boundaries and script both success and failure:

* ``FakeSubprocess`` replaces subprocess.call/Popen/check_output.  It honours
  the real API's type contract (bytes unless text mode was requested,
  ``None`` from communicate() for streams that were not piped, file-like
  stdout objects need a fileno, a text-mode stdin only takes str, ...) so a
  bytes-vs-str conversion bug in a module is still caught.
* ``FakeLog``/``FakeEnv``/``FakeEngine``/``FakeFlowControl`` stand in for the
  csmake engine so a module method can be called on an instance built with
  ``make_module`` (which skips the engine-requiring ``__init__``).

Real temp files are only used where file I/O itself is the behaviour under
test (open modes, chmod bits, the pickled progress log, SHA1 of binary data).
"""
import collections
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


def make_tmpdir(testcase):
    path = tempfile.mkdtemp(prefix='csmake-dib-test-')
    testcase.addCleanup(shutil.rmtree, path, True)
    return path


def file_mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


# ---------------------------------------------------------------- subprocess

Invocation = collections.namedtuple('Invocation', 'func args kwargs')

_STREAM_CONSTANTS = (subprocess.PIPE, subprocess.STDOUT, subprocess.DEVNULL)


def _is_text_mode(kwargs):
    return bool(kwargs.get('text') or kwargs.get('universal_newlines')
                or kwargs.get('encoding') or kwargs.get('errors'))


def _to_bytes(value):
    if value is None:
        return b''
    return value.encode('utf-8') if isinstance(value, str) else bytes(value)


def _render(data, kwargs):
    """What a real child's output looks like to the caller."""
    if not _is_text_mode(kwargs):
        return data
    text = data.decode(kwargs.get('encoding') or 'utf-8',
                       kwargs.get('errors') or 'strict')
    return text.replace('\r\n', '\n').replace('\r', '\n')


def _check_stream(name, value):
    if value is None or value in _STREAM_CONSTANTS:
        return
    if isinstance(value, int):
        return
    value.fileno()  # io.UnsupportedOperation for StringIO & friends


def _check_args(args, kwargs):
    if kwargs.get('shell'):
        ok = isinstance(args, (str, bytes)) or all(
            isinstance(a, (str, bytes, os.PathLike)) for a in args)
    elif isinstance(args, (str, bytes, os.PathLike)):
        ok = True  # a lone program name
    else:
        ok = all(isinstance(a, (str, bytes, os.PathLike)) for a in args)
    if not ok:
        raise TypeError("bad subprocess args: %r" % (args,))
    env = kwargs.get('env')
    if env is not None:
        for key, value in env.items():
            for item in (key, value):
                if not isinstance(item, (str, bytes, os.PathLike)):
                    raise TypeError(
                        "expected str, bytes or os.PathLike object, not %s"
                        % type(item).__name__)
    for name in ('stdin', 'stdout', 'stderr'):
        _check_stream(name, kwargs.get(name))


class _Rule(object):
    def __init__(self, match, rc, stdout, stderr, effect, raises, times):
        self.match = match
        self.rc = rc
        self.stdout = _to_bytes(stdout)
        self.stderr = _to_bytes(stderr)
        self.effect = effect
        self.raises = raises
        self.times = times
        self.used = 0

    def matches(self, args):
        if self.times is not None and self.used >= self.times:
            return False
        match = self.match
        if callable(match):
            return bool(match(args))
        if isinstance(args, (str, bytes)):
            command = args
        else:
            command = [str(a) for a in args]
        if isinstance(match, str):
            joined = command if isinstance(command, str) else ' '.join(command)
            return joined.startswith(match)
        if isinstance(command, str):
            return command.startswith(' '.join(match))
        return command[:len(match)] == list(match)


class _FakeStdin(object):
    """The write end of a child's stdin pipe: str in text mode, else bytes."""

    def __init__(self, kwargs):
        self.text = _is_text_mode(kwargs)
        self.data = []
        self.closed = False

    def write(self, data):
        if self.closed:
            raise ValueError("write to closed file")
        if self.text and not isinstance(data, str):
            raise TypeError("write() argument must be str, not %s"
                            % type(data).__name__)
        if not self.text and not isinstance(data, (bytes, bytearray)):
            raise TypeError("a bytes-like object is required, not '%s'"
                            % type(data).__name__)
        self.data.append(data)

    def close(self):
        self.closed = True

    def written(self):
        return ('' if self.text else b'').join(self.data)


class FakePopen(object):
    def __init__(self, invocation, rc, stdout, stderr):
        self.invocation = invocation
        self.args = invocation.args
        kwargs = invocation.kwargs
        self._kwargs = kwargs
        self._rc = rc
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = None
        self.stdin = _FakeStdin(kwargs) if kwargs.get('stdin') == subprocess.PIPE else None

    def communicate(self, input=None, timeout=None):
        if input is not None:
            if self.stdin is None:
                raise ValueError("stdin was not a pipe")
            self.stdin.write(input)
        kwargs = self._kwargs
        out = self._stdout
        err = self._stderr
        if kwargs.get('stderr') == subprocess.STDOUT:
            out, err = out + err, b''
        out = _render(out, kwargs) if kwargs.get('stdout') == subprocess.PIPE else None
        err = _render(err, kwargs) if kwargs.get('stderr') == subprocess.PIPE else None
        self.returncode = self._rc
        return out, err

    def wait(self, timeout=None):
        self.returncode = self._rc
        return self._rc

    def poll(self):
        return self.returncode

    def kill(self):
        pass


class FakeSubprocess(object):
    """Scripted stand-in for subprocess.call / Popen / check_output.

    ``on(match, rc=0, stdout=..., stderr=..., effect=..., raises=..., times=...)``
    scripts a command; ``match`` is an argv prefix (list), a command-line
    prefix (str) or a predicate.  Output is what the child writes (str is
    encoded to UTF-8 bytes); callers see bytes or str according to the
    kwargs they passed.  ``effect(invocation)`` simulates side effects (and
    may return an exit status).  Unscripted commands fail the test unless a
    ``default_rc`` was given.  Everything is recorded in ``invocations``.
    """

    def __init__(self, default_rc=None):
        self.default_rc = default_rc
        self.rules = []
        self.invocations = []
        self.processes = []

    def on(self, match, rc=0, stdout=b'', stderr=b'', effect=None,
           raises=None, times=None, first=False):
        """Script a command.  ``first=True`` makes it override earlier rules."""
        rule = _Rule(match, rc, stdout, stderr, effect, raises, times)
        if first:
            self.rules.insert(0, rule)
        else:
            self.rules.append(rule)
        return rule

    # -- patching
    def install(self, testcase):
        patcher = mock.patch.multiple(
            subprocess, call=self.call, Popen=self.Popen,
            check_output=self.check_output)
        patcher.start()
        testcase.addCleanup(patcher.stop)
        return self

    # -- recorded views
    def commands(self, func=None):
        return [inv.args for inv in self.invocations
                if func is None or inv.func == func]

    def kwargs_of(self, *prefix):
        return [inv.kwargs for inv in self.invocations
                if _Rule(list(prefix), 0, b'', b'', None, None, None).matches(inv.args)]

    def count(self, *prefix):
        return len(self.kwargs_of(*prefix))

    # -- the stand-ins
    def _dispatch(self, func, args, kwargs):
        _check_args(args, kwargs)
        invocation = Invocation(func, args, kwargs)
        self.invocations.append(invocation)
        for rule in self.rules:
            if rule.matches(args):
                rule.used += 1
                if rule.raises is not None:
                    raise rule.raises
                rc = rule.rc
                if rule.effect is not None:
                    override = rule.effect(invocation)
                    if override is not None:
                        rc = override
                return invocation, rc, rule.stdout, rule.stderr
        if self.default_rc is None:
            raise AssertionError("unscripted subprocess.%s: %r" % (func, args))
        return invocation, self.default_rc, b'', b''

    def call(self, args, **kwargs):
        return self._dispatch('call', args, kwargs)[1]

    def Popen(self, args, **kwargs):
        invocation, rc, out, err = self._dispatch('Popen', args, kwargs)
        process = FakePopen(invocation, rc, out, err)
        self.processes.append(process)
        return process

    def check_output(self, args, **kwargs):
        invocation, rc, out, err = self._dispatch('check_output', args, kwargs)
        output = _render(out, kwargs)
        if rc != 0:
            raise subprocess.CalledProcessError(rc, args, output=output)
        return output


# -------------------------------------------------------------- engine fakes

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

    def mentions(self, level, text):
        return any(text in m for m in self.messages(level))


class FakeStepResult(object):
    def __init__(self, passed=True, value=None):
        self.passed = passed
        self.value = value

    def _didPass(self):
        return self.passed

    def _getReturnValue(self, phase):
        return self.value


class FakeEngine(object):
    def __init__(self):
        self.settings = {'keep-going': False}
        self.phase = 'build'
        self.launches = []
        self.launch_hook = None
        self.steps = {}
        self.step_calls = []
        self.exit_callbacks = []
        self.unregistered = []

    def launchAspects(self, aspects, joinpoint, phase, module, options,
                      extra=None):
        self.launches.append((aspects, joinpoint, phase, extra))
        if self.launch_hook is not None:
            self.launch_hook(aspects, joinpoint, phase, module, options, extra)

    def joinpoints(self):
        return [launch[1] for launch in self.launches]

    def launchStep(self, name, phase):
        self.step_calls.append((name, phase))
        return self.steps.get(name)

    def getPhase(self):
        return self.phase

    def registerBuildExitCallback(self, callback):
        self.exit_callbacks.append(callback)
        return len(self.exit_callbacks)

    def unregisterBuildExitCallback(self, regid):
        self.unregistered.append(regid)


class FakeEnv(object):
    def __init__(self, env=None, engine=None, substitutions=None):
        self.env = {} if env is None else env
        self.engine = FakeEngine() if engine is None else engine
        self.metadata = types.SimpleNamespace(getCurrent=lambda: None)
        self.substitutions = substitutions or {}

    def doSubstitutions(self, text):
        for old, new in self.substitutions.items():
            text = text.replace(old, new)
        return text

    def update(self, dictionary):
        self.env.update(dictionary)


class FakeFlowControl(object):
    """advice(name) answers from ``advice``: a bool, or a list consumed one
    answer per call (False once exhausted)."""

    def __init__(self, advice=None):
        self.scripted = dict(advice or {})
        self.issues = []
        self.overrides = []
        self.votes = []

    def initFlowControlIssue(self, name, description):
        self.issues.append(name)

    def advice(self, name):
        value = self.scripted.get(name, False)
        if isinstance(value, list):
            return value.pop(0) if value else False
        return value

    def override(self, name, value, who):
        self.overrides.append((name, value, who))

    def vote(self, name, value, who):
        self.votes.append((name, value, who))


def make_module(cls, env=None, **attrs):
    """An instance of a CsmakeModule subclass without running its __init__
    (which needs a whole csmake engine), wired to the fakes above."""
    module = object.__new__(cls)
    module.log = FakeLog()
    module.env = env if isinstance(env, FakeEnv) else FakeEnv(env)
    module.engine = module.env.engine
    module.settings = module.engine.settings
    module.flowcontrol = FakeFlowControl()
    for name, value in attrs.items():
        setattr(module, name, value)
    return module


def new_module(cls, env=None, log=None):
    """Build a module through its real __init__ using the fakes."""
    env = env if isinstance(env, FakeEnv) else FakeEnv(env)
    log = FakeLog() if log is None else log
    module = cls(env, log)
    module.flowcontrol = FakeFlowControl()
    return module


# ------------------------------------------------------------------- imports

def _stub_shell_modules():
    """Minimal stand-ins for csmake core's Shell, ShellAspect and
    ShellToEnvironment (what the DIB shell classes extend)."""
    from Csmake.CsmakeAspect import CsmakeAspect
    from Csmake.CsmakeModuleAllPhase import CsmakeModuleAllPhase

    class Shell(CsmakeModuleAllPhase):
        STARTING_ENVIRONMENT = {'stub': 'Shell._getStartingEnvironment'}

        def _getStartingEnvironment(self, options):
            return self.STARTING_ENVIRONMENT

    class ShellAspect(CsmakeAspect):
        def __init__(self, env, log):
            CsmakeAspect.__init__(self, env, log)
            self.shellaspect_initialised = True

        def _joinPointLookup(self, joinpoint, phase, options):
            return ('ShellAspect._joinPointLookup', joinpoint, phase)

    class ShellToEnvironment(CsmakeModuleAllPhase):
        def default(self, options):
            self.default_saw_environ = os.environ
            return True

    modules = {}
    for name, cls in (('Shell', Shell), ('ShellAspect', ShellAspect),
                      ('ShellToEnvironment', ShellToEnvironment)):
        module = types.ModuleType('CsmakeModules.' + name)
        setattr(module, name, cls)
        modules['CsmakeModules.' + name] = module
    return modules


_SHELL_STUBS = {}
_NEEDS_SHELL_STUBS = ('DIBShell', 'DIBShellAspect', 'DIBShellToEnvironment')


@contextlib.contextmanager
def _swapped_modules(replacements):
    saved = {}
    for name, module in replacements.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = module
    try:
        yield
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def _git_dependency_stubs():
    """DIBGetSourceRepositories/DIBRepo import GitPython and csmake-providers'
    GitProvider at import time; the tests mock GitProvider itself, so stand-ins
    are used for those modules when they are not installed."""
    stubs = {}
    try:
        import git  # noqa: F401
    except ImportError:
        stubs['git'] = types.ModuleType('git')
    try:
        from CsmakeProviders.GitProvider import GitProvider  # noqa: F401
    except ImportError:
        for leftover in ('CsmakeProviders', 'CsmakeProviders.GitProvider'):
            sys.modules.pop(leftover, None)
        package = types.ModuleType('CsmakeProviders')
        package.__path__ = []
        provider = types.ModuleType('CsmakeProviders.GitProvider')
        provider.GitProvider = type('GitProvider', (object,), {})
        stubs['CsmakeProviders'] = package
        stubs['CsmakeProviders.GitProvider'] = provider
    return stubs


def import_dib(name):
    """Import CsmakeModules.<name>, SkipTest-ing only if csmake core itself is
    unavailable.  Third party / other-repo dependencies are replaced by stubs
    for the duration of the import."""
    csmake_alias.install()
    try:
        replacements = _git_dependency_stubs()
        if name in _NEEDS_SHELL_STUBS:
            if not _SHELL_STUBS:
                _SHELL_STUBS.update(_stub_shell_modules())
            replacements.update(_SHELL_STUBS)
        with _swapped_modules(replacements):
            return importlib.import_module('CsmakeModules.' + name)
    except ImportError as e:
        missing = (e.name or '').split('.')[0]
        if missing in EXTERNAL_PACKAGES:
            raise unittest.SkipTest(
                "cannot import CsmakeModules.%s: %s" % (name, e))
        raise
