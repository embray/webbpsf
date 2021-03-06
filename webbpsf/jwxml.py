﻿
""" jwxml: Various Python classes for parsing JWST-related information in XML files

* SUR: a segment update request file
* Update: a single mirror update inside of a SUR
* SIAF: a SIAF file
* Aperture: a single aperture inside a SIAF file


"""
import numpy as np
import matplotlib.pyplot as plt
try:
    from lxml import etree
except ImportError:
    import xml.etree.cElementTree as etree

import logging
import unittest
import os
_log = logging.getLogger('jwxml')


#---------------------------------------------------------------------------------
class Segment_Update(object):
    """ Class for representing one single mirror update (will be inside of groups in SURs)
    """
    def __init__(self, xmlnode):
        if xmlnode.attrib['type'] != 'pose': raise NotImplemented("Only Pose updates supported yet")

        self.id = int(xmlnode.attrib['id'])
        self.type = xmlnode.attrib['type']
        self.segment = xmlnode.attrib['seg_id'][0:2]
        self.absolute = xmlnode.attrib['absolute'] =='true'
        self.coord= xmlnode.attrib['coord'] #local or global
        self.stage_type= xmlnode.attrib['stage_type']  # recenter_fine, fine_only, none

        self.units = dict()
        self.moves = dict()
        for move in xmlnode.iterchildren():
            #print move.tag, move.text 
            self.moves[move.tag] =float(move.text)
            self.units[move.tag] = move.attrib['units']
            #X_TRANS, Y_TRANS, PISTON, X_TILT, Y_TILT, CLOCK
        #allowable units: 
		#units="id"
		#units="meters"
		#units="none"
		#units="radians"
		#units="sag"
		#units="steps"
		#
        # pose moves will only ever have meters/radians as units
    def __str__(self):
        return ("Update %d, %s, %s: "% (self.id, 'absolute' if self.absolute else 'relative', self.coord)) + str(self.moves)
    def shortstr(self):
        outstr = ("Update %d: %s, %s, %s {"% (self.id, self.segment, 'absolute' if self.absolute else 'relative', self.coord))

        outstr+= ", ".join([ coordname+"=%.3g" % self.moves[coordname] for coordname in ['PISTON','X_TRANS','Y_TRANS','CLOCK', 'X_TILT','Y_TILT']])
        #for coordname in ['PISTON','X_TRANS','Y_TRANS','CLOCK', 'X_TILT','Y_TILT']:
            #outstr+=coordname+"=%.3g" % self.moves[coordname]
        outstr+="}"
        return outstr

    @property
    def xmltext(self):
        """ The XML text representation of a given move """
        text= '        <UPDATE id="{0.id}" type="{0.type}" seg_id="{0.segment}" absolute="{absolute}" coord="{0.coord}" stage_type="{0.stage_type}">\n'.format( self, absolute = str(self.absolute).lower())
        for key in ['X_TRANS','Y_TRANS','PISTON','X_TILT', 'Y_TILT', 'CLOCK']:
            if key in self.moves.keys():
                text+='            <{key}  units="{unit}">{val:E}</{key}>\n'.format(key=key, unit=self.units[key], val=self.moves[key])
        text+= '        </UPDATE>\n'
        return text

    def toGlobal(self):
        """ Return moves cast to global coordinates """
        if self.coord =='global':
            return self.moves
        else:
            raise NotImplemented("Error")


    def toLocal(self):
        """ Return moves cast to local coordinates """
        if self.coord =='local':
            return self.moves
        else:
            raise NotImplemented("Error")
            # TO implement based on Ball's 'pmglobal_to_seg' in ./wfsc_core_algs/was_core_pmglobal_to_seg.pro
            # or the code in ./segment_control/mcs_hexapod_obj__define.pro


