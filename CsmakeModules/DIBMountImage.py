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

class DIBMountImage(CsmakeModule):
    """Purpose: Mount the DIB Image for use/copy, etc
                DIBInit and DIBPrepDisk should have been completed
                successfully before executing this command
                ***WARNING*** if for some reason the build is
                              interrupted before DIBUmountImage
                              can be executed, you'll have to clean
                              up the mount.
                              Cross-cut steps with a umount aspect
                              on failure to avoid this problem.
                              Also, have a cleanup failure command handy
       Phases:
           build 
       Flags:
           none
    """

    def _cleanUpMounts(self, dibenv, loopbackDevice):
        mountpath = os.path.join(
            dibenv['build-dir'],
            'mnt' )
        if mountpath is not None:
            result = subprocess.call(
                ['sudo', 'umount',
                 os.path.join(
                     mountpath,
                     'sys' ) ],
                stdout=self.log.out(),
                stderr=self.log.err() )
            if result != 0:
                self.log.warning("Unmount of sysfs failed")
            result = subprocess.call(
                ['sudo', 'umount', 
                 os.path.join(
                     mountpath,
                     'dev',
                     'pts' )],
                stdout=self.log.out(),
                stderr=self.log.err() )
            if result != 0:
                self.log.warning("Unmount of dev/pts failed")
            result = subprocess.call(
                ['sudo', 'umount',
                 os.path.join(
                     mountpath,
                     'dev' )],
                stdout=self.log.out(),
                stderr=self.log.err() )
            if result != 0:
                self.log.warning("Unmount of dev failed")
            result = subprocess.call(
                ['sudo', 'umount', 
                 os.path.join(
                     mountpath,
                     'proc' )],
                stdout = self.log.out(),
                stderr = self.log.err() )
            if result != 0:
                self.log.warning("Unmount of procfs failed")
            result = subprocess.call(
                ['sudo', 'umount', mountpath],
                stdout=self.log.out(),
                stderr=self.log.err() )
            if result != 0:
                self.log.warning("Unmount of image failed")
        if loopbackDevice is not None:
            result = subprocess.call(
                ['sudo', 'losetup', '-d', loopbackDevice],
                stdout=self.log.out(),
                stderr=self.log.err() )
            if result != 0:
                self.log.warning("Couldn't remove loopback device")
        if 'loopbackDevice' in dibenv:
            del dibenv['loopbackDevice']
        if 'CSMAKE_IMAGE_BLOCK_DISK' in dibenv['shellenv']:
            del dibenv['shellenv']['CSMAKE_IMAGE_BLOCK_DISK']
        if 'IMAGE_BLOCK_DEVICE' in dibenv['shellenv']:
            del dibenv['shellenv']['IMAGE_BLOCK_DEVICE']

    def build(self, options):
        dibenv = self.env.env['__DIBEnv__']
        if 'partitions' not in dibenv:
            self.log.error("Image cannot be mounted until the image is prepped.")
            self.log.failed()
            return None

        #Bring up loopback device
        p = subprocess.Popen(
            ['sudo', 'losetup', '--show', '-f', dibenv['imagedir']],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True )
        pout, perr = p.communicate()
        if p.returncode != 0:
            self.log.error("Creating loopback device for image failed")
            self.log.error(perr)
            self.log.failed()
            return None
        loopbackDevice = pout.strip()
        dibenv['loopbackDevice'] = loopbackDevice
        dibenv['shellenv']['CSMAKE_IMAGE_BLOCK_DISK'] = loopbackDevice
        success = False
        mountpath = None
        try:
            #Mount the root partition
            mounted = False
            mountpath = os.path.join(
                dibenv['build-dir'],
                'mnt' )
            if not os.path.exists(mountpath):
                os.mkdir(mountpath)
            elif not os.path.isdir(mountpath):
                self.log.error('Mountpath "%s" is not a directory', mountpath)
                self.log.failed()
                return None
            #TODO: Check to see if the path is already mounted???

            dibenv['shellenv']['TMP_BUILD_DIR'] = dibenv['build-dir']

            for part, values in dibenv['partitions'].items():
                if 'root' in values:
                    rootdev = loopbackDevice + values['lopart']
                    dibenv['shellenv']['IMAGE_BLOCK_DEVICE'] = rootdev
                    result = subprocess.call(
                        ['sudo', 'mount', rootdev, mountpath],
                        stdout=self.log.out(),
                        stderr=self.log.err() )
                    if result != 0:
                        self.log.error("Mount failed")
                        self.log.failed()
                        return None
                    mounted = True
                    break
            #Mount dev and proc
            procmountpath = os.path.join(
                mountpath,
                'proc' )
            if not os.path.exists(procmountpath):
                result = subprocess.call(
                    ['sudo', 'mkdir', procmountpath ],
                    stdout = self.log.out(),
                    stderr = self.log.err() )
                if result != 0:
                    self.log.error("Create of procfs mount point failed")
                    self.log.failed()
                    return None
            result = subprocess.call(
                ['sudo', 'mount', '-t', 'proc', 'none', procmountpath ],
                stdout = self.log.out(),
                stderr = self.log.err() )
            if result != 0:
                self.log.error("Mount of procfs failed")
                self.log.failed()
                return None
            devmountpath = os.path.join(
                mountpath,
                'dev' )
            if not os.path.exists(devmountpath):
                result = subprocess.call(
                    ['sudo', 'mkdir', devmountpath ],
                    stdout = self.log.out(),
                    stderr = self.log.err() )
                if result != 0:
                    self.log.error("Create of dev mount point failed")
                    self.log.failed()
                    return None
            result = subprocess.call(
                ['sudo', 'mount', '--bind', '/dev', devmountpath ],
                stdout=self.log.out(),
                stderr=self.log.err() )
            if result != 0:
                self.log.error("Mount of dev failed")
                self.log.failed()
                return None
            ptsmountpath = os.path.join(
                mountpath,
                'dev',
                'pts' )
            if not os.path.exists(ptsmountpath):
                result = subprocess.call(
                    ['sudo', 'mkdir', ptsmountpath ],
                    stdout=self.log.out(),
                    stderr=self.log.err() )
                if result != 0:
                    self.log.error("Create of the pts mount point failed")
                    self.log.failed()
                    return None
            result = subprocess.call(
                ['sudo', 'mount', '--bind', '/dev/pts', ptsmountpath],
                stdout=self.log.out(),
                stderr=self.log.err() )
            if result != 0:
                self.log.error("Mount of dev/pts failed")
                self.log.failed()
                return None
            sysmountpath = os.path.join(
                mountpath,
                'sys' )
            if not os.path.exists(sysmountpath):
                result = subprocess.call(
                    ['sudo', 'mkdir', sysmountpath],
                    stdout=self.log.out(),
                    stderr=self.log.err() )
                if result != 0:
                    self.log.error("Create of the sys mount point failed")
                    self.log.failed()
                    return None
            result = subprocess.call(
                ['sudo', 'mount', '-t', 'sysfs', 'none', sysmountpath],
                stdout=self.log.out(),
                stderr=self.log.err() )
            if result != 0:
                self.log.error("Mount of sysfs failed")
                self.log.failed()
                return None
            success = True
        finally:
            if not success:
                self._cleanUpMounts(
                    dibenv,
                    loopbackDevice )
        if success:
            self.log.passed()
            return True
        else:
            self.log.failed()
            return False
        #TODO: Save off environment in the logconfig

    def clean(self, options):
        dibenv = self.env.env['__DIBEnv__']
        loopbackDevice = None
        if 'CSMAKE_IMAGE_BLOCK_DISK' in dibenv['shellenv']:
            loopbackDevice = dibenv['shellenv']['CSMAKE_IMAGE_BLOCK_DISK']
        self._cleanUpMounts(
            dibenv,
            loopbackDevice )
        self.log.passed()
        return True
