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
from CsmakeCore.CsmakeModule import CsmakeModule
from CsmakeProviders.GitProvider import GitProvider
from CsmakeModules.DIBRunParts import DIBRunParts
import os.path
import git
import shutil
import os
import re
import subprocess
import tempfile
import urllib.parse as urlparse
from datetime import datetime

class DIBGetSourceRepositories(CsmakeModule):
    """Purpose: Download DIB element source repositories for a DIB image build
           Source modules will only be loaded once (unless behavior is
           otherwise modified by one or more aspects) tracked with the
           progress log.
           There is no guaranteed order of pulling - all source-repository-*
           definitions should assume and be independent.
           A source-repository that ends with a tilde '`' is supposed to be
           ignored.
       Commands:
           build - (default) will download the element specified source repos
           clean - will remove the element specified repos from local storage
       Options:
           default-url-base - will overwrite the url network address
                              for any default source repo
                              overrides will overried this default as well
                              as the standard defaults
                              (only use this if you know what you are doing)
       Script JoinPoints: script_skip, script_start, script_passed, 
                          script_failed, script_exception, script_end
           These join points are advised for each source repository
           This module is designed to work with DIBRunPartsAspects
           "source" will be passed as an extra aspect option for script_* 
               joinpoints and is a 5-ple of:
                   ('name', 'type', 'local', 'URL', 'ref')
           "pulled" will be passed as an extra aspect option for script_passed
               - is dependent on the 'type'
                 git - ((oldsha, newsha), status, message)
                 file,tar - (path/to/download, sha-1, utc-datetime-iso8601)
                 * tarfile will be deleted after source fetch completes
       Flowcontrol Advice introduced:
           doNotStartScript
           tryScriptAgain
           doNotAvoidScript
           doNotDeleteTemp
       Dependencies:
           GitPython>=0.3.6 (debian: python-git)
               http://tabasco0.ftc.hpeswlab.net/build_tools/python-git-0.3.6+hp-cs-1.0.deb
               http://gitpython.readthedocs.org/"""

    def build(self, options):
        dibenv = self.env.env['__DIBEnv__']
        overrides = dibenv['source-repository-overrides']
        self.log.devdebug("Overrides will be for: %s", str(overrides))
        scriptFilter = re.compile(r'^source-repository-.*[^~]$')
        
        #Gather all the source-repository sections
        hooksdir = dibenv['hooksdir']
        filelist = os.listdir(hooksdir)
        repofiles = []
        for fileitem in filelist:
            tested = scriptFilter.match(fileitem)
            if tested is not None:
                currentRepo = os.path.join(hooksdir, fileitem)
                if os.path.isfile(currentRepo):
                    repofiles.append(currentRepo)
        
        #Dig through and modify source-repo definitions
        sourceRepos = []
        repoEntry = ('name', 'type', 'local', 'URL', 'ref')
        self.log.devdebug("Source repo lines are: %s", str(repofiles))
        for repopath in repofiles:
            repolines = None
            try:
                with open(repopath) as repofile:
                    repolines = repofile.readlines()
            except:
                self.log.exception("source-repository %s failed to open",
                    repopath )
                self.log.failed()
                return None
            if repolines is None:
                self.log.error("source-repository %s failed to open",
                    repopath )
            for repoline in repolines:
                repoline = repoline.strip()
                if len(repoline) == 0 or repoline.startswith('#'):
                    self.log.warning("Empty line or comment character found in source-repository %s",
                        repopath )
                    self.log.warning("The (scant) source-repositories documentation makes no mention that these are acceptable in a source-repository spec. YMMV")
                    continue
                repoparts = repoline.split()
                if len(repoparts) == 4:
                    repoparts.append('*')
                if len(repoparts) != 5:
                    self.log.error(
                        "source-repository file '%s' did not provide all of the required data: %s", 
                        repoline)
                    #TODO: look up the element if getfattr is installed
                    self.log.failed()
                    return None
                repo = dict(zip(repoEntry, repoparts))
                self.log.info("Default source-repository '%s': %s",
                    repopath,
                    str(repoparts) ) 
                if 'default-url-base' in options:
                    defaultURL = self.env.doSubstitutions(options['default-url-base'])
                    self.log.info("substituting %s in place of base for %s",
                        defaultURL,
                        repo['URL'] )
                    parsed = urlparse.urlsplit(repo['URL'])
                    defaultParsed = urlparse.urlsplit(defaultURL)
                    fixup = urlparse.urlunsplit(defaultParsed[:2] + parsed[2:])
                    self.log.info("--- new url is: %s", fixup)
                    repo['URL'] = fixup
                    
                reponame = repo['name']
                altreponame = reponame.replace('-', '_')
                self.log.devdebug("Repo name to check: '%s'", reponame)
                self.log.devdebug("Alternate repo name to check: '%s'", altreponame)
                self.log.devdebug("   Current repo: %s", str(repo))
                repokey = None
                if reponame in overrides:
                    repokey = reponame
                elif altreponame in overrides:
                    repokey = altreponame
                if repokey is not None:
                    self.log.devdebug("Name '%s' was in overrides", repokey)
                    override = overrides[repokey]
                    self.log.info("Overriding source-repository: %s",
                        str(override) )
                    if 'URLbase' in override:
                        self.log.info("overriding with base %s for %s",
                            override['URLbase'],
                            repo['URL'] )
                        parsed = urlparse.urlsplit(repo['URL'])
                        overrideParsed = urlparse.urlsplit(override['URLbase'])
                        fixup = urlparse.urlunsplit(
                            overrideParsed[:2] + parsed[2:])
                        self.log.info('--- new url is: %s', fixup)
                        repo['URL'] = fixup
                    repo.update(override)
                else:
                    self.log.devdebug("~~~Name was not in overrides~~~")
                sourceRepos.append(repo)

        reposCompleted = []
        if dibenv['logconfig'].has_section('DIBGetSourceRepositories'):
            reposCompleted = dibenv['logconfig'].options('DIBGetSourceRepositories')
        else:
            dibenv['logconfig'].add_section('DIBGetSourceRepositories')

        #Run through all the parts 
        tempdir = None
        failed = False
        try:
            for source in sourceRepos:
                if failed and not self.settings['keep-going']:
                    self.log.info("Bailing out of source repo grabbing because of failure")
                    break
                self.flowcontrol.initFlowControlIssue(
                    "doNotStartScript",
                    "Tells DIBGetSourceRepositories to not run a script" )
                self.flowcontrol.initFlowControlIssue(
                    "tryScriptAgain",
                    "Tells DIBGetSourceRepositories to try the script again")
                self.flowcontrol.initFlowControlIssue(
                    "doNotAvoidScript",
                    "Tells DIBGetSourceRepositories to ignore build avoidance")
                self.flowcontrol.initFlowControlIssue(
                    "doNotDeleteTemp",
                    "Tells DIBGetSourceRepositories to leave the temp file")

                #Find all aspects for each step like RunParts
                sourceName = source['name']
                aspects = DIBRunParts.lookupAspects(options, sourceName)
                self.log.devdebug("Processing source-repository: %s", sourceName)
                if sourceName in reposCompleted:
                    self.engine.launchAspects(
                        aspects,
                        'script_skip',
                        'build',
                        self,
                        options,
                        {'source' : source} )
                    if not self.flowcontrol.advice("doNotAvoidScript"):
                        self.log.devdebug("----Avoiding repo '%s' as completed",
                            sourceName )
                        continue
                    else:
                        self.log.devdebug("++++Repeating repo '%s' even though completed",
                             sourceName )
                self.engine.launchAspects(
                    aspects,
                    'script_start',
                    'build',
                    self,
                    options,
                    {'source' : source})
                if self.flowcontrol.advice("doNotStartScript"):
                    self.log.info("==== Loading the repo was preempted by an aspect")
                    continue
                pullResult = None
                deleteMe = None
                tryagain = True
                while tryagain:
                    try:
                        tryagain = False

                        imageTargetPath, targetRepo = os.path.split(
                            source['local'])
                        #Root slash messes with os.path.join and other functions
                        if imageTargetPath[0] == '/':
                            imageTargetPath = imageTargetPath[1:]
                        targetRoot = self.env.env['TARGET_ROOT']
                        targetPath = os.path.join(
                            targetRoot,
                            imageTargetPath )
                        self.log.devdebug(
                            "targetPath for injection: %s", targetPath)
                        self.log.devdebug(
                            "imageTargetPath: %s", imageTargetPath)
                        self.log.devdebug(
                            "targetRepo: %s", targetRepo )
                        tempdir = tempfile.mkdtemp()
                        self.log.devdebug(
                            "tempdir: %s", tempdir )

                        #Create any parts that weren't already created
                        if not os.path.exists(targetPath):
                            result = subprocess.call(
                                ['sudo', 'mkdir', '-p', targetPath ] )
                            if result != 0:
                                self.log.error(
                                    "Could not make directory for repo")
                                failed = True
                                continue

                        #GIT REPO
                        if source['type'] == 'git':
                            ref = source['ref']
                            if ref == '*':
                                ref = 'master'
                            self.log.info("Fetching %s from git: %s:%s",
                                sourceName,
                                source['URL'],
                                ref )
                            
                            reftype = None
                            if 'reftype' in source:
                                reftype = source['reftype']
                            name, localRepo = GitProvider.determineRepoPath(
                                tempdir, 
                                targetRepo )
                            pullResult = GitProvider.fetchRepository(
                                self.log,
                                sourceName, 
                                localRepo,
                                source['URL'],
                                ref,
                                reftype,
                                dibenv['shellenv'] )
                            passed, status, msg = pullResult
                            if not passed:
                                self.log.error(msg)
                                failed = True
                                continue

                            #NOTE: Believe it or not pbr barfs if you do this
                            #....sad.
                            #shutil.rmtree(
                            #    os.path.join(
                            #        tempdir,
                            #        targetRepo,
                            #        '.git' ) )

                            result = subprocess.call(
                                [ 'sudo', '-E', 'rsync', '-a', '--remove-source-files', localRepo, targetPath ],
                                stdout=self.log.out(),
                                stderr=self.log.err(),
                                env=dibenv['shellenv'] )
                            if result != 0:
                                self.log.error("GIT repo failed to inject")
                                failed = True
                                continue
                            
                        #TAR REPO
                        elif source['type'] == 'tar':
                            temptarball = tempfile.mkdtemp() + "/temp.tgz"
                            self.log.info("Fetching %s from tar: %s:%s",
                                sourceName,
                                source['URL'],
                                source['ref'] )

                            result = subprocess.call(
                                'sudo -E curl %s -o %s' % (
                                    source['URL'],
                                    temptarball), 
                                shell=True,
                                stdout=self.log.out(),
                                stderr=self.log.err(),
                                env=dibenv['shellenv'] )
                            if result != 0:
                                self.log.error("TAR repo failed to download")
                                failed = True
                                continue
                            sha = None
                            with open(temptarball, 'rb') as myfile:
                                sha = self._fileSHA1(myfile)
                            downloadTime = datetime.utcnow().isoformat()
                            result = subprocess.call(
                                'sudo -E tar -C %s -xzf %s' % (
                                     tempdir,
                                     temptarball ),
                                shell=True,
                                stdout=self.log.out(),
                                stderr=self.log.err(),
                                env=dibenv['shellenv'] )
                            if result != 0:
                                self.log.error("TAR repo failed to download")
                                failed = True
                                continue

                            repofiles = []
                            sourcepull = os.path.join(
                                tempdir,
                                source['ref'],
                                '.' )
                            targetdrop = os.path.join(
                                targetPath,
                                targetRepo )
                              
                            proc = subprocess.Popen(
                                [ 'sudo', '-E', 'rsync', '-av', '--remove-source-files',
                                sourcepull,
                                targetdrop ],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                env=dibenv['shellenv'],
                                universal_newlines=True )
                            out, err = proc.communicate()
                            if proc.returncode != 0:
                                self.log.error("TAR repo failed to inject")
                                failed = True
                                continue
                            
                            repofiles = []
                            lines = out.split('\n')
                            start = False
                            for line in lines:
                                line = line.strip()
                                if not start:
                                    start = line == './'
                                    continue
                                else:
                                    if len(line) == 0:
                                        break
                                repofiles.append(line)
                            pullResult = (
                                targetdrop, repofiles, sha, downloadTime, temptarball)
                            deleteMe = temptarball

                        #FILE
                        elif source['type'] == 'file':
                            self.log.info("Pulling '%s' as a file", sourceName)
                            filepath = os.path.join(
                                targetPath,
                                targetRepo )
                            cmd = [ 'sudo', '-E', 'curl', source['URL'], 
                                         '-o', filepath ]
                            self.log.devdebug('File command is: %s', str(cmd))
                            if 'http_proxy' in dibenv['shellenv']:
                                self.log.debug("http_proxy is: %s", dibenv['shellenv']['http_proxy'])
                            if 'HTTPS_PROXY' in dibenv['shellenv']:
                                self.log.debug("HTTPS_PROXY is: %s", dibenv['shellenv']['HTTPS_PROXY'])
                            if 'NO_PROXY' in dibenv['shellenv']:
                                self.log.debug("NO_PROXY is: %s", dibenv['shellenv']['NO_PROXY'])
                            result = subprocess.call(
                                cmd,
                                stdout=self.log.out(),
                                stderr=self.log.err(),
                                env=dibenv['shellenv'] )
                            if result != 0:
                                self.log.error("FILE repo failed to download and inject")
                                failed = True
                                continue
                            sha = None
                            with open(filepath, 'rb') as myfile:
                                sha = self._fileSHA1(myfile)
                            pullResult = (
                                filepath, sha, datetime.utcnow().isoformat())
                        else:
                            self.log.error("Incorrect repo type")
                            failed = True
                            continue
                    except Exception as e:
                        self.log.exception(
                            "Repo failed to load with exception" )
                        self.engine.launchAspects(
                            aspects,
                            'script_exception',
                            'build',
                             self,
                             options,
                             {'__ex__' : e,
                              'source' : source } )
                        tryagain = self.flowcontrol.advice("tryScriptAgain")
                        if not tryagain:
                            failed = True
                            self.log.info("Not trying repo again")
                            raise e
                    finally:
                        if not tryagain:
                            if failed:
                                self.engine.launchAspects(
                                    aspects,
                                    "script_failed",
                                    'build',
                                    self,
                                    options,
                                    {'source' : source} )
                                self.log.error(
                                    "Repo '%s' failed with errors",
                                    sourceName )
                                tryagain = self.flowcontrol.advice("tryScriptAgain")
                            else:
                                dibenv['logconfig'].set(
                                    'DIBGetSourceRepositories',
                                    sourceName,
                                    '')
                                self.engine.launchAspects(
                                    aspects,
                                    "script_passed",
                                    "build",
                                    self,
                                    options,
                                    {'source' : source,
                                     'pulled' : pullResult } )
                                tryagain = self.flowcontrol.advice("tryScriptAgain")
                            try:
                                if deleteMe is not None:
                                    result = subprocess.call(
                                        ['sudo', '-E', 'rm', '-rf', deleteMe],
                                        stdout=self.log.out(),
                                        stderr=self.log.err(),
                                        env=dibenv['shellenv'] )
                                    deleteMe = None
                                    if result != 0:
                                        raise ValueError("Result: %d" % result)
                            except:
                                self.log.exception(
                                    "Failed to delete temporary tar: %s", 
                                    deleteMe )

                self.engine.launchAspects(
                    aspects,
                    'script_end',
                    'build',
                    self,
                    options,
                    {'source' : source} )
        except:
            failed = True
            self.log.exception("Source repo loading exited on exception")
        finally:
            if tempdir is not None and \
                not self.flowcontrol.advice('doNotDeleteTemp'):
                shutil.rmtree(tempdir)
            with open(dibenv['logfile'], 'w') as f:
                dibenv['logconfig'].write(f)
            
        if failed:
            self.log.failed()
        else:
            self.log.passed()
        return not failed
