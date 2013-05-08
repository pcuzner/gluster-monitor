#!/usr/bin/env python
#
#	gtop - A performance and capacity monitoring program for glusterfs clusters
#
#	gtop-ip-utils : generic IP methods/classes called by the main gtop program
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

import socket
import netsnmp

class SNMPsession:
	
	def __init__(self,
			oid='sysdescr',
			version=2,
			destHost='localhost',
			community='gluster'):
		
		self.oid=oid
		self.version=version
		self.destHost=destHost
		self.community=community
	
	def query(self):
		"""	Issue the snmpwalk. If it fails the output is empty, not exception is thrown, so you need to check
			the returned value to see if it worked or not ;o)
		"""
		
		result = []
		# result is a tuple of string values
		snmpOut = netsnmp.snmpwalk(self.oid, Version=self.version, DestHost=self.destHost, Community=self.community, Retries=0, Timeout=100000)

		# convert any string element that is actually a number to a usable number (int)
		for element in snmpOut:
			if element == None:
				result.append(element) 
			elif element.isdigit():
				result.append(int(element))
			else:
				result.append(element)

		return result
		
def validIPv4(ip):
	"""	Attempt to use the inet_aton function to validate whether a given IP is valid or not """
	
	try:
		t = socket.inet_aton(ip)				# try and convert the string to a packed binary format
		result = True
	except socket.error:						# if it doesn't work address string is not valid
		result = False
	
	return result

	
def forwardDNS(name):
	"""	Use socket module to find name from IP, or just return empty"""
	
	try:
		result = socket.gethostbyname(name)			# Should get the IP for NAME
	except socket.gaierror:							# Resolution failure
		result = ""
	
	return result	
	
def reverseDNS(ip):
	"""	Use socket module to find name from IP, or just return the IP"""
	try:
		out = socket.gethostbyaddr(ip)				# returns 3 member tuple
		name = out[0]
		result = name.split('.')[0]					# only 1st entry is of interest, and only the 1st qualifier
	except:
		result = "" 
	
	return result
