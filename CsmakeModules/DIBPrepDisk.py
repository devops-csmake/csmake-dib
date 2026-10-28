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
import os
import re

class DIBPrepDisk(CsmakeModule):
    """Purpose: To mimic the disk creation steps of DIB
           A DIBInit and any DIBRepo steps should be run prior to executing
           this step.
           NOTE: This ignores the block-device.d section
                 (opinion) The elements shouldn't define the way the
                           disk is setup for the image.
                           This should be declared in the build
                 (/opinion)
       Phases: build, clean
       Flags:
           disk-size   - size of the disk for the image, e.g., 40G
           part_# - size, filesystem, and label 
                     - e.g., part_1=40G, ext4, cloudimg-rootfs
                     NOTE: xfs has a 12 character label limitation
                    Valid units for size are G,M,K (Gibibyes, etc)
                      - no unit implies Bytes
                    - for swap use linux-swap
           options_# - mkfs options for the # partition
       Requires:
           Any filesystems to be installed on the host that are desired
           parted - not on all distros
    """

    def _removeImage(self, dibenv):
        #Clear any cruft out of the way
        self.log.info("Removing any old image files/directories")
        result = subprocess.call(
            ['sudo', 'rm', '-rf', dibenv['imagedir']],
            stdout = self.log.out(),
            stderr = self.log.err() )
        
    def _grockPartitions(self, options):
        partitions = {}
        #TODO: Sanity checking on options
        parts = ['size','fs','label']
        for option in options.keys():
            if option.startswith('part_'):
                number = option.split('part_')[1]
                spec = [ x.strip() for x in options[option].split(',')]
                partitions[option] = dict(zip(parts,spec))
                optionskey = 'options_%s' % number
                if optionskey in options:
                    partitions[option]['options'] = options[optionskey]
                else:
                    partitions[option]['options'] = ''
        self.log.devdebug("  Partitions: %s", str(partitions))
        return partitions

    def _getSizeInBytes(self, sizestr):
        matches = re.match(r"([0-9]*)([GMK]?)", sizestr)
        if matches is None:
            self.log.error("'size' incorrectly specified for partition %d: %s", partcount, part)
            self.log.failed()
            return None
        groups = matches.groups()
        if len(groups) != 2 or len(groups[0]) ==0:
            self.log.error("'size' incorrectly specified for partition %d: %s", partcount, part)
            self.log.failed()
            return None
        sizeUnit = groups[1]
        sizeValue = int(groups[0])
        if sizeUnit == 'G':
            endvalue = sizeValue *1024 *1024 *1024
        elif sizeUnit == 'M':
            endvalue = sizeValue *1024 *1024
        elif sizeUnit == 'K':
            endvalue = sizeValue *1024
        else:
            endvalue = sizeValue
        return endvalue

    def build(self, options):
        dibenv = self.env.env['__DIBEnv__']
        size = options['disk-size'].strip()
        diskSizeInBytes = self._getSizeInBytes(size)
        #TODO: Think about build avoidance 
        #      put in logconfig and stash calculated partitions
        #      need to redo losetup and remap the lopart parts
        #      and the root partition (might be easier to stash that
        #      too
        self._removeImage(dibenv)
        self.log.info("Creating a new image with %s space", size)
        result = subprocess.call(
            ['truncate', '-s', size, dibenv['imagedir']],
            stdout = self.log.out(),
            stderr = self.log.err() )
        if result != 0:
            self.log.error("Creating the image failed")
            self.log.failed()
            return None
        partitions = self._grockPartitions(options)
        if 'DIB_ROOT_LABEL' in dibenv['shellenv']:
            rootlabel = dibenv['shellenv']['DIB_ROOT_LABEL']
        else:
            rootlabel = partitions['1']['label']

        #find the root label - this will be where we drop our payload
        rootpartition = None
        for key, value in partitions.items():
            if value['label'] == rootlabel:
                rootpartition = key
                value['root'] = True
                break
        if rootpartition is None:
            self.log.error("The root partition was not found")
        loopbackDevice = None
        try:
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
            self.log.devdebug("Loopback device created: %s", loopbackDevice)
            
            #Make the partitions
            #result = subprocess.call(
            #    ['sudo', 'parted', '-s', loopbackDevice, '--', 'mklabel', 'gpt'],
            #TODO: Enable both gpt and oldschool msdos
            result = subprocess.call(
                ['sudo', 'parted', '-s', loopbackDevice, '--', 'mklabel', 'msdos'],
                stdout = self.log.out(),
                stderr = self.log.err() )
            if result != 0:
                self.log.error("Creating the partition table failed")
                self.log.failed()
                return None
            startPercent = 0
            keys = sorted(partitions.keys())
            partcount = 0
            for part in keys:
                if startPercent >= 100:
                    self.log.error("The specified partitions are larger than the disk")
                    self.log.failed()
                    return None
                partcount = partcount + 1
                values = partitions[part]
                sizevaluestr = values['size'].strip()
                requestedSize = self._getSizeInBytes(sizevaluestr)
                requestedPercent = int(round(requestedSize*100.0/diskSizeInBytes))
                endPercent = requestedPercent + startPercent
                if endPercent > 100:
                    endPercent = 100
                    self.log.warning("Last partition was truncated")

                extracommands = []
                values['lopart'] = 'p%d' % (partcount,)
                if part == rootpartition:
                    #TODO: Possibly needed for gpt
                    #extracommands.extend(
                    #    ['set', str(partcount), 'legacy_boot', 'on'])
                    extracommands.extend(
                        ['set', str(partcount), 'boot', 'on'])
                        
                elif values['fs'] == 'swap':
                    extracommands.extend(
                        ['set', str(partcount), 'swap', 'on'] )
                    
                #TODO: GPT uses labels instead of partition types
                #result = subprocess.call(
                #    ['sudo', 'parted', '-s', '-a', 'optimal', loopbackDevice,
                #       '--', 
                #       'mkpart', values['label'], values['fs'], '%d%%' % startPercent, '%d%%' % endPercent] + extracommands,
                #    stdout = self.log.out(),
                #    stderr = self.log.err() )
                #For now only supporting "primary"
                result = subprocess.call(
                    ['sudo', 'parted', '-s', '-a', 'optimal', loopbackDevice,
                       '--', 
                       'mkpart', 'primary', values['fs'], '%d%%' % startPercent, '%d%%' % endPercent] + extracommands,
                    stdout = self.log.out(),
                    stderr = self.log.err() )
                if result != 0:
                    self.log.error("Partition '%s' failed", part)
                    self.log.failed()
                    return None
                startPercent = endPercent 
            result = subprocess.call(
                ['sudo', 'partprobe', loopbackDevice],
                stdout=self.log.out(),
                stderr=self.log.err())
            if result != 0:
                self.log.warning("Part probe failed")
            result = subprocess.call(
                ['sudo', 'udevadm', 'settle'],
                stdout=self.log.out(),
                stderr=self.log.err())
            if result != 0:
                self.log.warning("settle failed")
            #Make the filesystems
            for part, values in partitions.items():
                if 'swap' in values['fs']:
                    continue
                result = subprocess.call(
                    ['sudo', 'mkfs', values['options'], 
                       '-t', values['fs'], 
                       '-L', values['label'],
                       loopbackDevice + values['lopart'] ] )

            #Put the partition table in __DIBEnv__
            #XXX: This won't work for multiple disks
            dibenv['partitions'] = partitions

            self.log.passed()

            #Different step:
                #Move the built image
            #Different step:
                #unmount teardown

        finally:
            #Tear down lodevice
            if loopbackDevice is not None:
                result = subprocess.call(
                    [ 'sudo', 'losetup', '-d', loopbackDevice ],
                    stdout=self.log.out(),
                    stderr=self.log.err() )

    def clean(self, options):
        dibenv = self.env.env['__DIBEnv__']
        self._removeImage(dibenv)
        self.log.passed()
        return True
