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
import urllib.parse as urlparse
import base64
import os.path
import os
from datetime import datetime

class DIBCsversionRecordSourceRepository(DIBRunPartsAspect):
    """Purpose: Record the version of the source repos pulled using
                DIBGetSourceRepositories
    Options: tag - Provides a context name for the pull
                   for example - cs-mgmt-base-image or cs-mgmt-sources
             partOverride - specify a source repository to execute on
                            '*' for all repositories - (usually...)
             pulled ** from DIBGetSourceRepositories
    Joinpoints: script_passed
    Creates Environment:
        __Csversion__ - A dictionary where source versions are stored under
                        'sources'.  'sources' is version info keyed off of
                        the source repo name and a makefile designated tag.
                        The same repo/tag combination will be overwritten
                        if pulled twice (with a warning)
                        Every element in the 'sources' dictionary is
                        a dictionary of tags contiaining dictionaries keyed
                        by the repo name with a tuple:
                        (<repo-type>, <value-dict>)
                        Specifically:
                        ('git', {'NAME': <source-repo name>
                                 'BRANCH':<reference>, 'REF':hashvalue,
                                 'INJECTED': <path/to/repo/in/appliance>})
                        ('file', {'NAME': <source-repo name>,
                                  'URL':<url>, 'SHA1':<sha1>,
                                  'BUILD_INDEX' : <encoded url, base64>,
                                  'TIME':<time of pull, iso8601>,
                                  'FILE':<file>,
                                  'INJECTED':<path/to/file/in/appliance>})
                        ('tar', {'NAME': <source-repo name>,
                                 'URL':<url>, 'SHA1':<sha1>,
                                 'BUILD_INDEX' : <encoded url, base64>,
                                 'TIME':<time of pull, iso8601>,
                                 'FILES':(<dir/to/landed>, [<files>])})
    Note: Will use the result returned by GitDependent to record the SHA
          and will use the "ref" parameter to record the requested reference.
        """

    REQUIRED_OPTIONS = ['tag', 'partOverride']

    def _handleGit(self, source, options, versdict):
        name, repotype, local, url, ref = source
        result, code, message = options['pulled']
        parsedurl = urlparse.urlparse(url)
        repo = parsedurl.path
        if repo[0] == '/':
            repo = repo[1:]
        entry = ('git', {
            'NAME': name,
            'BRANCH' : ref,
            'REF' : result[1],
            'URL' : url,
            'INJECTED' : local })
        return (repo, entry)

    def _handleFile(self, source, options, versdict):
        name, repotype, local, url, ref = source
        downloadPath, sha, downloadTime = options['pulled']
        entry = ('file', {
            'NAME':name,
            'PATH':downloadPath,
            'SHA1':sha,
            'URL' :url,
            'BUILD_INDEX' :base64.b64encode(url.encode()).decode('ascii').replace('\n', ''),
            'TIME':downloadTime,
            'INJECTED':local } )
        return (name, entry)

    def _handleTar(self, source, options, versdict):
        name, repotype, local, url, ref = source
        imagepath, tarfiles, sha, downloadTime, downloadPath = options['pulled']
        entry = ('tar', {
            'NAME':name,
            'SHA1':sha,
            'URL' :url,
            'BUILD_INDEX' : base64.b64encode(url.encode()).decode('ascii').replace('\n', ''),
            'TIME':downloadTime,
            'FILES': (local, tarfiles) } )
        return (name, entry)

    def script_passed(self, phase, options, gitsection, gitoptions):
        name = options['source']['name']
        repotype = options['source']['type']
        local = options['source']['local']
        url = options['source']['URL']
        ref = options['source']['ref']
        source = (name, repotype, local, url, ref)
        if '__Csversion__' not in self.env.env:
            self.env.env['__Csversion__'] = {}
        if 'sources' not in self.env.env['__Csversion__']:
            self.env.env['__Csversion__']['sources'] = {}
        versdict = self.env.env['__Csversion__']['sources']
        key = None
        entry = None
        if 'git' == repotype:
            key, entry = self._handleGit(source, options, versdict)
        elif 'file' == repotype:
            key, entry = self._handleFile(source, options, versdict)
        elif 'tar' == repotype:
            key, entry = self._handleTar(source, options, versdict)
        else:
            self.log.error("Unknown repo type: %s", repotype)
            self.log.failed()
            return False
        if key not in versdict:
            versdict[key] = {}
        else:
            if options['tag'] in versdict[key]:
                self.log.warning("Repo: %s, Tag: %s :: Overwriting %s",
                    key,
                    options['tag'],
                    str(versdict[key][options['tag']] ))
        versdict[key][options['tag']] = entry
        self.log.info("Repo: %s, Tag: %s :: Added %s",
            key,
            options['tag'],
            str(versdict[key][options['tag']] ))
        self.log.passed()
        return True
