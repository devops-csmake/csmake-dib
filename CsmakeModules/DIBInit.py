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
import os.path
import argparse
import collections
import os
import sys
import subprocess
import shutil
import io
import configparser
import pickle
import base64

class DIBInit(CsmakeModule):
    """Purpose: Prepare to process elements for a disk-image-builder run
                Establishes the following environment:
                __DIBEnv__ = Dictionary of options passed to DIBInit
                    shellenv: Dictionary for the environment for scripts, etc.
                              executed in the DIB environment
                    result-image-name: Name of the result image
                          (Used to interpret what build is being executed)
                    logconfig: The ConfigParser object that maintains the
                               status of the build avoidance
                    logfile: Contains the path and filename of where the
                               config log should be written or read
                               DIBInit will do the proper gluing
                               of the prrevious image and the current image
                               status so that the logconfig has the right
                               information and is the only necessary source
                               for information
                    source-repository-overrides: definitions of overrides
                             for source repositories pulled.

       Phases: build, clean
       Flags:
           start-image - The image to base this DIB run off of.
                         For a new image, do not set this flag.
           start-dir  - The directory where the start is located
           result-image  - The output image name
           result-dir  - The directory for the result
                         * Specify different from start-dir (not enforced)
           build-dir   - The location to build the new image
                         If unspecified, will use:
                          <result-dir>.build-<result-image>
           root-label  - The label of the root disk
                         Must match the DIBPrepDisk or similar functioning
                         section's idea of a root label
                         This defines DIB_ROOT_LABEL
                         NOTE: xfs has a 12 character limit
           arch    - The architecture for target the image 
                     * Defined only in a fresh init
           release - Release of the specified os (e.g., 'cattleprod' on hlinux)
                     * Defined only in a fresh init
           env     - ShellEnv Environments to add to the execution context
                        Comma delimited refered to by id or module@id.
                     * nth phase builds will get the previous env
           repos   - A newline and comma delimited list of DIBRepo ids
           dibPath - Path to the diskimage-builder project directory.
           elements - list of all required elements
                      ',' or newlines may be used to delimit the elements.
           elements-path - paths to use to follow all the elements. ':' or
                          newlines may be used to delimit the paths.
                          A leading colon implies the current working
                          directory %(WORKING)s/Elements starts in front.
           no-deps - (OPTIONAL) when True dependencies are not collected.
                          default is False.
           build-avoidance-ignore -
                     Lines of <element>:<path-to-elements-file>
                     Where element describes the element that the
                        file is coming from, and
                           path-to-elements-file is the file or directory
                           to ignore for comparison purposes
        Defines (in the shell environment and the csmake environment):
            * Anything defined in the "env" declarations ... plus:
            TARGET_ROOT: <build-dir>/build
            TMP_MOUNT_PATH: <build-dir>/build
            DIB_IMAGE_CACHE: <cache-path>
                If cache-path doesn't start with a '/' then cwd is appended
            ARCH: <arch>
            TMP_HOOKS_PATH: <build-dir>/hooks
            DIB_RELEASE: <release>
            IMAGE_ELEMENT: <elements> and all dependents (joined by spaces)
            ELEMENT_PATH: <elements-path> (expanded as described above)
            DIB_ROOT_LABEL: defined by disk's DIBPrepDisk section
            BUILD_DIB_DIR: <build-dir>
            RESULT_DIB_DIR: <result-dir>
            DIB_HOOKS: This will track where the hooks are when the various
                       run parts, etc is running, so it will be
                       correct in both a non-chrooted and chrooted environment
           
        Optionally Requires:
            attr(5) - sudo apt-get install attr
                This will allow tracking of elements
                You must run a clean before building after this package is
                installed or you will get inconsistent builds.
                installation of attr will help with build avoidance
                and will also allow you to track scripts back to their elements
                'getfattr -n user.element <script>' will show the element
                   that the script came from.
            """
    def __init__(self, env, log):
        CsmakeModule.__init__(self, env, log)
        self.usefattr = self._isfattrInstalled()

    ### Code from diskimage_builder/element_dependencies.py
    def _get_set(self, element, fname):
        for path in self.dibenv['elements-path']:
            element_deps_path = os.path.join(path, element, fname)
            try:
                with open(element_deps_path) as element_deps:
                    return set([line.strip() for line in element_deps])
            except IOError as e:
                if os.path.exists(os.path.join(path, element)) and e.errno == 2:
                    return set()
                if e.errno == 2:
                    continue
                else:
                    raise

        self.log.error("Element '%s' not found", element)
                        
        return set()
    
    
    def _provides(self, element, elements_dir=None):
        """Return the set of elements provided by the specified element.
    
        :param element: name of a single element
        :param elements_dir: the elements dir to read from. If not supplied,
                             inferred by calling get_elements_dir().
    
        :return: a set just containing all elements that the specified element
                 provides.
        """
        return self._get_set(element, 'element-provides')


    def _dependencies(self, element):
        """Return the non-transitive set of dependencies for a single element.
    
        :param element: name of a single element
        :param elements_dir: the elements dir to read from. If not supplied,
                             inferred by calling get_elements_dir().

        :return: a set just containing all elements that the specified element
                 depends on.
        """
        return self._get_set(element, 'element-deps')


    def _expandDependencies(self, user_elements):
        """Expand user requested elements using element-deps files.

        Arguments:
        :param user_elements: iterable enumerating the elements a user requested
        :param elements_dir: the elements dir to read from. Passed directly to
                         dependencies()

        :return: a set containing user_elements and all dependent elements
                 including any transitive dependencies.
        """
        final_elements = set(user_elements)
        check_queue = collections.deque(user_elements)
        provided = set()

        while check_queue:
            # bug #1303911 - run through the provided elements first to avoid
            # adding unwanted dependencies and looking for virtual elements
            element = check_queue.popleft()
            if element in provided:
                continue
            deps = self._dependencies(element)
            provided.update(self._provides(element))
            check_queue.extend(deps - (final_elements | provided))
            final_elements.update(deps)

        conflicts = set(user_elements) & provided
        if conflicts:
            self.log.error(
                "Following elements were explicitly required "
                    "but are provided by other included elements: %s", 
                ", ".join(conflicts))
            #TODO: Exception
            return None
        return final_elements - provided

    ### End code provided by diskimage_builder/element_dependencies.py

    def _setfattrElement(self, filename, element):
        result = subprocess.call(
            'sudo setfattr -n user.element -v %s %s' % (
                element,
                filename ),
            stdout=self.log.out(),
            stderr=self.log.err(),
            shell=True )
        return result == 0

    def _getfattrElement(self, filename):
        stdoutstring = io.StringIO()
        try:
            result = subprocess.call(
                'getfattr -n user.element %s' % filename,
                stdout=stdoutstring,
                stderr=self.log.err(),
                shell=True)
            if result == 0:
                value = stdoutstring.getvalue()
                if '=' in value:
                    return value.split('=')[1].strip('"')
        finally:
            stdoutstring.close()
        return None

    def _isfattrInstalled(self):
        result = subprocess.call(
            'which getfattr',
            shell=True )
        return result == 0
 
    def _isElementDirty(self, element, elementpath, hookspath):
        ignoring = []
        self.log.devdebug("isElementDirty: %s", element)
        if element in self.buildAvoidanceIgnore:
            ignoring = self.buildAvoidanceIgnore[element]
        if len(ignoring) > 0:
            self.log.devdebug("should be ignoring: %s", str(ignoring))
        self.log.devdebug
        for workingpath in os.listdir(elementpath):
            workingElementpath = os.path.join(
                elementpath,
                workingpath )
            if os.path.isfile(workingElementpath):
                continue
            targetElementpath = os.path.join(
                hookspath,
                workingpath )
            if self._needRebuild(workingElementpath, targetElementpath, ignoring):
                self.log.info("An update was detected, image rebuild required")
                return True
        return False

    def _cleanMounts(self, dibenv):
        if '__DIBEnv__' not in self.env.env:
            self.env.env['__DIBEnv__'] = dibenv
        if 'source-repository-overrides' not in dibenv:
            dibenv['source-repository-overrides'] = {}
        if 'shellenv' not in dibenv:
            dibenv['shellenv'] = {}
        self.log.info("Attempting to unmount any mount points still mounted")

        #TODO: Desparately needing to refactor the dib environment
        #      setup code do have a common basis for all steps
        builddir = dibenv['build-dir']
        resultdir = dibenv['result-dir']
        pathrest, leaftobuild = os.path.split(builddir)
        umountPrefix = ['sudo', 'umount', '-f']
        rmdirPrefix = ['sudo', 'rmdir']
        umounts = []
        #NOTE: Ordering is important!
        umounts.append( [ os.path.join(
                           builddir,
                           'build',
                           'tmp',
                           'in_target.d' ) ] )
        umounts.append( [ os.path.join(
                           builddir,
                           'build',
                           'proc' ) ] )
        umounts.append( [ os.path.join(
                           builddir,
                           'mnt',
                           'sys' ) ] )
        umounts.append( [ os.path.join(
                           builddir,
                           'mnt',
                           'dev',
                           'pts' ) ] )
        umounts.append( [ os.path.join(
                           builddir,
                           'mnt',
                           'dev' ) ] )
        umounts.append( [ os.path.join(
                           builddir,
                           'mnt',
                           'proc' ) ] )
        umounts.append( [ os.path.join(
                           builddir,
                           'mnt' ) ] )
        umounts.append( [ os.path.join(
                           builddir,
                           'cache',
                           'ccache' ) ] )

        #TODO: umount ccache

        for umount in umounts:

            result = subprocess.call(
                umountPrefix + umount,
                stdout=self.log.out(),
                stderr=self.log.err() )
            result = subprocess.call(
                rmdirPrefix + umount,
                stdout=self.log.out(),
                stderr=self.log.err() )

        #TODO: Delete lookback device....may need a different phase 
        #      That deletes all lo's and should only run it
        #      if not performing builds.

    def _cleanBuild(self, dibenv):
        self._cleanMounts(dibenv)
        

        self.log.info("Attempting to wipe clean all DIB build results")
        result = subprocess.call(
            ['sudo', 'rm', '-rf', dibenv['build-dir'] ],
            stdout = self.log.out(),
            stderr = self.log.err() )
        if result != 0:
            self.log.warning(
                "Build target '%s' could not be deleted", 
                dibenv['build-dir'] )
        if 'repos' in dibenv:
            env = self._loadStepResultsIntoDict(self.engine.getPhase(), dibenv['repos'])
            self.env.update(env)
        if 'env' in dibenv:
            env = self._loadStepResultsIntoDict(self.engine.getPhase(), dibenv['env'])
            self.env.update(env)

    def _cleanResults(self, dibenv):
        result = subprocess.call(
            ['sudo', 'rm', '-rf', dibenv['result-dir'] ],
            stdout = self.log.out(),
            stderr = self.log.err() )
        if result != 0:
            self.log.warning(
                "Build target '%s' could not be deleted", 
                dibenv['result-dir'] )

    def _copyFilesMarkLeaves(self, element, src, dst):
        if os.path.isfile(src):
            shutil.copy2(src, dst)
            if self.usefattr:
                self._setfattrElement(
                    dst,
                    element )
        elif os.path.isdir(src):
            if not os.path.exists(dst):
                os.mkdir(dst)
            elif not os.path.isdir(dst):
                self.log.error('copyfiles: Destination is a file, directory expected: %s',
                    dst )
                return False
            for item in os.listdir(src):
                if not self._copyFilesMarkLeaves(
                    element,
                    os.path.join(
                        src,
                        item ),
                    os.path.join(
                        dst,
                        item ) ):
                    return False
        else:
            self.log.error('copyfiles: Source path is not a file or dir: %s',
                src )
            return False
        return True

    def _copyElementHooks(self, element, elementpath, hookspath):
        #The correctness of this processing assumes that if an element
        #  is changed, the containing directory of the changed scripts will
        #  be modified.  Primarily, a script was deleted from the element.
        #  That's the big one.  Any change will cause a scrub of the
        #  element's scripts from the hooks for this reason.
        #  Any other change will be picked up by the regular build avoidance
        #  check.
        if len(element.strip()) == 0:
            return False

        for workingpath in os.listdir(elementpath):
            workingElementpath = os.path.join(
                elementpath,
                workingpath)
            targetElementpath = os.path.join(
                hookspath,
                workingpath )
            if not self._copyFilesMarkLeaves(
                element,
                workingElementpath,
                targetElementpath ):
                #An error occurred, stop processing
                return True

        #Tell caller to keep processing (don't stop here)
        return False

    def _appendStartElements(self, dibenv, elements, elementspaths):
        if 'start-elements' in dibenv:
            elements.update(dibenv['start-elements'])
        else:
            self.log.info("No start image elements were found")
        startElementPathExists = True
        if 'start-elementpath' in dibenv:
            for startElementPath in dibenv['start-elementpath']:
                if startElementPath not in elementspaths:
                    if os.path.exists(startElementPath):
                        elementspaths.append(startElementPath)
                    else:
                        startElementPathExists = False
        if not startElementPathExists:
            #TODO: Scan path to ensure start elements exist
            self.log.warning("The start image used an element path that doesn't exist in this environment")
            self.log.warning("The start image elements must be available to the build because of the way DIB works")
            self.log.warning("Ensure that these elements are in the path: %s", str(dibenv['start-elements']))

        return (elements, elementspaths)

    def _calculateElements(self, dibenv):
        #Process the element path appropriately into a colon delimited list
        dibenv['elements-path'] = self.env.doSubstitutions(
            dibenv['elements-path'])
        elementsParts = dibenv['elements-path'].split()
        elementsParts = [ x.strip() for x in elementsParts ]

        #Allow a leading colon to mean from the working directory, 
        #   i.e., ./Elements
        elementsPath = ':'.join(elementsParts)
        if elementsPath[0] == ':':
            elementsPath = "%s/%s%s" % (
                self.env.env['WORKING'],
                'Elements',
                elementsPath )
        dibenv['elements-path'] = elementsPath.split(':')
        elementspaths = dibenv['elements-path']

        # Get the required elements into a list
        dibenv['elements'] = self.env.doSubstitutions(dibenv['elements'])
        elementsParts = dibenv['elements'].split()
        elementsParts = [ x.strip() for x in elementsParts ]
        dibenv['elements'] = ','.join(elementsParts).split(',')
        elements = dibenv['elements']

        #  Get all dependents
        getDeps = 'no-deps' not in self.options \
                      or self.options['no-deps'] == 'False'

        if getDeps:
            elements = self._expandDependencies(elements)
        return (elements, elementspaths)

    def _runThroughElements(self, elements, elementspaths, hooksdir, op):
        #  First check that we can proceed if this build is resumed.
        elementFound = True
        previousElement = '<none>'
        for element in elements:
            if len(element.strip()) == 0:
                continue
            if not elementFound:
                self.log.error(
                    "Required element '%s' not found in provided paths",
                     previousElement )
                self.log.failed()
                raise KeyError(previousElement)
            elementFound = False
            previousElement = element
            for elpathroot in elementspaths:
                #Find the first instance of the element
                elementpath = os.path.join(elpathroot, element)
                if os.path.exists(elementpath):
                    if not os.path.isdir(elementpath):
                        self.log.warning(
                            "Element '%s' had a file instead of a well-formed DIB at path '%s'",
                            element,
                            elementpath )
                        continue
                    else:
                        elementFound = True
                        if op(
                            element,
                            elementpath,
                            hooksdir ):
                            return False
                        break
        return True

    def _prepareStartImage(self, dibenv):
        if 'start-image' not in dibenv:
            return True
        self.log.info("---Preparing to unpack saved image---")
        result = subprocess.call(
            ['sudo', 'tar', '--numeric-owner', '-xzpvf', dibenv['start-image'], 
               '-C', dibenv['build-dir'] ],
            stdout=self.log.out(),
            stderr=self.log.err())
        return result == 0

    def _setupLogConfig(self, dibenv):
        dibenv['logconfig'] = configparser.RawConfigParser()
        dibenv['logconfig'].optionxform = str
        startImage = 'start-logfilename' in dibenv
        if startImage:
            self.log.devdebug("Reading %s config", os.path.join(
                dibenv['build-dir'],
                dibenv['start-logfilename'] ) )
            dibenv['logconfig'].read([
                os.path.join(
                    dibenv['build-dir'],
                    dibenv['start-logfilename'] ) ])
            self.log.devdebug("Sections loaded: %s", str(dibenv['logconfig'].sections()))
            if dibenv['logconfig'].has_section('environment'):
                if dibenv['logconfig'].has_option('environment', 'pickle'):
                    try:
                        dibenv['startenv'] = pickle.loads(
                            base64.b64decode(
                                dibenv['logconfig'].get(
                                    'environment',
                                    'pickle' ) ) )
                        if 'IMAGE_ELEMENT' in dibenv['startenv']:
                            dibenv['start-elements'] = dibenv['startenv']['IMAGE_ELEMENT'].split(' ')
                        if 'ELEMENT_PATH' in dibenv['startenv']:
                            dibenv['start-elementpath'] = dibenv['startenv']['ELEMENT_PATH'].split(':')
                        if 'ARCH' in dibenv['startenv']:
                            dibenv['arch'] = dibenv['startenv']['ARCH']
                        if 'DIB_RELEASE' in dibenv['startenv']:
                            dibenv['release'] = dibenv['startenv']['DIB_RELEASE']
                        if 'DIB_ROOT_LABEL' in dibenv['startenv']:
                            dibenv['root-label'] = dibenv['startenv']['DIB_ROOT_LABEL']
                    except:
                        self.log.exception(
                            "Couldn't load the start image environment")
            
        self.log.devdebug("logconfig reading current build log %s", dibenv['logfile'])
        dibenv['logconfig'].read([dibenv['logfile']])
        self.log.devdebug("logconfig Read sections are: %s" % str(dibenv['logconfig'].sections()))

        if 'startenv' in dibenv:
            dibenv['shellenv'] = dibenv['startenv'].copy()
        else:
            dibenv['shellenv'] = os.environ.copy()
        if dibenv['logconfig'].has_section('environment'):
            if dibenv['logconfig'].has_option('environment', 'pickle'):
                try:
                    dibenv['shellenv'] = pickle.loads(base64.b64decode(
                        dibenv['logconfig'].get(
                            'environment',
                            'pickle' ) ) )
                except:
                    self.log.exception(
                        "Couldn't load the previous build shell environment")
        else:
            dibenv['logconfig'].add_section('environment')

    def _setupBuildAvoidanceIgnoreDictionary(self, options):
        self.buildAvoidanceIgnore = {}
        if 'build-avoidance-ignore' in options:
            lines = options['build-avoidance-ignore'].split('\n')
            count = 0
            self.log.devdebug("ba-ignore: %d lines", len(lines))
            for line in lines:
                count=count+1
                line = line.strip()
                if len(line) == 0:
                    continue
                parts = line.split(':')
                if len(parts) != 2:
                    self.log.warning("build-avoidance-ignore line %d: malformed entry: %s", count, line)
                    continue
                element = parts[0].strip()
                if element not in self.buildAvoidanceIgnore:
                    self.buildAvoidanceIgnore[element] = []
                filename = self.env.doSubstitutions(parts[1].strip())
                self.buildAvoidanceIgnore[element].append(filename)
                self.log.info("build avoidance for '%s' ignoring: %s",
                    element,
                    filename )

    def _setupDibEnv(self, options):
        self._setupBuildAvoidanceIgnoreDictionary(options)
        dibenv = options.copy()
        self.dibenv = dibenv
        self.env.env['__DIBEnv__'] = dibenv
        self.dibenv['source-repository-overrides'] = {}
        if 'root-label' in options:
            dibenv['root-label'] = options['root-label'].strip()
        if 'arch' in options:
            dibenv['arch'] = options['arch'].strip()
        if 'release' in options:
            dibenv['release'] = options['release'].strip() 

        if 'start-dir' in dibenv:
            dibenv['start-dir'] = self.env.doSubstitutions(dibenv['start-dir'])
            startImageName = self.env.doSubstitutions(dibenv['start-image'])
            dibenv['start-logfilename'] = '%s.progresslog' % startImageName 
                
            dibenv['start-image'] = os.path.join(
                dibenv['start-dir'],
                startImageName )
            if not os.path.exists(dibenv['start-image']):
                self.log.error("Did not find image %s", dibenv['start-image'])
                self.log.failed()
                return None

        dibenv['result-dir'] = self.env.doSubstitutions(dibenv['result-dir'])
        imageResultName = self.env.doSubstitutions(dibenv['result-image'])
        dibenv['result-image-name'] = imageResultName
        dibenv['result-image'] = os.path.join(
            dibenv['result-dir'],
            imageResultName )

        if 'build-dir' in dibenv:
            dibenv['build-dir'] = self.env.doSubstitutions(dibenv['build-dir'])
        else:
            dibenv['build-dir'] = dibenv['result-dir'] + '.build-%s' % resultImageName

        dibenv['logfile'] = os.path.join(
            dibenv['build-dir'], 
            "%s.progresslog" % imageResultName )

        dibenv['cache-path'] = os.path.join(
            dibenv['build-dir'],
            'cache' )

        #Load the log file
        self._setupLogConfig(dibenv)

        #NOTE: We're not going to reload the current execution environment
        #      here.  You have to do a clean if you want to change
        #      from what was the initial execution environment.
        #      Honestly, the build shouldn't actually be dependent
        #      on the correctness or consistency of the shell environment.
            
        #Create (or check) the image location, preparing for initial install
        mountdir = dibenv['build-dir']
        imagedir = 'image'
        dibenv['imagedir'] = os.path.join(mountdir, imagedir)
        builddir = 'build'
        #TODO: Yeah, super sorry about this - the naming needs to be fixed
        #      no obvious resolution comes to mind...
        dibenv['buildworkingdir'] = os.path.join(mountdir, builddir)
        dibenv['hooksdir'] = os.path.join(mountdir, 'hooks')
        return dibenv

    def _createFreshImage(self, dibenv):
        if not os.path.exists(dibenv['build-dir']):
            os.makedirs(dibenv['build-dir'])
        if not self._prepareStartImage(dibenv):
            self.log.error(
                "Could not load start image from '%s'", 
                dibenv['start-image'])
            self.log.failed()
            return None
        if not os.path.exists(dibenv['buildworkingdir']):
            os.mkdir(dibenv['buildworkingdir'])
        if not os.path.exists(dibenv['hooksdir']):
            os.mkdir(dibenv['hooksdir'])
        if not os.path.exists(dibenv['result-dir']):
            os.makedirs(dibenv['result-dir'])
        if not os.path.exists(dibenv['cache-path']):
            os.makedirs(dibenv['cache-path'])
        self._setupLogConfig(dibenv)

    def _loadStepResultsIntoDict(self, phase, reposOption):
        newenv = {}
        repos = []
        repolines = self.env.doSubstitutions(reposOption).split()
        for repoline in repolines:
            repos.extend(repoline.split(','))
        for repo in repos:
            repoTarget = repo.strip()
            if len(repoTarget) > 0:
                result = self.engine.launchStep(repoTarget, phase)
                if result is None or not result._didPass():
                    self.log.failed()
                    message = 'Execution of "%s" failed in DIBInit' % repo
                    self.log.error(message)
                    raise RuntimeError(message)
                returnValue = result._getReturnValue(phase)
                self.log.devdebug("loadStep: Dict adding: %s" % returnValue)
                if returnValue is None:
                    self.log.info("A value was not returned - skipping")
                    continue
 
                newenv.update(returnValue)
        return newenv

    def build(self, options):
        self.options = options
        self._registerOnExitCallback("_onExit")
        dibenv = self._setupDibEnv(options)
        if dibenv is None:
            self.log.failed()
            return None

        #TODO: This needs to be more controlled 
        #       - e.g., a specified initial environment.
        #       Builds should not be dependent on the environment
        #       from which they are executed, generally.
        #       PATH, etc is helpful, but still, this should
        #       be intentionall limited - a docker container
        #       would help with this.
        localMods = {}
        if 'repos' in dibenv:
            env = self._loadStepResultsIntoDict('build', dibenv['repos'])
            localMods.update(env)
        if 'env' in dibenv:
            env = self._loadStepResultsIntoDict('build', dibenv['env'])
            localMods.update(env)
            
        imagepath = dibenv['imagedir']
        buildpath = dibenv['buildworkingdir']

        allelements, elementspaths = self._calculateElements(dibenv)
        dibenv['elements'] =allelements
        hooksdir = dibenv['hooksdir']


        # Check the elements for changes or existance
        if not self._runThroughElements(
            allelements,
            elementspaths,
            hooksdir,
            self._isElementDirty ):
            self._cleanBuild(dibenv)
            self._cleanResults(dibenv)
            self._createFreshImage(dibenv)
            if not self._runThroughElements(
                allelements,
                elementspaths, 
                hooksdir,
                self._copyElementHooks ):
                self.log.error('Failed to copy element hooks to image')
                self.log.failed()
                return None

        allelements, elementspaths = self._appendStartElements(dibenv, allelements, elementspaths)
        #Insert calculated, required environment variables
        localMods['TARGET_ROOT'] = buildpath

        #TODO: Doublecheck that this is the right answer for TMP_MOUNT_PATH
        localMods['TMP_MOUNT_PATH'] = buildpath
        localMods['DIB_IMAGE_CACHE'] = dibenv['cache-path']
        if localMods['DIB_IMAGE_CACHE'][0] != '/':
            localMods['DIB_IMAGE_CACHE'] = os.path.join(
                os.getcwd(),
                localMods['DIB_IMAGE_CACHE'] )
        localMods['ARCH'] = dibenv['arch']
        localMods['TMP_HOOKS_PATH'] = hooksdir
        localMods['DIB_HOOKS'] = hooksdir
        localMods['DIB_RELEASE'] = dibenv['release']
        localMods['IMAGE_ELEMENT'] = ' '.join(allelements)
        localMods['ELEMENT_PATH'] = ':'.join(dibenv['elements-path'])
        localMods['DIB_ROOT_LABEL'] = dibenv['root-label']
        localMods['BUILD_DIB_DIR'] = dibenv['build-dir']
        localMods['RESULT_DIB_DIR'] = dibenv['result-dir']
        localMods['_LIB'] = os.path.join(
            self.env.env['RESULTS'],
            'diskimage-builder',
            'lib' )
        dibenv['shellenv'].update(localMods)
        self.env.update(localMods)

        self.log.passed()
        return dibenv

    def _setupCleanupDibEnv(self, options):
        dibenv = {}
        for option, value in options.items():
            dibenv[option] = value
        dibenv['TARGET_ROOT'] = os.path.join(dibenv['build-dir'], 'build')
        self.env.env['TARGET_ROOT'] = dibenv['TARGET_ROOT']
        dibenv['DIB_HOOKS'] = os.path.join(dibenv['build-dir'], 'hooks')
        dibenv['TMP_MOUNT_PATH'] = os.path.join(dibenv['build-dir'], 'build')
        self.env.env['TMP_MOUNT_PATH'] = dibenv['TMP_MOUNT_PATH']
        self.env.env['DIB_HOOKS'] = dibenv['DIB_HOOKS']
        builddir = dibenv['build-dir']
        resultdir = dibenv['result-dir']
        self.env.env['BUILD_DIB_DIR'] = builddir
        self.env.env['RESULT_DIB_DIR'] = resultdir
        return dibenv

    def _onExit(self):
        dibenv = self._setupCleanupDibEnv(self.options)
        self._cleanMounts(dibenv)
        
    def clean(self, options):
        #TODO: Clean up log directory and mount point
        dibenv = self._setupCleanupDibEnv(options)
        self._cleanBuild(dibenv)
        self._cleanResults(dibenv)
        self.log.passed()
        try:
            self._unregisterOnExitCallback("_onExit")
        except:
            pass
        return dibenv

    def clean_results(self, options):
        return self.clean(options)

    def clean_build(self, options):
        dibenv = self._setupCleanupDibEnv(options)
        self._cleanBuild(dibenv)
        self.log.passed()
        return dibenv

    def test(self, options):
        raise Exception

    def default(self, options):
        #TODO: REFACTOR THE DIB ENV SETUP!
        self._setupCleanupDibEnv(options)
        self.log.passed()
