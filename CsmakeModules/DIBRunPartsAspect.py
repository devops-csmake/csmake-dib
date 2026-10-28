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
from CsmakeCore.CsmakeAspect import CsmakeAspect

class DIBRunPartsAspect(CsmakeAspect):
    """Purpose: To modify the run-dib-parts processing with a workaround.
                These aspects should only modify DIBRunParts steps
       Phases: *
       Joinpoints Added: script_skip, script_start, script_passed
                         script_failed, script_exception, script_end
       Flags:
           partOverride - the part(s) proposed for overriding, 
                          e.g. 01-install-selinx"""

    def _installPartHandler(self, options, stepoptions):
        self.override = options['partOverride']
        partName = "__%s" % self.override.strip()
        if partName not in stepoptions:
            stepoptions[partName] = []
        stepoptions[partName].append( (self,options) )

    def start(self, phase, options, step, stepoptions):
        self.options = options
        self._installPartHandler(options, stepoptions)
        self.log.passed()
    
