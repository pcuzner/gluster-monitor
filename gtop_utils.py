#!/usr/bin/env python
#
#	gtop - A performance and capacity monitoring program for glusterfs clusters
#
#	gtop-utils : generic methods called by the main gtop program
#
#   Copyright (C) 2013 Paul Cuzner
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
import subprocess
import struct, datetime
from subprocess import PIPE,Popen					# used in screenSize and issueCMD

def convertBytes(inBytes):
	"""
	Routine to convert a given number of bytes into a more human readable form
	
	Input  : number of bytes
	Output : returns a MB / GB / TB value for bytes
	
	"""
	
	
	bytes = float(inBytes)
	if bytes >= 1125899906842624:
		size = round(bytes / 1125899906842624)
		#displayBytes = '%.1fP' % size
		displayBytes = '%dP' % size
	elif bytes >= 1099511627776:
		size = round(bytes / 1099511627776)
		displayBytes = '%dT' % size
	elif bytes >= 1073741824:
		size = round(bytes / 1073741824)
		displayBytes = '%dG' % size 
	elif bytes >= 1048576:
		size = int(round(bytes / 1048576))
		displayBytes = '%dM' % size
	elif bytes >= 1024:
		size = int(round(bytes / 1024))
		displayBytes = '%dK' % size 
	else:
		displayBytes = '%db' % bytes 

	return displayBytes
	
	
def issueCMD(cmd=""):
	""" Issue cmd to the system, and return output to caller as a list"""
	
	cmdWords = cmd.split()
	out = subprocess.Popen(cmdWords,stdout=PIPE, stderr=PIPE)
	(response, errors)=out.communicate()					# Get the output...response is a byte
															# string that includes \n
	
	return response.split('\n')								# use split to return a list


#def oct2Tuple(dateOctet):
#	""" This function call converts the SNMP datetime octet into a human readable
#		tuple. 
#	"""
#
#	thisOct = str(dateOctet[0])								# convert to str first
#	octLen = len(thisOct)
#	fmt = dict({8:'>HBBBBBB', 11:'>HBBBBBBcBB'})
#	
#	if octLen == 8 or octLen == 11:
#		dateTuple = struct.unpack(fmt[octLen],thisOct)
#		return dateTuple
#	else:
#		return []
#		
		
def oct2DateTime(dateOctet):
	""" receive an snmp date octet string and convert and return a datetime object
	"""
	
	thisOct = str(dateOctet[0])
	
	# SNMP returns an 11 byte str that includes UTC offset. Not interesting in that
	# so when we return the datetime object, we only return first 6 items (yy,mm,dd,hh,mm,ss)
	thistuple=struct.unpack('>HBBBBBBcBB',thisOct)
	t = datetime.datetime(*thistuple[:6])

	return t
		
#>> import datetime
#>> a = datetime.datetime.now()
#>> b = datetime.datetime.now()
#>> c = b - a
#datetime.timedelta(0, 8, 562000)
#>>> divmod(c.days * 86400 + c.seconds, 60)
#(0, 8)      # 0 minutes, 8 seconds		
