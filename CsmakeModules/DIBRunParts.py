# <copyright>
# (c) Copyright 2017 Hewlett Packard Enterprise Development LP
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
from Csmake.CsmakeModule import CsmakeModule
from Csmake.ShellPassEnv import ShellPassEnv
import subprocess
import os.path
import argparse
import collections
import os
import sys
import pickle
import base64
import re

class DIBRunParts(CsmakeModule):
    """Purpose: To mimic the run-dib-parts, but allowing for
           retry and modification (via aspects)
           A DIBInit and any DIBRepo steps should be run prior to executing
           this step.
       Phases: build, clean
       Flags:
           scripts - The script directory to execute
           source - True if the scripts in this phase are only
                    sourced and not themselves executed
           ignore-progress - True if the step should be started
                             from the beginning.
           shell - Full path to the shell to use to execute the scripts
                   If not specified, /bin/bash is used.
           in-image - If True, the image is mounted and execution chrooted
                      Default is false.
           filter - Regular expression that states the kinds of files the
                    runparts step will accept
                    default filter is ^([0-9]*)[0-9A-Za-z_-]+$
                    The grouping in the front allows for 
                    sorting by the number the filename leads with in
                    a natural way.  Leaving this grouping off will
                    cause the scripts to be ordered lexigraphically

       Joinpoints introduced:
           script_skip - called when a script is being avoided
           script_start - called when the script is being started
           script_passed - called when the script succeeds
           script_failed - called when a script fails
           script_exception - called when a script generates an exception
           script_end - called when a script ends

       Flowcontrol Advice introduced:
           doNotStartScript - This advice is checked before the script is
                              started and after the script_start joinpoint
                              If it is set to True, the script will be skipped
                              NOTE: This occurs after any avoidance logic
                                    thus will not cause the script_skip
                                    joinpoint (otherwise this would have 
                                     non-useful semantics, generally)
           tryScriptAgain - This advice is checked after script_passed or
                            script_failed joinpoint.
                              If it is set to True, the script will reexecute
           doNotAvoidScript - This advice is checked after script_skip joinpoint
                               If it is set to True, the script will not be
                               avoided

       Notes:
           aspects can register themselves by appending themselves
            to the options with the name of the script starting with
            two underbars '__', e.g., "__05-debian-systemd")
            They must append a list so that multiple aspects can
            be registered.  Order of aspect execution cannot be 
            guaranteed, in fact aspects shouldn't know or depend on
            each other, so if there is a dependency between two
            aspects, a new aspect should be created.  The join point
            protocol, beyond the usual join points, are script_start,
            script_end, script_passed, script_failed.

          aspects registered under '__*' will be used in every step.

            Use of any aspect should be only to provide a temporary
            workaround and indicates a need for the element or environment
            deficiency to be fixed."""

    @staticmethod
    def lookupAspects(options, partName):
        path, script = os.path.split(partName)
        result = []
        if '__*' in options:
            result.extend(options['__*'])
        partLookupName = '__%s' % script
        if partLookupName in options:
            result.extend(options[partLookupName])
        return result

    def build(self, options):
        execer = ShellPassEnv()

        self.dibenv = self.env.env['__DIBEnv__']
        self.newenv = self.dibenv['shellenv']
        self.inImage = False
        if 'in-image' in options:
            self.inImage = options['in-image'] == "True"
        if self.inImage:
            self.log.info("Commands will be executed from the image's context")
        else:
            self.log.info("Commands will be executed from the build machine's context")

        self.scriptFilter = r"^([0-9]*)[0-9A-Za-z_-]+$"
        if 'filter' in options:
            self.scriptFilter = options['filter'].strip()

        self.scriptTarget = options['scripts'].strip()
        if 'shell' in options:
            self.shellexe = self.env.doSubstitutions(options['shell'])
        else:
            self.shellexe = '/bin/bash'

        scriptsCompleted = []
        ignoreProgress = False
        if 'ignore-progress' in options:
            ignoreProgress = options['ignore-progress'].strip() == 'True'
        if not ignoreProgress and \
            self.dibenv['logconfig'].has_section(self.scriptTarget):
            scriptsCompleted = self.dibenv['logconfig'].options(self.scriptTarget)
        else:
            self.log.devdebug("Build avoidance sections were: %s", str(self.dibenv['logconfig'].sections()))
            self.log.devdebug("Section referenced is: %s", self.scriptTarget)
            self.log.devdebug('Build avoidance information does not exist or is bypassed.')
        if not self.dibenv['logconfig'].has_section(self.scriptTarget):
            self.dibenv['logconfig'].add_section(self.scriptTarget)
        if len(scriptsCompleted) == 0:
            self.log.info('Build avoidance detects the following scripts as completed: %s', str(scriptsCompleted))

        # Find all the script sections dib parts under the 'scripts' directories
        self.hooksdir = self.dibenv['hooksdir']
        currentPath = os.path.join(self.hooksdir,self.scriptTarget) 
        if not os.path.exists(currentPath):
            self.log.info("There are no scripts for section: %s", self.scriptTarget)
            success = True
            filelist = []
        else:
            filelist = os.listdir(currentPath)

        self.source = False
        if 'source' in options:
            self.source = options['source'] == 'True'

        scripts = []
        for fileitem in filelist:
            currentScript = os.path.join(currentPath, fileitem)
            if self.source \
               or ( os.access(currentScript, os.X_OK)
                   and not os.path.isdir(currentScript) ):
                m = re.match(
                    self.scriptFilter,
                    fileitem )
                if m is not None:
                    num = ''
                    try:
                        num = int(m.groups()[0])
                    except:
                        pass
                    scripts.append((num, fileitem))
                else:
                    self.log.warning("Skipping executable file: %s", fileitem)
        #Numbered scripts sort first (numerically), then unnumbered scripts
        # by name.  (Python 3 can't compare the int prefix with the '' that
        # unnumbered scripts get, so sort on an explicit key.)
        scripts.sort(key=lambda item: (item[0] == '', item[0] or 0, item[1]))

        #Drive through the list of parts
        success = True

        self.command = [
            self.shellexe,
            '-xc' ]
                
        if 'TMPDIR' in self.newenv:
            envTmpDir = self.newenv['TMPDIR']
        else:
            envTmpDir = None

        self.commandPrefix = '%s'
        self.scriptPath = os.path.join(
            self.hooksdir,
            self.scriptTarget )
        envHome = self.newenv['HOME']
        if self.source:
            self.commandPrefix = 'source %s'

        #TODO: Consider pulling the "pseudo mount" out into its own module
        #   Then the build spec would be
        #    [DIBpseudomount@blah]
        #    [DIBRunParts@blahblah]
        #    [DIBRunParts@thisblah]
        #    [DIBpseudoumount@blah]
        if self.inImage:
            self.command = [
                'sudo',
                '-E',
                'chroot',
                self.newenv['TMP_MOUNT_PATH'],
                self.shellexe,
                '-xc' ]
            self.newenv['HOME'] = '/root'
            if 'TMPDIR' in self.newenv:
                del self.newenv['TMPDIR'] 
            self.commandPrefix = 'PATH=%s:/usr/local/bin; %s' % (
                self.newenv['PATH'],
                self.commandPrefix )
            self.scriptPath = os.path.join(
                '/tmp/in_target.d',
                self.scriptTarget )
            cmd =['sudo', 
                'mkdir', '%s/tmp/in_target.d' % self.newenv['TMP_MOUNT_PATH'] ]
            result = subprocess.call(
                cmd,
                stdout=self.log.out(),
                stderr=self.log.err())
            if result != 0:
                self.log.error("mkdir for special mountpoint failed (%d)", result)
                self.log.devdebug('   command was: %s' % str(cmd))
                self.log.failed()
                return None
            result = subprocess.call(
                ['sudo', 'mount', '--bind',
                    self.newenv['TMP_HOOKS_PATH'],
                    '%s/tmp/in_target.d' % self.newenv['TMP_MOUNT_PATH']  ],
                stdout=self.log.out(),
                stderr=self.log.err() )
            if result != 0:
                self.log.error('mount bind for special mountpoint failed (%d)', result)
                self.log.failed()
                return None
            result = subprocess.call(
                ['sudo', 'mount', '-o', 'remount,ro,bind',
                    self.newenv['TMP_HOOKS_PATH'],
                    '%s/tmp/in_target.d' % self.newenv['TMP_MOUNT_PATH'] ],
                stdout=self.log.out(),
                stderr=self.log.err() )
            if result != 0:
                self.log.error('mount for special mountpoint failed (%d)', result)
                self.log.failed()
                return None
            self.newenv['DIB_HOOKS'] = '/tmp/in_target.d'
            chrootProcPath = os.path.join(
                self.newenv['TMP_MOUNT_PATH'],
                'proc' )
            if not os.path.exists(chrootProcPath):
                result = subprocess.call(
                    ['sudo', 'mkdir', chrootProcPath],
                    stdout=self.log.out(),
                    stderr=self.log.err() )
            result = subprocess.call(
                ['sudo', 'mount', '--bind', '/proc', chrootProcPath],
                stdout=self.log.out(),
                stderr=self.log.err() )
            if result != 0:
                self.log.error("mount bind for the proc mountpoint failed (%d)",
                     result )
                self.log.failed()
                return None

        try:
            for num, script in scripts:
                if not success and not self.settings['keep-going']:
                    self.log.info("Bailing out of run parts due to failure")
                    break
                self.flowcontrol.initFlowControlIssue(
                    "doNotStartScript",
                    "Tells DIBRunParts to not run a script" )
                self.flowcontrol.initFlowControlIssue(
                    "tryScriptAgain",
                    "Tells DIBRunParts to try the script again")
                self.flowcontrol.initFlowControlIssue(
                    "doNotAvoidScript",
                    "Tells DIBRunParts to ignore build avoidance")

                aspects = self.lookupAspects(options, script)
                self.log.devdebug("Processing script: %s", script)
                if script in scriptsCompleted:
                    self.engine.launchAspects(
                        aspects,
                        'script_skip',
                        'build',
                        self,
                        options)
                    if not self.flowcontrol.advice("doNotAvoidScript"):
                        self.log.devdebug("---- Avoiding script '%s' as completed", script)
                        continue
                    else:
                        self.log.devdebug("++++ Repeating script: '%s' even though completed", script)
                self.engine.launchAspects(
                    aspects,
                    'script_start',
                    'build',
                    self,
                    options)

                if self.flowcontrol.advice("doNotStartScript"):
                    self.log.info("==== Execution of the script was preempted by an aspect")
                    continue
                tryagain = True
                while tryagain:
                    try:
                        tryagain = False

                        shell = ShellPassEnv()
                        fullcommand = self.command + [
                            self.commandPrefix % os.path.join(
                                self.scriptPath,
                                script) ] 
                        self.log.info("~~ Script '%s' starting ~~" % script)
                        result, resultenv  = shell.call(
                            fullcommand,
                            self.log.out(), 
                            self.log.err(), 
                            self.newenv,
                            shell=False)

                        if result == 0:
                            self.log.info("~~ Script '%s' succeeded ~~" % script)
                            self.newenv.update(resultenv)
                            self.dibenv['logconfig'].set(
                                self.scriptTarget,
                                script,
                                '')
                            self.log.devdebug("()()  Recorded completion of %s %s in log  ()()", self.scriptTarget, script)
                       
                            self.engine.launchAspects(
                                aspects,
                                'script_passed',
                                'build',
                                self,
                                options)
                            tryagain = self.flowcontrol.advice("tryScriptAgain")
                        else:
                            self.log.error(
                                ">> Script '%s' failed with error code: %d <<",
                                script,
                                result )
                            self.engine.launchAspects(
                                aspects,
                                'script_failed',
                                'build',
                                self,
                                options)
                            tryagain = self.flowcontrol.advice("tryScriptAgain")
                            if not tryagain:
                                success = False
                                raise SystemError("Script '%s' failed" % script)
                    except Exception as e:
                        self.engine.launchAspects(
                            aspects,
                            'script_exception',
                            'build',
                            self,
                            options,
                            {'__ex__' : e} )
                        tryagain = self.flowcontrol.advice("tryScriptAgain")
                        if not tryagain:
                            self.log.info("Not trying again on step exception")
                            raise e
                    finally:
                        if not tryagain:
                            self.engine.launchAspects(
                                aspects,
                                'script_end',
                                'build',
                                self,
                                options)
 
        except:
            success = False
            self.log.exception('DIBRunParts exited on exception')
        finally:
            self.dibenv['logconfig'].set(
                'environment',
                'pickle',
                base64.b64encode(pickle.dumps(self.newenv)).decode('ascii'))
            self.log.devdebug("()()  Writing DIB Progress logfile  ()()")
            with open(self.dibenv['logfile'], 'w') as f:
                self.dibenv['logconfig'].write(f)
                f.close()
            self.log.devdebug("Wrote logfile")
            self.newenv['DIB_HOOKS']=self.newenv['TMP_HOOKS_PATH']
            if self.inImage:
                #Undo mount, undo HOME, TMPDIR
                self.newenv['HOME'] = envHome
                if envTmpDir is not None:
                    self.newenv['TMPDIR'] = envTmpDir
                result = subprocess.call(
                    ['sudo', 'umount', '-f', 
                     '%s/tmp/in_target.d' % self.newenv['TMP_MOUNT_PATH'] ],
                    stdout=self.log.out(),
                    stderr=self.log.err() )
                if result != 0:
                    self.log.error('umount for special mountpoint failed')
                    success = False
                result = subprocess.call(
                    ['sudo', 'rmdir',
                     '%s/tmp/in_target.d' % self.newenv['TMP_MOUNT_PATH'] ],
                    stdout=self.log.out(),
                    stderr=self.log.err() )
                if result != 0:
                    self.log.error('rmdir of special mountpoint failed')
                    success = False
                chrootProcPath = os.path.join(
                    self.newenv['TMP_MOUNT_PATH'],
                    'proc' )
                result = subprocess.call(
                    ['sudo', 'umount', chrootProcPath],
                    stdout=self.log.out(),
                    stderr=self.log.err() )
                if result != 0:
                    self.log.error("umount bind for the proc mountpoint failed (%d)",
                     result )
                    success = False
                if os.path.exists(chrootProcPath):
                    result = subprocess.call(
                        ['sudo', 'rmdir', chrootProcPath ],
                        stdout= self.log.out(),
                        stderr= self.log.err() )

        self.log.devdebug("success is %s", str(success))
        if success:
            self.log.passed()
        else:
            self.log.failed()
        return success
