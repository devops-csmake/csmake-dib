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

class DIBPackagePhasedImage(CsmakeModule):
    """Purpose: To package a partial DIB image
           A DIBInit and any DIBRepo steps should be run prior to executing
           this step.
       Phases: build, clean, archive
       Flags:
    """

    def build(self, options):
        dibenv = self.env.env['__DIBEnv__']

        result = subprocess.call(
            ['sudo', 'tar', '-czvf', dibenv['result-image'], '-C', dibenv['build-dir'], '.' ],
            stdout=self.log.out(),
            stderr=self.log.err())

        if result == 0:
            self.log.passed()
            return True
        else:
            self.log.failed()
            return False

    def clean(self, options):
        dibenv = self.env.env['__DIBEnv__']
        result = subprocess.call(
            ['sudo', 'rm', dibenv['result-image']],
            stdout=self.log.out(),
            stderr=self.log.err())
        self.log.passed()
        return True

    def archive(self, options):
        self.log.warning("archive:  NOT IMPLEMENTED YET")
        self.log.passed()
        return True
