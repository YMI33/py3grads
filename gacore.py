"""
Python 3 interface to GrADS, inspired by the work of Arlindo da Silva on PyGrADS.
A GrADS object is used to pass commands to a GrADS instance and parse the output.

Basic Usage:
    from py3grads import gacore
    ga = gacore.Grads()
    # Example command
    output, rc = ga('query config')

Version: 1.0

Compatible with: GrADS 2.1.a3

Author: Levi Cowan <levicowan@tropicaltidbits.com>
"""

__all__ = ['GrADSError', 'PygradsError', 'Grads', 'GaEnv']

import numpy as np
from datetime import datetime
from subprocess import Popen, PIPE, STDOUT
from io import BytesIO

###############################################
#              Custom Exceptions              #
###############################################

class GrADSError(Exception):
    pass

class PygradsError(Exception):
    pass

###############################################
#               GrADS Interface               #
###############################################
class Grads:
    def __init__(self, launch='grads -bul', verbose=True):
        """
        The primary interface to GrADS. User commands can be passed in as input
        to be executed after the object is initialized.

        Args:
            launch:  The system command to launch GrADS. The flags '-b', '-u', and
                     either '-l' or '-p' are required in order to collect GrADS output
                     without shell interactivity. Other flags may be specified.
                     Default flags are '-bul'.

            verbose: If True, will print all output.
        """
        self.verbose = verbose
        # Launch the GrADS process
        args = launch.split()
        self.p = Popen(args, bufsize=0, stdin=PIPE, stdout=PIPE, stderr=STDOUT,
                       universal_newlines=False)
        # Dismiss initial launch output
        self._parse_output()

    def __call__(self, gacmd):
        """
        Allow commands to be passed to the GrADS object
        """
        outlines, rc = self.cmd(gacmd)
        if rc > 0:
            print('\n'.join(outlines))
            raise GrADSError('GrADS returned rc='+str(rc)
                             +' for the following command:\n'+gacmd)
        return outlines, rc

    def __del__(self):
        """
        Call the GrADS quit command and close pipes. An error here is not fatal.
        """
        try:
            self.cmd('quit')
            self.p.stdin.close()
            self.p.stdout.close()
        except:
            pass

    def _parse_output(self, marker='IPC', verbose=True, encoding='utf-8'):
        """
        Collect and return GrADS output from stdout.

        Args:
            marker:   The tag name bounding relevant output.
            verbose:  If True, each line of output is printed to stdout.
            encoding: Expected character encoding of the GrADS output
        Returns:
            lines: List containing all lines of output
            rc:    The return code (int)
        """
        markstart = '<'+marker+'>'
        markend = '</'+marker+'>'
        lines = []
        out = ''
        rc = -1
        # Output is contained within stream marker tags
        # First get to the next markstart tag
        while markstart not in out:
            out = self.p.stdout.readline().decode(encoding)
            if len(out) == 0:
                raise GrADSError("GrADS terminated.")
        # Collect output between marker tags
        out = self.p.stdout.readline().decode(encoding)
        while markend not in out:
            if len(out) > 0:
                # Get return code
                if '<RC>' in out:
                    rc = int(out.split()[1])
                # Collect all other lines
                else:
                    # Get rid of newline at the end
                    lines.append(out[:-1])
                    if verbose:
                        print(lines[-1])
            else:
                raise GrADSError("GrADS terminated.")
            out = self.p.stdout.readline().decode(encoding)

        return lines, rc

    def move_pointer(self, marker, encoding='utf-8', verbose=False):
        """
        Move the GrADS stream pointer to the given marker.
        The marker only has to match a portion of a line of output.

        Additional Args:
            encoding: Expected character encoding of the GrADS output
        """
        out = ''
        while marker not in out:
            out = self.p.stdout.readline().decode(encoding)
            if verbose:
                print(out)
            if len(out) == 0:
                raise GrADSError("GrADS terminated.")
        return

    def cmd(self, gacmd, verbose=True, block=True, encoding='utf-8'):
        """
        Run a GrADS command.

        Args:
            gacmd:    The command string to be executed.
            verbose:  If False, suppress output to stdout
            block:    If True, block and collect all output.
            encoding: Expected character encoding of the GrADS output
        Returns:
            outlines: List of output lines from GrADS.
            rc:       GrADS return code (int)
        """
        if gacmd[-1] != '\n':
            gacmd += '\n'
        # Input to GrADS is always UTF-8 bytes
        self.p.stdin.write(gacmd.encode('utf-8'))
        self.p.stdin.flush()
        if block:
            # Let global verbose=False override if local verbose is True
            if verbose:
                outlines, rc = self._parse_output(encoding=encoding, verbose=self.verbose)
            else:
                outlines, rc = self._parse_output(encoding=encoding, verbose=False)
            output = '\n'.join(outlines)
            if 'Syntax Error' in output:
                raise GrADSError('Syntax Error while evaluating '+gacmd)
        else:
            outlines = []
            rc = 0

        return outlines, rc

    def flush(self):
        """
        Flush the GrADS output pipe. This may be necessary when
        the output stream ends but the stream pointer is decoupled
        from its marker. At this point the output pipe hangs.
        If it is known in advance that this will happen, calling
        flush() will reset the pointer by running an ubiquitous command.
        """
        self.cmd('q config', verbose=False)

    def env(self, query='all'):
        """
        Query and return the GrADS dimension and display environment.
        This function is designed to make a new query every time it is
        called in order to avoid problems when assuming the last known
        state has not changed. A snapshot of the environment at a specific
        time can be saved by assigning a variable to a call of this function.
        """
        return GaEnv(self, query)

    def exp(self, expr):
        """
        Export a GrADS field to a Numpy array.
        Currently only 2D xy grids are supported.

        Args:
            expr: The GrADS expression representing the field to be exported.
        """
        # Get the current environment
        env = self.env()
        if (env.xfixed or env.yfixed) or (env.rank != 2):
            raise PygradsError('Unsupported environment for export of expression: '+expr)
        # Enable GrADS binary output to stream
        self.cmd('set gxout fwrite', verbose=False)
        self.cmd('set fwrite -st -', verbose=False)
        # Don't block output here so we can intercept the data stream
        self.cmd('display '+expr, verbose=False, block=False)
        # Move stream pointer to '<FWRITE>'
        self.move_pointer('<FWRITE>', encoding='latin-1', verbose=False)
        # Read binary data from stream
        handle = BytesIO()
        while True:
            # Read data in 512 byte chunks
            chsize = 4096
            chunk = self.p.stdout.read(chsize)
            # We know we're at the end when we encounter an RC tag
            # preceded by a newline. The newline is important because
            # '<RC>' by itself can appear in a binary data stream.
            if b'\n<RC>' in chunk:
                # Cut out whatever data precedes the <RC> tag
                handle.write(chunk.split(b'\n<RC>')[0])
                # The ending character of the last chunk is arbitrary,
                # we only know that <RC> is in it.
                # Thus, need to flush GrADS pipes to avoid hanging
                # and reset the pointer to the next marker.
                self.flush()
                break
            else:
                handle.write(chunk)
        # For whatever reason, GrADS will sometimes return a grid offset
        # by an index or two from what the dimension environment says it
        # should be (nx*ny). To work around this, test a few perturbations
        # around the expected size and see if any of them work. Record
        # tuples of ((nx,ny),size)
        possible_sizes = []
        for dim in ('x','y'):
            for di in range(-2,3):
                if dim == 'x':
                    nx = env.nx + di; ny = env.ny
                elif dim == 'y':
                    nx = env.nx; ny = env.ny + di
                possible_sizes.append( ((nx,ny),nx*ny) )
        dims, sizes = zip(*possible_sizes)
        try:
            # Convert binary data to 32-bit floats
            arr = np.fromstring(handle.getvalue(), dtype=np.float32)
            assert arr.size in sizes
        except:
            raise PygradsError('Problems occurred while exporting GrADS expression: '+expr)
        else:
            # Actual shape of the grid
            nx, ny = dims[sizes.index(arr.size)]
        # Close stream
        self.cmd('disable fwrite', verbose=False)
        # Restore gxout settings, assuming typical 2D scalar field plot
        self.cmd('set gxout '+env.gx2Dscalar, verbose=False)
        # Return the Numpy array
        arr = arr.reshape((ny, nx))
        return arr

