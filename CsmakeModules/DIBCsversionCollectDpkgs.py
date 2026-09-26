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
import subprocess
import re

class DIBCsversionCollectDpkgs(CsmakeModule):
    """Purpose: Record the version of all the dpkg installations on the
                created image.
    Options: tag - Provides a context name for the pull
                   for example - cs-mgmt-base-image or cs-mgmt-sources
    Joinpoints: build
    Creates Environment:
        __Csversion__ - A dictionary where source versions are stored under
                        'sources'.  'sources' is version info keyed off of
                        the source repo name and a makefile designated tag.
                        The same repo/tag combination will be overwritten
                        if pulled twice (with a warning)
                        Every element in the 'sources' dictionary is 
                        a dictionary of tags contiaing a tuple:
                        (<repo-type>, <value-dict>) 
                        Dpkg, specifically:
                        ('dpkg', {'PACKAGE':<reference>, 'VERSION':hashvalue, 'ARCH':<architecture>}) 
        'ARCH' may be an empty string if not specified by the image's package.
        """

    REQUIRED_OPTIONS = ['tag']
    PACKAGE_RE = re.compile(r'(?P<package>[^\s(:]*)(?P<arch>(:[^\s(]*)?)\s*\((?P<version>[^)]*)\)')

    def build(self, options):
        if '__Csversion__' not in self.env.env:
            self.env.env['__Csversion__'] = {}
        if 'sources' not in self.env.env['__Csversion__']:
            self.env.env['__Csversion__']['sources'] = {}
        versdict = self.env.env['__Csversion__']['sources']
        shellenv = self.env.env['__DIBEnv__']['shellenv']
        mountpath = shellenv['TMP_MOUNT_PATH']
        p = subprocess.Popen(
            ["sudo", "-E", "chroot", mountpath, 
                 "dpkg-query", "--show", 
                 "-f", "${binary:Package} (${Version})\\n"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True )
        out, err = p.communicate()
        dpkgs = out.split('\n')
        for dpkg in dpkgs:
            dpkg = dpkg.strip()
            if len(dpkg) == 0:
                continue
            match = self.PACKAGE_RE.match(dpkg)
            if match is None:
                self.log.debug("Didn't match: %s", dpkg)
                continue
            package = match.group('package')
            version = match.group('version')
            arch = match.group('arch')
            tag = options['tag']
            if len(arch) > 0:
                arch = arch.lstrip(':')
                tag = "%s_%s" % (tag, arch)
            if package not in versdict:
                versdict[package] = {}
            if tag in versdict[package]:
                self.log.warning(
                    "Package: %s, Tag: %s :: Overwriting %s",
                    package,
                    tag,
                    str(versdict[package][tag]) )
            versdict[package][tag] = ('dpkg', {
                'PACKAGE' : package,
                'VERSION' : version,
                'ARCH' : arch } )
            self.log.info(
                "Package: %s, Tag: %s :: Added %s",
                package,
                tag,
                str(versdict[package][tag]) )
        self.log.passed()
        return versdict

