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
from CsmakeModules.DIBRunPartsAspect import DIBRunPartsAspect
import shutil
import os.path
import subprocess
 
class DIBTemporaryFileHack(DIBRunPartsAspect):
    """Purpose: Modify a file for a specific DIB step
                If multiple flags are defined the order of operation
                  is replace, replace-file, patch, patch-file, append,
                    then append-file
                If the path to the specified file does not exist
                  it will be created then deleted unless 'keepChanges'
                  is True
                The path to temp-file will not be created if it is different
                  and does not exist
       Phases: build
       Flags:
           partOverride - the script proposed for skipping, 
                          e.g. 01-install-selinx
           file - File to modify
           line-pad - character or string that will start each line
                    (Should be unique - not used with files)
           temp-file - location to store the old file to restore it
              Default: <file dir>/<file name>.csmake-DIBTemporaryFileHack-save
           keepChanges - The old file will not be restored if True
                     The step actions will be skipped if the specified
                     temporary file exists.
           patch - Inline patch diff
           patch-file - File containing the patch diff to apply (not impl)
           replace - Inline replacement of the file
           replace-file - File containing the new version
           append - Inline append of the file 
           append-file - File containing the new lines to append
    """

    def _filterLinePad(self, options, intext):
        if 'line-pad' in options:
            replstr = '\n%s' % options['line-pad'].strip()
            return intext.replace(replstr,'\n')
        else:
            return intext

    def _replace(self, options):
        replaceText = self._filterLinePad(options, options['replace'])
        with open(self.targetFile, 'w') as openTarget:
            openTarget.write(replaceText)

    def _replaceFile(self, options):
        shutil.copy(
            self.env.doSubstitutions(options['replace-file']),
            self.targetFile )

    def _applyPatch(self, options):
        patchText = self._filterLinePad(options, options['patch'])
        self.log.debug("Applying patch: %s", patchText)
        process = subprocess.Popen(
            ['patch', self.targetFile],
            stdout=self.log.out(),
            stderr=self.log.err(),
            stdin=subprocess.PIPE,
            universal_newlines=True )
        process.stdin.write(patchText)
        process.stdin.close()
        result = process.wait()
        if result != 0:
            self.log.error("File was not patched")
            self.log.failed()
            return False

    def _applyPatchFile(self, options):
        pass

    def _append(self, options):
        appendText = self._filterLinePad(options, options['append'])
        with open(self.targetFile, 'a') as openTarget:
            openTarget.write(appendText)

    def _appendFile(self, options):
        sourceFile = self.env.doSubstitutions(options['append-file'])
        with open(self.targetFile, 'a') as openTarget:
            with open(sourceFile) as source:
                openTarget.writelines(source.readlines())

    def _setFileMode(self, path, mode):
        result = subprocess.call(
            ['sudo', 'chmod', mode, path],
            stdout = self.log.out(),
            stderr = self.log.err() )
        if result != 0:
            self.log.error("Could not set mode (%s) on '%s'",
                mode,
                path )
            return False
        return True

    def _setPermissiveMode(self, path):
        oldMode = oct(os.stat(self.dirpath).st_mode & 0o777)[2:]
        if not self._setFileMode(path, '777'):
            self.log.error("Could not set permissive move on '%s'",
                path )
            return None
        return oldMode

    def _removeCreatedPath(self):
        if len(self.createdParts) > 0:
            os.removedirs(
                os.path.join(
                    self.dirpath,
                    self.createdParts[0] ) )
            return True
        return False

    def _createPathTo(self, path):
        self.createdParts = []
        currentPath = path
        while currentPath != '' \
            and currentPath !='/' \
            and not os.path.exists(currentPath):
            currentPath, last = os.path.split(currentPath)
            self.createdParts.append(last)
        if currentPath == '':
            currentPath = '.'
        self.dirpath = currentPath
        self.realDirMode = self._setPermissiveMode(self.dirpath)
        self.createdParts.reverse()
        if len(self.createdParts) > 0:
            os.makedirs(
                os.path.join(
                    self.dirpath,
                    *self.createdParts) )
        return self.realDirMode is not None

    def script_start__build(self, phase, options, step, stepoptions):
        self.targetFile = self.env.doSubstitutions(options['file'])
        self.log.info("Modifying file '%s'", self.targetFile)
        self.tempFile = self.targetFile + ".csmake-DIBTemporaryFileHack-save"
        self.dirpath = None
        if 'temp-file' in options:
            self.tempFile = self.env.doSubstitutions(options['temp-file'])
        self.log.info("Saving original file in '%s'", self.tempFile)
        if os.path.exists(self.tempFile):
            self.log.warning("The temporary original file already exists")
            self.log.warning("Not applying this change as it is assumed it is already applied")
            self.log.passed()
            return None

        
        self.dirpath = os.path.dirname(self.targetFile)
        if not self._createPathTo(self.dirpath):
            self.log.error("Create path to directory '%s' failed", self.dirpath)
            self.dirpath = None
            self.log.failed()
            return None
      
        #Save the mode string for the dir to restore it on exit

        if os.path.exists(self.targetFile):
            self.realFileMode = oct(os.stat(self.targetFile).st_mode & 0o777)[2:]
            result = subprocess.call(
                ['sudo', 'chmod', '777', self.targetFile],
                stdout = self.log.out(),
                stderr = self.log.err() )
            if result != 0:
                self.log.warning("Attempting to change the mode of the target file failed")
            shutil.copy2(self.targetFile, self.tempFile)
        else:
            #Go ahead and touch the file
            with open(self.targetFile, 'w') as openTarget:
                openTarget.write('')
        if 'replace' in options:
            self._replace(options)
        if 'replace-file' in options:
            self._replaceFile(options)
        if 'patch' in options:
            self._applyPatch(options)
        if 'patch-file' in options:
            self._applyPatchFile(options)
        if 'append' in options:
            self._append(options)
        if 'append-file' in options:
            self._appendFile(options)
        self.log.passed()
        return True

    def script_end__build(self, phase, options, step, stepoptions):
        if self.dirpath is None:
            self.log.error("Directory for target file does not exist: %s", self.dirpath)
            self.log.failed()
            return False

        if 'keepChanges' in options and options['keepChanges'].strip() == 'True':
            self.log.info("Keeping modified file from aspect")
            self.log.passed()
            return True
        try:
            try:
                os.remove(self.targetFile)
            except OSError as e:
                self.log.warning("SECURITY HAZARD: Removal of hacked file failed: %s", repr(e))
            if os.path.exists(self.tempFile):
                os.rename(self.tempFile, self.targetFile)
                result = subprocess.call(
                    ['sudo', 'chmod', self.realFileMode, self.targetFile],
                    stdout=self.log.out(),
                    stderr=self.log.err() )
                if result != 0:
                    self.log.error('Could not reset mode for the target file - SECURITY')
            else:
                try:
                    self._removeCreatedPath()
                except Exception as e:
                    self.log.warning("SECURITY HAZARD: Could not remove created path", repr(e))

            result = subprocess.call(
                ['sudo', 'chmod', self.realDirMode, self.dirpath],
                stdout=self.log.out(),
                stderr=self.log.err() )
            if result != 0:
                self.log.warning("SECURITY HAZARD: Could not reset mode for the target file directory")
            
            self.log.passed()
        except:
            self.log.exception("SECURITY HAZARD: Failed to restore files")
            self.log.failed()
        return True

        #TODO: Need to add start__clean to wipe out the temp file created.