class SUR(object):
    """ Class for parsing/manipulating Segment Update Request files

    """
    def __init__(self, filename):
        """ Read a SUR from disk """
        self.filename=filename

        self._tree = etree.parse(filename)

        for tag in ['creator','date','time','version', 'operational']:
            self.__dict__[tag] = self._tree.getroot().attrib[tag]
        for element in self._tree.getroot().iter():
            if element.tag =='CONFIGURATION_NAME':  self.configuration_name = element.text
            if element.tag =='CORRECTION_ID':  self.correction_id = element.text

        self.groups = []
        for grp in self._tree.getroot().iter('GROUP'):
            myupdates = []
            for update in grp.iter('UPDATE'):
                #print update
                myupdates.append(Segment_Update(update))
            self.groups.append(myupdates)

    def __str__(self):
        outstr = "SUR %s\n" % self.filename #, type=%s, coords=%s\n" % (self.filename, 'absolute' if self.absolute else 'relative', self.coord)
        for igrp, grp in enumerate(self.groups):
            outstr+= "\tGroup %d\n" % (igrp+1)
            for update in grp:
                outstr+= "\t\t"+str(update)+"\n"
        return outstr

    @property
    def xmltext(self):
        """ The XML text representation of a given move """
        text = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<SEGMENT_UPDATE_REQUEST creator="?" date="{date}" time="{time}" version="0.0.1" operational="false" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="../../setup_files/schema/segment_update_request.xsd">
    <CONFIGURATION_NAME>{self.configuration_name}</CONFIGURATION_NAME>
    <CORRECTION_ID>{self.correction_id}</CORRECTION_ID>\n""".format(self=self, date='YYYY-MM-DD', time='HH:MM:SS')
    # FIXME add date and time keywords for real
        for igrp, grp in enumerate(self.groups):
            text+='    <GROUP id="{id}">\n'.format(id=igrp+1)
            for update in grp:
                text+=update.xmltext
            text+='    </GROUP>\n'
        text+= '</SEGMENT_UPDATE_REQUEST>'
        return text


    #@property
    #def name(self): return self._tree.getroot().attrib['name']


#---------------------------------------------------------------------------------



class Aperture(object):
    """ An Aperture, as parsed from the XML.
    All XML nodes are converted into object attributes. 

    See JWST-STScI-001550 for the reference on which this implementation was based. 

    4 Coordinate systems:
        * Detector: pixels, in raw detector read out axes orientation ("Det")
        * Science: pixels, in conventional DMS axes orientation ("Sci")
        * Ideal: arcsecs relative to aperture reference location. ("Idl")
        * Telescope: arcsecs V2,V3 ("Tel")

    """
    def __init__(self, xmlnode):

        convfactors = {'RADIANS': 1, 'DEGREES': np.pi/180, 'ARCSECS': np.pi/180/60/60}

        for node in xmlnode.iterchildren(): 
            tag = node.tag.replace('{http://www.stsci.edu/SIAF}','')
            if len(node.getchildren()) ==0:
                # if doens't have children, 
                try:
                    value = float(node.text) # do we care about ints vs floats?
                except: 
                    value=node.text
                self.__dict__[tag] = value
            else:
                # if does have children:
                if '{http://www.stsci.edu/SIAF}units' in [c.tag for c in node.getchildren()]:
                    # this will be an angle/units pair. units are either in arcsec or degrees. Convert to radians in any case for internal use.
                    unit  = node.find('{http://www.stsci.edu/SIAF}units').text
                    value =float( node.find('{http://www.stsci.edu/SIAF}value').text) * convfactors[unit]
                    self.__dict__[tag] = value
                elif '{http://www.stsci.edu/SIAF}elt' in [c.tag for c in node.getchildren()]:
                    #  an array of values which should go to an NDarray
                    elts = [float(c.text) for c in node.iterchildren('{http://www.stsci.edu/SIAF}elt')]
                    self.__dict__[tag] = np.asarray(elts)

                else:
                    raise NotImplemented("Not sure how to parse that node.")

        # pack things into NDarrays for convenient access
        # first the vertices
        self.XIdlVert = np.asarray((self.XIdlVert1, self.XIdlVert2,self.XIdlVert3,self.XIdlVert4))
        self.YIdlVert = np.asarray((self.YIdlVert1, self.YIdlVert2,self.YIdlVert3,self.YIdlVert4))

        # then the transformation coefficients
        self.Sci2IdlDeg = int(self.Sci2IdlDeg)
        self.Sci2IdlCoeffs_X = np.zeros( (self.Sci2IdlDeg+1, self.Sci2IdlDeg+1))
        self.Sci2IdlCoeffs_Y = np.zeros( (self.Sci2IdlDeg+1, self.Sci2IdlDeg+1))
        self.Idl2SciCoeffs_X = np.zeros( (self.Sci2IdlDeg+1, self.Sci2IdlDeg+1))
        self.Idl2SciCoeffs_Y = np.zeros( (self.Sci2IdlDeg+1, self.Sci2IdlDeg+1))
        for i in range(1,self.Sci2IdlDeg+1):
            for j in range(0,i+1):
                #if self.AperName == 'FGS2_FULL_CNTR':
                    #print 'Sci2IdlX{0:1d}{1:1d}'.format(i,j), self.__dict__['Sci2IdlX{0:1d}{1:1d}'.format(i,j)]
                self.Sci2IdlCoeffs_X[i,j] = self.__dict__['Sci2IdlX{0:1d}{1:1d}'.format(i,j)]
                self.Sci2IdlCoeffs_Y[i,j] = self.__dict__['Sci2IdlY{0:1d}{1:1d}'.format(i,j)]
                self.Idl2SciCoeffs_X[i,j] = self.__dict__['Idl2SciX{0:1d}{1:1d}'.format(i,j)]
                self.Idl2SciCoeffs_Y[i,j] = self.__dict__['Idl2SciY{0:1d}{1:1d}'.format(i,j)]

    def __repr__(self):
        return "<jwxml.Aperture object AperName={0} >".format(self.AperName)


    #--- the actual fundamental transformation code follows in these next routines:
    def Det2Sci(self, XDet, YDet):
        """ Detector to Science, following Section 4.1 of JWST-STScI-001550"""
        XDet = np.asarray(XDet, dtype=float)
        YDet = np.asarray(YDet, dtype=float)
        ang = np.deg2rad(self.DetSciYAngle)
        XSci = self.XSciRef + self.DetSciParity* ((XDet - self.XDetRef)* np.cos(ang) + (YDet-self.YDetRef) * np.sin(ang))
        YSci = self.YSciRef -                     (XDet - self.XDetRef)* np.sin(ang) + (YDet-self.YDetRef) * np.cos(ang)
        return XSci, YSci

    def Sci2Det(self, XSci, YSci):
        """ Science to Detector, following Section 4.1 of JWST-STScI-001550"""
        XSci = np.asarray(XSci, dtype=float)
        YSci = np.asarray(YSci, dtype=float)
 
        ang = np.deg2rad(self.DetSciYAngle)
        XDet = self.XDetRef + self.DetSciParity * (XSci - self.XSciRef ) * np.cos(ang) - (YSci - self.YSciRef ) * np.sin(ang)
        YDet = self.YDetRef + self.DetSciParity * (XSci - self.XSciRef ) * np.sin(ang) + (YSci - self.YSciRef ) * np.cos(ang)
        return XDet, YDet

    def Sci2Idl(self, XSci, YSci):
        """ Convert Sci to Idl
        input in pixel, output in arcsec """
        dX = np.asarray(XSci, dtype=float) - self.XSciRef
        dY = np.asarray(YSci, dtype=float) - self.YSciRef
 
        degree = self.Sci2IdlDeg
        #CX = self.Sci2IdlCoefX
        #CY = self.Sci2IdlCoefY

        #XIdl = CX[0]*dX + CX[1]*dY + CX[2]*dX**2 + CX[3]*dX*dY + CX[4]*dY**2
        #YIdl = CY[0]*dY + CY[1]*dY + CY[2]*dY**2 + CY[3]*dY*dY + CY[4]*dY**2
        XIdl = np.zeros_like(np.asarray(XSci), dtype=float)
        YIdl = np.zeros_like(np.asarray(YSci), dtype=float)

        for i in range(1,degree+1):
            for j in range(0,i+1):
                XIdl += self.Sci2IdlCoeffs_X[i,j] * dX**(i-j) * dY**j
                YIdl += self.Sci2IdlCoeffs_Y[i,j] * dX**(i-j) * dY**j


        return XIdl, YIdl

    def Idl2Sci(self, XIdl, YIdl):
        """ Convert Idl to  Sci
        input in arcsec, output in pixels """
        XIdl = np.asarray(XIdl, dtype=float)
        YIdl = np.asarray(YIdl, dtype=float)
 
        degree = self.Sci2IdlDeg
        #dX = XIdl #Idl origin is by definition 0 
        #dY = YIdl #Idl origin is by definition 0

        XSci = np.zeros_like(np.asarray(XIdl), dtype=float)
        YSci = np.zeros_like(np.asarray(YIdl), dtype=float)

        for i in range(1,degree+1):
            for j in range(0,i+1):
                XSci += self.Idl2SciCoeffs_X[i,j] * XIdl**(i-j) * YIdl**j
                YSci += self.Idl2SciCoeffs_Y[i,j] * XIdl**(i-j) * YIdl**j



        #CX = self.Idl2SciCoefX
        #CY = self.Idl2SciCoefY

        #XSci = CX[0]*dX + CX[1]*dY + CX[2]*dX**2 + CX[3]*dX*dY + CX[4]*dY**2
        #YSci = CY[0]*dY + CY[1]*dY + CY[2]*dY**2 + CY[3]*dY*dY + CY[4]*dY**2
        return XSci + self.XSciRef, YSci + self.YSciRef
        #return XSci, YSci

    def Idl2Tel(self, XIdl, YIdl):
        """ Convert Idl to  Tel

        input in arcsec, output in arcsec

        WARNING
        --------
        This is an implementation of the planar approximation, which is adequate for most
        purposes but may not be for all. Error is about 1.7 mas at 10 arcminutes from the tangent
        point. See JWST-STScI-1550 for more details.
        """
        XIdl = np.asarray(XIdl, dtype=float)
        YIdl = np.asarray(YIdl, dtype=float)
 
        #print self.V2Ref, self.V3Ref
        #rad2arcsec = 1./(np.pi/180/60/60)

        #V2Ref and V3Ref are now in arcseconds in the XML file
        ang = np.deg2rad(self.V3IdlYAng)
        V2 = self.V2Ref + self.VIdlParity * XIdl * np.cos(ang) + YIdl * np.sin(ang)
        V3 = self.V3Ref - self.VIdlParity * XIdl * np.sin(ang) + YIdl * np.cos(ang)
        return V2, V3

    def Tel2Idl(self,V2, V3):
        """ Convert Tel to Idl

        input in arcsec, output in arcsec

        This transformation involves going from global V2,V3 to local angles with respect to some
        reference point, and possibly rotating the axes and/or flipping the parity of the X axis.


        WARNING
        --------
        This is an implementation of the planar approximation, which is adequate for most
        purposes but may not be for all. Error is about 1.7 mas at 10 arcminutes from the tangent
        point. See JWST-STScI-1550 for more details.
        """
 
        #rad2arcsec = 1./(np.pi/180/60/60)
        dV2 = np.asarray(V2, dtype=float)-self.V2Ref
        dV3 = np.asarray(V3, dtype=float)-self.V3Ref
        ang = np.deg2rad(self.V3IdlYAng)

        XIdl = self.VIdlParity * (dV2 * np.cos(ang) - dV2 * np.sin(ang))
        YIdl =                    dV2 * np.sin(ang) + dV3 * np.cos(ang)
        return XIdl, YIdl

    #--- and now some compound transformations that are less fundamental. This just nests calls to the above.

    def Det2Idl(self, *args):
        return self.Sci2Idl(*self.Det2Sci(*args))
    def Det2Tel(self, *args):
        return self.Idl2Tel(*self.Sci2Idl(*self.Det2Sci(*args)))
    def Sci2Tel(self, *args):
        return self.Idl2Tel(*self.Sci2Idl(*args))
    def Idl2Det(self, *args):
        return self.Sci2Det(*self.Idl2Sci(*args))
    def Tel2Sci(self, *args):
        return self.Idl2Sci(*self.Tel2Idl(*args))
    def Tel2Det(self, *args):
        return self.Sci2Det(*self.Idl2Sci(*self.Tel2Idl(*args)))

    #--- now, functions other than direct coordinate transformations
    def convert(self, X, Y, frame_from, frame_to):
        """ Generic conversion routine, that calls one of the
        specific conversion routines based on the provided frame names as strings. """
        if frame_from == frame_to: return X, Y  # null transformation

        #frames = ['Det','Sci', 'Idl','Tel']
        function = eval('self.%s2%s' % (frame_from, frame_to))
        return function(X,Y)

    def corners(self, frame='Idl'):
        " Return coordinates of the aperture outline"
        return self.convert(self.XIdlVert, self.YIdlVert, 'Idl', frame)

    def center(self, frame='Tel'):
        """ Return the defining center point of the aperture"""
        return self.convert(self.V2Ref, self.V3Ref, 'Tel', frame)


    def plot(self, frame='Idl', label=True, ax=None, title=True, units='arcsec'):
        if units is None:
            units='arcsec'

        # should we flip the X axis direction at the end of this function?
        need_to_flip_axis = False # only flip if we created the axis
        if ax is None:
            ax = plt.gca()
            ax.set_aspect('equal')
            if frame=='Idl' or frame=='Tel':
                need_to_flip_axis = True # *and* we're displaying some coordinates in angles relative to V2.
                ax.set_xlabel('V2 [{0}]'.format(units))
                ax.set_ylabel('V3 [{0}]'.format(units))

            elif frame=='Sci' or frame=='Det':
                ax.set_xlabel('X pixels [{0}]'.format(frame))
                ax.set_ylabel('Y pixels [{0}]'.format(frame))



        x, y = self.corners(frame=frame)

        if units.lower() == 'arcsec':
            scale=1
        elif units.lower() =='arcmin':
            scale=01./60
        else:
            raise ValueError("Unknown units: "+units)


        x2 = np.concatenate([x, [x[0]]]) # close the box
        y2 = np.concatenate([y, [y[0]]])
        ax.plot(x2*scale,y2*scale) # convert arcsec to arcmin


        if need_to_flip_axis:
            ax.set_xlim(ax.get_xlim()[::-1])

        if label:
            ax.text(x.mean()*scale, y.mean()*scale, self.AperName, verticalalignment='center', horizontalalignment='center', color=ax.lines[-1].get_color())
        if title:
            ax.set_title("{0} frame".format(frame))


class SIAF(object):
    """ Science Instrument Aperture File """
    def __init__(self, instr='NIRISS', basepath="/Users/mperrin/Dropbox/JWST/Optics Documents/SIAF/"):
        """ Read a SIAF from disk 
        
        Parameters
        -----------
        instr : string
            one of 'NIRCam', 'NIRSpec', 'NIRISS', 'MIRI', 'FGS'; case sensitive.
        """

        if instr not in ['NIRCam', 'NIRSpec', 'NIRISS', 'MIRI', 'FGS']:
            raise ValueError("Invalid instrument name: {0}. Note that this is case sensitive.".format(instr))

        self.instrument=instr

        self.filename=os.path.join(basepath, instr+('_' if instr =='NIRISS' else '')+'SIAF.XML')

        self.apertures = {}

        self._tree = etree.parse(self.filename)



        #for entry in self._tree.getroot().iter('{http://www.stsci.edu/SIAF}SiafEntry'):
        for entry in self._tree.getroot().iter('SiafEntry'):
            aperture = Aperture(entry)
            self.apertures[aperture.AperName] = aperture

    def __getitem__(self, key):
        return self.apertures[key]

    def __len__(self):
        return len(self.apertures)

    @property
    def apernames(self):
        return self.apertures.keys()
 
    def plot(self, frame='Tel', names=None, label=True, units=None, clear=True):
        if clear: plt.clf()
        ax = plt.subplot(111)
        ax.set_aspect('equal')

        for ap in self.apertures.itervalues():
            if names is not None:
                if ap.AperName not in names: continue

            ap.plot(frame=frame, label=label, ax=ax, units=None)
        ax.set_xlabel('V2 [arcsec]')
        ax.set_ylabel('V3 [arcsec]')



        if frame =='Tel' or frame=='Idl':
            # enforce V2 increasing toward the left
            ax.autoscale_view(True,True,True)
            xlim = ax.get_xlim()
            if xlim[1] > xlim[0]: ax.set_xlim(xlim[::-1])
            ax.set_autoscalex_on(True)

class Test_SIAF(unittest.TestCase):

    def assertAlmostEqualTwo(self, tuple1, tuple2):
        self.assertAlmostEqual(tuple1[0], tuple2[0], places=1)
        self.assertAlmostEqual(tuple1[1], tuple2[1], places=1)


    def _test_up(self):
        siaf = SIAF("/Users/mperrin/Dropbox/JWST/Optics Documents/SIAF/JwstSiaf-2010-10-05.xml")
        startx = 1023
        starty = 1024

        nca = siaf['NIRCAM A']

        self.assertAlmostEqualTwo( nca.Det2Sci(startx,starty), (1020.,1020.))
        print "Det2Sci OK"

        self.assertAlmostEqualTwo( nca.Det2Idl(startx,starty), (0.0, 0.0))
        print "Det2Idl OK"
        self.assertAlmostEqualTwo( nca.Det2Tel(startx,starty), (87.50, -497.10))
        print "Det2Tel OK"

    def _test_down(self):
        siaf = SIAF("/Users/mperrin/Dropbox/JWST/Optics Documents/SIAF/JwstSiaf-2010-10-05.xml")
        startV2 = 87.50
        startV3 = -497.10
        nca = siaf['NIRCAM A']

        self.assertAlmostEqualTwo( nca.Sci2Det(1020., 1020), (1023.,1024.))
        print "Sci2Det OK"

        self.assertAlmostEqualTwo( nca.Tel2Idl(startV2, startV3), (0.0, 0.0))
        print "Tel2Idl OK"
        self.assertAlmostEqualTwo( nca.Tel2Sci(startV2, startV3), (1020., 1020.))
        print "Tel2Sci OK"
        self.assertAlmostEqualTwo( nca.Tel2Det(startV2, startV3), (1023.,1024.))
        print "Tel2Det OK"

    def test_inverses(self):
        siaf = SIAF("/Users/mperrin/Dropbox/JWST/Optics Documents/SIAF/JwstSiaf-2010-10-05.xml")
        nca = siaf['NIRCAM A']

        self.assertAlmostEqualTwo( nca.Det2Sci(*nca.Sci2Det(1020., 1020)), (1020., 1020) )
        self.assertAlmostEqualTwo( nca.Sci2Det(*nca.Det2Sci(1020., 1020)), (1020., 1020) )
        print "Det <-> Sci OK"

        self.assertAlmostEqualTwo( nca.Tel2Idl(*nca.Idl2Tel(10., 10)), (10., 10) )
        self.assertAlmostEqualTwo( nca.Idl2Tel(*nca.Tel2Idl(10., 10)), (10., 10) )
        print "Tel <-> Idl OK"

        self.assertAlmostEqualTwo( nca.Tel2Sci(*nca.Sci2Tel(10., 10)), (10., 10) )
        self.assertAlmostEqualTwo( nca.Sci2Tel(*nca.Tel2Sci(10., 10)), (10., 10) )
        print "Tel <-> Sci OK"




if __name__== "__main__":
    logging.basicConfig(level=logging.DEBUG,format='%(name)-10s: %(levelname)-8s %(message)s')
    #unittest.main()
    #sur = SUR('/itar/jwst/wss/MMS_Delivery/09_MMS_Source_Code/wfsc_mcs~1.1.2/wfsc_mcs/fqt/tc7/sur_ok_rel_gl.xml')

    s = SIAF()



