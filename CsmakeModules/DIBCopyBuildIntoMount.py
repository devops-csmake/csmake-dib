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
import subprocess
import os.path

class DIBCopyBuildIntoMount(CsmakeModule):
    """Purpose: Copy the built image into the mount
                DIBInit, DIBPrepDisk, and DIBMountImage
                should have been completed
                successfully before executing this command
           NOTE: Upon success TMP_MOUNT_PATH and TARGET_ROOT
                 will move to the mount point for the image
       Phases:
           build 
       Flags:
           none
    """

    def build(self, options):
        dibenv = self.env.env['__DIBEnv__']
        #Ensure that /dev/shm is empty on the resulting build
        devshmpath = os.path.join(
            dibenv['buildworkingdir'],
            "dev",
            "shm" )
        if os.path.exists(devshmpath):
            result = subprocess.call(
                ['sudo', 'rm', '-rf', devshmpath],
                stdout=self.log.out(),
                stderr=self.log.err())
            if result != 0:
                self.log.warning("Could not remove /dev/shm from the built image")
        imagemount = os.path.join(dibenv['build-dir'], 'mnt')
        ignores = []
        ignores.append(
            os.path.join(
                imagemount,
                'dev' ) )
        ignores.append(
            os.path.join(
                imagemount,
                'proc' ) )
        ignores.append(
            os.path.join(
                imagemount,
                'sys' ) )
        copyImage = self._needRebuild(
            dibenv['buildworkingdir'],
            imagemount,
            ignores )
        if copyImage:
            result = subprocess.call(
                'sudo cp -t %s -a %s/*' % (
                    imagemount,
                    dibenv['buildworkingdir'] ),
                stdout=self.log.out(),
                stderr=self.log.err(),
                shell=True )
            if result != 0:
                self.log.error("Transfer of the image to the mounted filesystem failed")
                self.log.failed()
                return None
        else:
            self.log.info("Skipping build copy - build avoidance")
        self.log.passed()
        dibenv['shellenv']['TMP_MOUNT_PATH'] = imagemount
        dibenv['shellenv']['TARGET_ROOT'] = imagemount
        self.env.env['TMP_MOUNT_PATH'] = imagemount
        self.env.env['TARGET_ROOT'] = imagemount
        return True
