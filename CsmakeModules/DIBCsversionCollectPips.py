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
import os.path

class DIBCsversionCollectPips(CsmakeModule):
    """Purpose: Record the version of all the pip installations on the
                created image, both global and all virtual envs.
    Options: tag - Provides a context name for the pull
                   for example - cs-mgmt-base-image or cs-mgmt-sources
                Note: The actual tag used for pip records will be a combination
                      of the tag given and the virtual environment, since
                      several virtual environments may have the same
                      pip package installed.
                   The "non" virtual environment will be called '__global'
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
                        Pip, specifically:
                        ('pip', {'VENV':<virtual environment>, 
                                 'PACKAGE':<pip package>,
                                 'VERSION':<package version>}) 
        """

    REQUIRED_OPTIONS = ['tag']
    PACKAGE_RE = re.compile(r'(?P<package>[^\s(]*)\s*\((?P<version>[^)]*)\)')

    def _appendPip(self, tag, versdict, virtualenv, specs):
        tag = "%s_%s" % (tag, virtualenv)

        for spec in specs:
            preppedspec = spec.strip()
            if len(preppedspec) == 0:
                continue
            m = self.PACKAGE_RE.match(preppedspec)
            if m is None:
                self.log.info('%s was not recognized from pip list', preppedspec)
                continue
            package = m.group('package')
            version = m.group('version')
            if package not in versdict:
                versdict[package] = {}
            if tag in versdict[package]:
                self.log.warning(
                    "Package: %s, Tag: %s :: Overwriting %s",
                    package,
                    tag,
                    str(versdict[package][tag]) )
            versdict[package][tag]=('pip',{'VENV':virtualenv, 'PACKAGE':package,'VERSION':version})
            self.log.info(
                "Package: %s, Tag: %s :: Added %s",
                package,
                tag,
                str(versdict[package][tag]) )

    def build(self, options):
        if '__Csversion__' not in self.env.env:
            self.env.env['__Csversion__'] = {}
        if 'sources' not in self.env.env['__Csversion__']:
            self.env.env['__Csversion__']['sources'] = {}
        versdict = self.env.env['__Csversion__']['sources']
        shellenv = self.env.env['__DIBEnv__']['shellenv']
        mountpath = shellenv['TMP_MOUNT_PATH']
        p = subprocess.Popen(
            'sudo -E chroot %s find . | grep "bin/activate_this"' % mountpath,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True )
        out, err = p.communicate()
        venvs = []
        if p.returncode != 0:
            self.log.info("Search for virtual environments failed")
            self.log.info("   It is possible that there are no virtual environments")
            self.log.debug(err)
        else:
            venvraw = out.split('\n')
            for raw in venvraw:
                preppedpath = raw.strip().strip('.')
                if len(preppedpath) == 0:
                    continue
                venv, _ = os.path.split(os.path.split(preppedpath)[0])
                venvs.append(venv)
        p = subprocess.Popen(
            ['sudo', '-E', 'chroot', mountpath, 'pip', 'list'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True )
        out, err = p.communicate()
        #Iterate through all the freeze results
        specs = out.split('\n')
        self._appendPip(options['tag'], versdict, '__global', specs)

        #Iterate through all the venvs
        delim = "=-=-=-=-=-=-=-=-="
        for venv in venvs:
            p = subprocess.Popen(
                """sudo -E chroot %s /bin/bash -c " \
                   source %s/bin/activate; \
                   echo %s; \
                   pip list; \
                   deactivate;"
                   """ % (
                       mountpath,
                       venv,
                       delim ),
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True )
            out, err = p.communicate()
            if p.returncode != 0:
                self.log.info("Could not get information on %s", venv)
                self.log.debug(err)
                continue
            done = False
            piplines = out.split('\n')
            count = 0
            while not done and count < len(piplines):
                done = piplines[count].strip() == delim
                count = count + 1
            piplines = piplines[count:]
            self._appendPip(options['tag'], versdict, venv, piplines)
        self.log.passed()
        return versdict