###############################################
#           GrADS Environment Handle          #
###############################################
class GaEnv:
    def __init__(self, ga, query='all'):
        """
        Container for holding GrADS dimension and display environment data.
        The information is derived from GrADS query commands ['dims','gxout'].
        A specific query may be requested if only one is needed. Default
        is to load all supported queries.
        """
        # Query dims
        if query in ('dims', 'all'):
            qdims, rc = ga.cmd('query dims', verbose=ga.verbose)
            if rc > 0:
                raise GrADSError('Error running "query dims"')

            # Current open file ID
            self.fid = int(qdims[0].split()[-1])
            # Which dimensions are varying or fixed?
            self.xfixed = 'fixed' in qdims[1]
            self.yfixed = 'fixed' in qdims[2]
            self.zfixed = 'fixed' in qdims[3]
            self.tfixed = 'fixed' in qdims[4]
            self.efixed = 'fixed' in qdims[5]

            # Get the dimension values. These are single numbers if the dimension
            # is fixed, or a tuple of (dim1, dim2) if the dimension is varying.
            # Grid coordinates x,y,z,t,e can be non-integers for varying dimensions,
            # but it is useful to have the "proper" integer grid coordinates xi,yi,zi,ti,ei.
            # If a dimension is fixed, GrADS automatically rounds non-integer dimensions
            # to the nearest integer.
            xinfo = qdims[1].split()
            if self.xfixed:
                self.lon = float(xinfo[5])
                self.x = float(xinfo[8])
                self.xi = int(np.round(self.x))
            else:
                self.lon = (float(xinfo[5]), float(xinfo[7]))
                self.x = (float(xinfo[10]), float(xinfo[12]))
                self.xi = (int(np.floor(self.x[0])), int(np.ceil(self.x[1])))
            yinfo = qdims[2].split()
            if self.yfixed:
                self.lat = float(yinfo[5])
                self.y = float(yinfo[8])
                self.yi = int(np.round(self.y))
            else:
                self.lat = (float(yinfo[5]), float(yinfo[7]))
                self.y = (float(yinfo[10]), float(yinfo[12]))
                self.yi = (int(np.floor(self.y[0])), int(np.ceil(self.y[1])))
            zinfo = qdims[3].split()
            if self.zfixed:
                self.lev = float(zinfo[5])
                self.z = float(zinfo[8])
                self.zi = int(np.round(self.z))
            else:
                self.lev = (float(zinfo[5]), float(zinfo[7]))
                self.z = (float(zinfo[10]), float(zinfo[12]))
                self.zi = (int(np.floor(self.z[0])), int(np.ceil(self.z[1])))
            tinfo = qdims[4].split()
            if self.tfixed:
                self.time = datetime.strptime(tinfo[5], '%HZ%d%b%Y')
                self.t = float(tinfo[8])
                self.ti = int(np.round(self.t))
            else:
                self.time = (datetime.strptime(tinfo[5], '%HZ%d%b%Y'),
                             datetime.strptime(tinfo[7], '%HZ%d%b%Y'))
                self.t = (float(tinfo[10]), float(tinfo[12]))
                self.ti = (int(np.floor(self.t[0])), int(np.ceil(self.t[1])))
            einfo = qdims[5].split()
            if self.efixed:
                self.e = float(einfo[8])
                self.ei = int(np.round(self.e))
            else:
                self.e = (float(einfo[10]), float(einfo[12]))
                self.ei = (int(np.floor(self.e[0])), int(np.ceil(self.e[1])))

            # Dimension lengths in the current environment.
            # Different from total dimension length in the file (see ctlinfo)
            if self.xfixed:
                self.nx = 1
            else:
                self.nx = self.xi[1] - self.xi[0] + 1
            if self.yfixed:
                self.ny = 1
            else:
                self.ny = self.yi[1] - self.yi[0] + 1
            if self.zfixed:
                self.nz = 1
            else:
                self.nz = self.zi[1] - self.zi[0] + 1
            if self.tfixed:
                self.nt = 1
            else:
                self.nt = self.ti[1] - self.ti[0] + 1
            if self.efixed:
                self.ne = 1
            else:
                self.ne = self.ei[1] - self.ei[0] + 1

            # Rank of the data field (number of dimensions)
            self.rank = sum([not d for d in
                             [self.xfixed,self.yfixed,self.zfixed,self.tfixed,self.efixed]])

        # Query ctlinfo
        if query in ('ctlinfo', 'all'):
            qctl, rc = ga.cmd('query ctlinfo', verbose=ga.verbose)
            if rc > 0:
                raise GrADSError('Error running "query ctlinfo"')
            # Total dimension lengths in the file
            self.Ne = 1
            for line in qctl:
                if 'xdef ' in line or 'XDEF ' in line:
                    self.Nx = int(line.split()[1])
                elif 'ydef ' in line or 'YDEF ' in line:
                    self.Ny = int(line.split()[1])
                elif 'zdef ' in line or 'ZDEF ' in line:
                    self.Nz = int(line.split()[1])
                elif 'tdef ' in line or 'TDEF ' in line:
                    self.Nt = int(line.split()[1])
                # EDEF section may or may not be present
                elif 'edef ' in line or 'EDEF ' in line:
                    self.Ne = int(line.split()[1])

        # Query gxout
        if query in ('gxout', 'all'):
            qgxout, rc = ga.cmd('query gxout', verbose=ga.verbose)
            if rc > 0:
                raise GrADSError('Error running "query gxout"')
            # gxout defines graphics types for 1D scalar plots, 1D vector plots,
            # 2D scalar plots, and 2D vector plots.
            # Map GrADS graphic identifiers to gxout commands. Note that "gxout stat"
            # and "gxout print" do not change the output of "query gxout"
            graphicTypes = {'Contour': 'contour', 'Line': 'line', 'Barb': 'barb',
                            '16': 'shaded', '17': 'shade2b', 'Shaded': 'shade1',
                            'Vector': 'vector', 'Shapefile': 'shp', 'Bar': 'bar',
                            'Grid': 'grid', 'Grfill': 'grfill', 'Stream': 'stream',
                            'Errbar': 'errbar', 'GeoTIFF': 'geotiff', 'Fgrid': 'fgrid',
                            'ImageMap': 'imap', 'KML': 'kml', 'Linefill': 'linefill',
                             'Scatter': 'scatter', 'Fwrite': 'fwrite', '0': None}

            # Get current graphics settings
            self.gx1Dscalar = graphicTypes[qgxout[1].split()[-1]]
            self.gx1Dvector = graphicTypes[qgxout[2].split()[-1]]
            self.gx2Dscalar = graphicTypes[qgxout[3].split()[-1]]
            self.gx2Dvector = graphicTypes[qgxout[4].split()[-1]]
            stationData = qgxout[5].split()[-1]
            if stationData == '6':
                self.stationData = None
            else:
                self.stationData = stationData
