#!/usr/bin/env python
#
#	gtop - A performance and capacity monitoring program for glusterfs clusters
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
#
# Usage
# It is assumed that if you run the program on a gluster node, you want the interactive mode to get volume info and glusterd 
# checks. If this is not needed, the user uses the -s (server list) or -g (group) option to start gathering and displaying 
# the system stats for those systems
#
# Dependencies
# 1. snmp packages - net-snmp / net-snmp-utils (for snmpset) and net-snmp-python on each gluster node 
# 2. a working snmpd daemon (a sample snmpd.conf file is provided
# 3. [Optional] Update to rc.local on older systems to ensure network stats
#    are consistent (RHEL6)
#	 >snmpset -c RHS -v2c  127.0.0.1 1.3.6.1.4.1.8072.1.5.3.1.2.1.3.6.1.2.1.2.2 i 1
# 4. All gluster nodes are assumed to be registered in DNS
#			- also worthwhile using rc.local to execute the following to ensure NIC metrics are consistent with dstat/ifstat
#
# Data Refresh
# This program relies on the SNMP agent on each node. SNMP is hard coded for a 5 second refresh rate
# which means this tool inherits this level of granularity.
#
# References  	
# https://net-snmp.svn.sourceforge.net/svnroot/net-snmp/trunk/net-snmp/python/README
# http://www.ibm.com/developerworks/aix/library/au-netsnmpnipython/
# http://www.ibm.com/developerworks/aix/library/au-multiprocessing/
#
# Return Codes
# 00 .. completed successfully
# 04 .. parameter error program aborted during invocation
# 08 .. program started but xwindow size os too small for the UI to be displayed
#

import os, sys
import math											# only used for rounding up					
import threading									# object based module to handle multithreading

import netsnmp
import locale										# enabling curses display of unicode chars

import traceback									# tracing exceptions
import datetime

# modules and packages used for XML 
from xml.dom	import 	minidom
import xml.parsers.expat

from   time import strftime,gmtime, sleep
import time

import re											# regex module used for whitelisting interface names

from optparse   import OptionParser					# command line option parsing

from multiprocessing import Process, Queue, current_process, Pipe, Manager


import syslog										# Used for pushing error msgs to the syslog

import curses										# ncurses interface 

from gtop_utils import convertBytes, issueCMD, oct2DateTime
from gtop_iputils import SNMPsession, forwardDNS, reverseDNS, validIPv4



class GLUSTERvol:
	def __init__(self, name=""):
		"""	Initialise an instance of a new gluster volume """


		
		self.name = name
		
		self.fmtdName = name if len(name)<17 else name[:15] + ">"
		
		self.fmtdName 
		self.volType = ""
		self.numBricks = 0
		self.rawSize = 0
		self.usableSize = 0
		self.usedSize = 0
		self.freeSpace = 0
		self.graph =[]
		#self.highlight = False
			
	def printVol(self):
		"""	DEBUG routine used, to show the volumes attributes """
		
		print self.name
		print "\tBricks " + str(self.numBricks)
		print "\tType " + str(self.volType)
		print "\tRaw Space " + str(self.rawSize)
		print "\tUsable " + convertBytes(self.usableSize) + " (" + str(self.usableSize) + ")"
		print "\tUsed " + convertBytes(self.usedSize) + " (" + str(self.usedSize) + ")" 
		print "\nBricks used by this volume"
		
		self.printBricks()
	

	def printBricks(self):
		"""	DEBUG method to show the bricks assigned to a given volume """
		
		for xl in self.graph:
			if xl.type == "Brick":
				print xl.options['remote-host'] + " " + \
						xl.options['remote-subvolume'] + " " + \
						"Size " + str(xl.size) + " " + \
						"Used " + str(xl.used)
						
	def formatVol(self):
		"""	format volume data for display on the UI """
		
		volData = self.fmtdName.ljust(16) + "  " + \
				str(self.numBricks).rjust(4) + "    " + \
				volTypeShort[self.volType] + "   " + \
				convertBytes(self.usableSize).rjust(5) + "  " + \
				convertBytes(self.usedSize).rjust(5) + "   " + \
				convertBytes(self.freeSpace).rjust(5)
								
		if self.usableSize == 0:
			pctUsed = 0
			numBlocks = 1
		else:
			pctUsed = int((self.usedSize / float(self.usableSize)) *100)
			numBlocks = pctUsed / pctPerBlock
								
		if numBlocks < 1:
			numBlocks = 1
					
		# Use '>' initially so each 'block' will only be 1 char (encoding to unicode block occupies
		# 3 characters of text in the string, so using > keeps the formatting correct
		bar = ">"*numBlocks
								
		# Add the bar to the volume data, including a spacer added to the end
		volData = volData + "  " + bar + " " + str(pctUsed) + "%"
				
		spacer = 79 - len(volData)
		volData += " "*spacer
		
		# once the spacer is added convert the > symbol to a unicode 'block' symbol
		volData = volData.replace('>',block.encode('utf_8'))

		return volData

	def updateVol(self):
		"""	Update the volume space information, based on the usage of the underlying bricks 
		and the types of translators used by the volume. The routine relies on the definition of the
		vol file and the current peer names being consistent.
		 
		For example, if the cluster was formed using DNS names, and later uses IP - the vol file may
		still have entries there (remote-host field) referencing names; this routine attempts to use 
		the current name (which would be IP based) to match against a brick which fails - result is 
		the output shows 0b against all volumes!
		"""
		self.rawSize = 0										# reset raw size to recalculate based
																# on current observation
																
		for xl in self.graph:									# look at this volumes Xlators
			
			if xl.type == "Brick":								# if this Xlator is a brick just add
				self.rawSize += xl.size						# to raw total of volume
			
			elif xl.type == "Replicated":
				usable=[]
				used=[]
															# this is a replica set, so we iterate over
															# brick subvolumes
				for brick in xl.subvolumes:
					if brick.size >= 0:
						usable.append(brick.size)
						
					used.append(brick.used)
				xl.size = max(usable)						# all bricks in a replica should be of
				xl.used = max(used)							# equal size, but taking the max incase any of the bricks 
															# are offline at scan time.									
				
			elif xl.type == "Distributed" or xl.type == "Striped":

					xl.size = 0								# reset the size/used, ready to recalculate
					xl.used = 0								# from current child subvolumes
					for child in xl.subvolumes:
						xl.size += child.size
						xl.used += child.used
		
			else:
				pass 
	
						
		for xl in self.graph:								# with the sizes calculated, propogate the 
			if xl.parent == None:							# top level XL objects values to the Volume
				self.usableSize = xl.size
				self.usedSize   = xl.used
				self.freeSpace = self.usableSize - self.usedSize
	

		
class Xlator():
	def __init__(self, name=""):
		self.volname = ""
		self.name=name
		self.type=""
		self.parent=None
		self.subvolumes = []
		self.options = {}
		self.size = 0
		self.used = 0
				

class Cluster:
	
	def __init__(self):
		self.nodes = []						# list of peer node objects in the cluster
		self.evictNodes=[]					# when nodes drop out of the main list catch them here for diagnostics
		self.nodeNames = []					# displayable node names in the cluster
		self.peerCount = 0
		self.processList = []
		self.version = ""					# version of glusterfs on the running host
		self.activeNodes = 0
		self.volumes=[]						# list of volumes within the cluster
		self.brickXref={}					# dict pointing a brick to the volume that owns it
		self.brick2Xlator={}				# dict pointing a brick path to the relevant translator
		self.avgCPU = 0
		self.peakCPU = 0
		self.aggrNetIn = 0
		self.aggrNetOut = 0
		self.aggrDiskR = 0
		self.aggrDiskW = 0 
		self.rawCapacity = 0
		self.usableCapacity = 0
		self.usedCapacity = 0
		self.freeCapacity = 0
		
	
	def addHost(self,hostName):
		"""	Receive a node to add to the clusters node list """

	
		newnode = GLUSTERhost(hostName=hostName)
		self.nodes.append(newnode)
		self.nodeNames.append(hostName)

	def validateServers(self,serverList):
		"""	This function takes a list of servers (comma separated string), and attempts to 
			validate the server name/IP and create glusterHOST objects for each valid server
		"""
	
		svrs = serverList.split(',')
		
		if svrs:											
			for thisSvr in svrs:
				name2Add = serverOK(thisSvr)
				if name2Add:	
					self.addHost(hostName=name2Add)
				else:
					print "Can't resolve supplied server name of " + thisSvr
		
	def getGlusterPeers(self):
		"""	Look for the glusterfs peers directory to confirm whether gluster is installed, if so
			read the peer files to pick out the hostname, building a string of servers to push to 
			the validateServers function
		"""
		
		peers = []
		
		if os.path.exists(peersDir):								# Does path exist?
			
			for nodeCfg in os.listdir(peersDir):					# Yes, so process each file
				nodeCfgPath = os.path.join(peersDir,nodeCfg)
				for line in open(nodeCfgPath):						# looking for the hostname keyword  
	
					p = line.strip().split('=')
					if p[0] == 'hostname1':							# to build the server list
						peers.append(p[1])
			
			peers.append(os.getenv('HOSTNAME').split('.')[0])		# Add this host to the list		
			self.peerCount = len(peers)								
			peersList = ",".join(peers)
			self.validateServers(peersList)
			

		
	def getGlusterVols(self):
		"""	Open this hosts gluster vol file to build volume objects and then 
			attach them to the cluster object via a list 
			
			This function is derived from the work Niels did on the 'lsgvt' script
			
		"""
		
		types = {'cluster/distribute' : 'Distributed', 
				'cluster/stripe' : 'Striped', 
				'cluster/replicate' : 'Replicated', 
				'protocol/client' : 'Brick'}
		
		#-----------------------------------------------------------------------------------
		# build the volume objects from the volfiles
		# output is 
		# 1. a list of volume objects
		# 2. a dict pointing a given brick to a volume object that contains the brick
		#-----------------------------------------------------------------------------------
		for thisDir in os.listdir(volDir):
			volFile = os.path.join(volDir,thisDir,thisDir + "-fuse.vol")
			thisVol = GLUSTERvol(name=thisDir)
			self.volumes.append(thisVol)
			
			stack =[]						# List to hold the translators found for this volume
			layout = []						# list holding data layout XL types e.g. distributed
		
			xl = None					
			
			for line in open(volFile):
				words = line.split()
				
				if not words: continue
	
				if words[0] == 'volume':
					xl = Xlator()
					xl.volname = thisDir
					xl.name = words[1]
					
				elif words[0] == 'type':
					
					if words[1] in types.keys():
						xl.type = types[words[1]]
						
						if xl.type == "Brick":					
							thisVol.numBricks += 1				# increase the brick count 
	
						else:									# valid Xlator, so just add the type
							if xl.type in layout:				# to layout list for propogation to 
								pass							# owning volume object
							else:	
								layout.append(xl.type)
		
				elif words[0] == 'option':
					xl.options[words[1]] = words[2]
					
				elif words[0] == 'subvolumes':
					xl.subvolumes = words[1:]
					
				elif words[0] == 'end-volume':
					# only keep xlators that describe the volume layout
					if xl.type in types.values():
						
						if xl.type == "Brick":
							# Grab this translators hostname and filesystem name (brick)
							thisHost = xl.options['remote-host']
							thisPath = xl.options['remote-subvolume']
																
							ptr =  thisHost + ":" + thisPath		
							
							# the gCluster object maintains a list of bricks to translators
							# used for file system size information tracking/calculations
							self.brickXref[ptr] = thisVol
							self.brick2Xlator[ptr] = xl				
																
						stack.append(xl)
					xl = None
	
			# replace the subvolumes 'volname' by the xlator object
			for xl in stack:
				xl.subvolumes = [_xl for _xl in stack if _xl.name in xl.subvolumes]
				for subvol in xl.subvolumes:
					subvol.parent = xl
		
		
			thisVol.graph = list(stack)
			layout.reverse()								# Add the volume type description to the 
			thisVol.volType = '-'.join(layout)				# volume object
		

	
	def getVersion(self):
		"""	Simple function to retrieve the version of gluster running on the node """
		
		versionInfo = issueCMD(cmd="glusterfsd --version")
		# First line of the output looks like this
		# glusterfs 3.3.0.5rhs built on Nov  8 2012 22:30:35
		
		self.version = versionInfo[0].split()[1]

	def updateActive(self):
		"""	Maintain the cluster objects active node count based on the state of all 
			the nodes in the cluster """
			
		self.activeNodes = 0
		for node in self.nodes:
			if node.state == 'connected':
				self.activeNodes += 1
			
	def updateStats(self):
		"""	Process the nodes in the cluster, to create an aggregate view of the 
			clusters throughput for display in the information window (top 3 lines 
			of the console """
		
		cpuStats = []
		totalNetIn = 0
		totalNetOut = 0
		totalDiskR = 0
		totalDiskW = 0 
		
		# Process each node in the cluster
		for node in gCluster.nodes:
			cpuStats.append(node.cpuBusyPct)
			totalNetIn += node.netInRate
			totalNetOut += node.netOutRate
			totalDiskR += node.blocksReadAvg
			totalDiskW += node.blocksWriteAvg
		
		# Use the updated stats to derive averages and aggregates for the cluster	
		if cpuStats:
			
			gCluster.avgCPU = int(sum(cpuStats)/len(cpuStats))
			gCluster.peakCPU = max(cpuStats)
			gCluster.aggrNetIn = totalNetIn
			gCluster.aggrNetOut = totalNetOut
			gCluster.aggrDiskR = totalDiskR
			gCluster.aggrDiskW = totalDiskW
			
		else:
			
			gCluster.avgCPU = 0
			gCluster.peakCPU = 0 
			gCluster.aggrNetIn = 0
			gCluster.aggrNetOut = 0
			gCluster.aggrDiskR = 0 
			gCluster.aggrDiskW = 0 	
			
		pass 
	
	def formatStats(self,prefix=""):
		"""	Format the aggregate stats maintained in updateStats for display in batch mode """
		
		if FORMAT == "readable":
			displayStats = prefix + " < ALL >".ljust(15) + " " \
						+ "     " \
						+ str(self.avgCPU).rjust(3) + " " \
						+ "     " \
						+ "     " \
						+ "       " \
						+ convertBytes(self.aggrNetIn).rjust(5) + "  " \
						+ convertBytes(self.aggrNetOut).rjust(5) + "  " \
						+ convertBytes(self.aggrDiskR*BLOCKSIZE).rjust(5) + "  " \
						+ convertBytes(self.aggrDiskW*BLOCKSIZE).rjust(5)
		else:
			displayStats = prefix + ",<ALL>" + "," \
						+ "," \
						+ str(self.avgCPU) \
						+ "," \
						+ "," \
						+ "," \
						+ str(self.aggrNetIn) + "," \
						+ str(self.aggrNetOut) + "," \
						+ str(self.aggrDiskR*BLOCKSIZE) + "," \
						+ str(self.aggrDiskW*BLOCKSIZE)
#
		return displayStats
		
	def SNMPcheck(self):
		""" Try to do a high level snmpwalk to see if snmp is listening """
	
		servers = list(self.nodes)							# Create a fresh copy of the list

		
		for node in servers:							# Process each server 
			target = node.hostName
			print "---> " + target + "",
			s = SNMPsession(destHost=target,community=SNMPCOMMUNITY)
			s.oid=netsnmp.Varbind('sysDescr')
			validSNMP = s.query()						# Try a walk to see if SNMP responds
			
			if validSNMP:
				print "OK"
			else:
				print "not reachable over SNMP, dropping " + target + " from list"
				servers.remove(node)
	
		if len(servers) < len(self.nodes):				# if there has been a change, update the 
			self.nodes = list(servers)					# cluster objects server list

	def dump(self):
		"""	DEBUG routine to show what objects and attributes the cluster currently has """
		
		if self.volumes:
			print str(len(self.volumes)) + " volumes found:"
			for volume in self.volumes:
				volume.printVol()
		
		for n in self.nodes:
			print n.hostName
			print n.state
			print n.timeStamp 
			
		print "active nodes " + str(self.activeNodes)
		
		print "evicted nodes "
		for n in self.evictNodes:
			print n.hostName
			print n.state
			print n.errMsg
				
		

def screenSize():
	"""	Routine which uses stty to determine the size of the console window, Only used in
		batch mode to trigger the headers to be re-displayed
	
		Requires - Linux stty command
	"""
	
	data = issueCMD('stty size')							# returns a single string "y x"
	dimensions = data[0].split()							# split it up
	y = int(dimensions[0])
	x = int(dimensions[1])

	return y,x




class GLUSTERhost:
	""" Class for gluster nodes, holding the hosts data and containing the methods
		to populate and manage the data
	"""
	def __init__(self, hostName=None,state='unknown'):
		# Need to audit the variable declarations, some may not be used..
		
		self.hostName = hostName				# used

		# look at the hostname and determine how to format it for
		# display
		if '.' in self.hostName:
			# fqdn for hostname, so just extract host name
			self.fmtdName = self.hostName.split('.')[0]
		else:
			self.fmtdName = self.hostName

		if len(self.fmtdName) > 14:
			self.fmtdName = self.fmtdName[:14] + ">"
		else:
			self.fmtdName = self.fmtdName.ljust(15)
		
		
		self.hostActive = True					# used 
		self.state = state						# used
		self.peers = 0
		#self.highlight = False
		self.reset()

	
	def reset(self):
		self.cpuSysPct = 0
		self.cpuWaitPct = 0 
		self.cpuUserPct = 0
		self.cpuIdlePct = 0
		self.cpuBusyPct = 0						# Not USED
		self.cpuSys = 0 
		self.cpuUser = 0 
		self.cpuIdle = 0
		self.cpuWait = 0 
		self.diffUser = 0
		self.diffSys = 0 
		self.diffWait = 0
		self.diffIdle = 0
		self.memTotal = 0						# used
		self.memAvail = 0						# used
		self.memUsedPct = 0						# used
		self.swapTotal = 0						# used
		self.swapAvail = 0						# used
		self.swapUsedPct = 0					# used
		self.blocksReadAvg = 0					# used
		self.blocksWriteAvg = 0 				# used
		self.netIn = 0							# used
		self.netInRate = 0 						# used
		self.netOutRate = 0 					# used
		self.netOut = 0							# used
		self.lcpuSys = 0 
		self.lcpuUser = 0 
		self.lcpuIdle = 0
		self.lcpuWait = 0 
		self.lblocksRead = 0					# used
		self.lblocksWritten = 0 				# used
		self.lnetIn = 0 						# used
		self.lnetOut = 0						# used
		self.ltotalChange = 0
		self.nicList = []						# used
		self.brickfsOffsets = []				# used
		self.procCount = 0						# used
		self.errMsg = ''
		self.brickInfo = {}						# used, size[0] and used[1] info for each brick	
		self.ctdb = "."							# used
		self.samba = "."						# used
		self.nfs = "."							# used
		self.selfHeal = "."						# used
		self.georep = "."						# used
		self.timeStamp = None					# used
		
		return 

	def getData(self):
		
		# Default is to assume snmp will work, and then turn off this state if 
		# an error occurs
		self.hostActive = True
		
		s = SNMPsession(destHost=self.hostName,community=SNMPCOMMUNITY)
		
		if self.procCount == 0:				# On 1st run, get the number of processors for this host
			s.oid=netsnmp.Varbind('hrDeviceType')
											# count hrDeviceProcessor occurances				
			self.procCount = s.query().count('.1.3.6.1.2.1.25.3.1.3')
		
		#------------------------------------------------------------------------------------------------------
		# Get the memory usage stats from the server, and add to the memory stats
		#------------------------------------------------------------------------------------------------------		
		s.oid = netsnmp.Varbind('memory')
		memInfo = s.query()
		
		if memInfo:										# if this is empty, host has stopped answering
			self.swapTotal = memInfo[2]
			self.swapAvail =  memInfo[3]
			self.memTotal =  memInfo[4]
			self.memAvail =  memInfo[5]
			self.swapUsedPct = 0 if int(self.swapTotal) == 0 else int(round((self.swapTotal - self.swapAvail)/float(self.swapTotal)*100))
			self.memUsedPct = int(round((self.memTotal - self.memAvail)/float(self.memTotal)*100))
		else:
			self.errMsg = "snmp query for memory failed"
			self.hostActive = False
			return

		#------------------------------------------------------------------------------------------------------
		# Grab this systems current datetime 		
		#------------------------------------------------------------------------------------------------------
		s.oid = netsnmp.Varbind('hrSystemDate')
		dateOct = s.query()										# SNMP returns this as an octet string
		if dateOct:
			self.timeStamp = oct2DateTime(dateOct)
			#syslog.syslog("sent data to main process for " + self.timeStamp)
			#print self.timeStamp
		else:
			self.errMsg = "SNMP query for the datestamp - hrSystemDate - failed"
			self.hostActive = False
			return
		

		#------------------------------------------------------------------------------------------------------
		# Process the systemStats table
		# NB. SNMP agent only polls every 5 seconds, current and lat have to be compared to calculate consumption
		# SNMP data not that reliable for CPU info, so need to add try/except clauses
		#------------------------------------------------------------------------------------------------------
		
		s.oid = netsnmp.Varbind('systemStats')						# Grab the whole stats table
		systemStats = s.query()		
		
		if systemStats:												# check we have data to process
			
			userDiff,sysDiff,waitDiff,idleDiff,totalDiff = 0,0,0,0,0
			
			if self.lcpuUser == 0:					# First run clause
				self.lcpuUser = systemStats[11]
			else:
				
				try:
					userDiff = systemStats[11] - self.lcpuUser
					if userDiff == 0:
						userDiff = self.diffUser		# use value from last poll
					else:
						self.diffUser = userDiff
					self.lcpuUser = systemStats[11]
				except IndexError:
					userDiff = self.diffUser
					
				
			if self.lcpuSys == 0:
				self.lcpuSys = systemStats[13]
			else:
				try:
					sysDiff = systemStats[13] - self.lcpuSys
					if sysDiff == 0:
						sysDiff = self.diffSys		# use value from last poll
					else:
						self.diffSys = sysDiff
					self.lcpuSys = systemStats[13]
				except IndexError:
					sysDiff = self.diffSys
				
				
			if self.lcpuWait == 0:
				self.lcpuWait = systemStats[15]
			else:
				
				try:
					waitDiff = systemStats[15] - self.lcpuWait
					if waitDiff == 0:
						waitDiff = self.diffWait		# use value from last poll
					else:
						self.diffWait = waitDiff
					self.lcpuWait = systemStats[15]		
				except IndexError:
					waitDiff = self.diffWait
	
			if self.lcpuIdle == 0:
				self.lcpuIdle = systemStats[14]
			else:
				try:
					idleDiff = systemStats[14] - self.lcpuIdle
					if idleDiff == 0:
						idleDiff = self.diffIdle		# use value from last poll
					else:
						self.diffIdle = idleDiff
					self.lcpuIdle = systemStats[14]
				except IndexError:
					idleDiff = self.diffIdle
					
			totalDiff = userDiff + sysDiff + waitDiff + idleDiff
			
			if totalDiff > 0:						# Changes detected, updated counters
				self.cpuUserPct = (userDiff / ((float(refreshRate) * 100) * self.procCount))*100
				self.cpuSysPct = (sysDiff / ((float(refreshRate) * 100) * self.procCount))*100
				self.cpuWaitPct = (waitDiff / ((float(refreshRate) * 100) * self.procCount))*100
				self.cpuIdlePct = (idleDiff / ((float(refreshRate) * 100) * self.procCount))*100
				self.cpuBusyPct = int(self.cpuUserPct + self.cpuSysPct + self.cpuWaitPct)
				
				# After SNMP starts the numbers can be a little wierd. Catch them here and just reset to 0
				if self.cpuBusyPct > 100:
					self.cpuBusyPct = 0
				


			#----------------------------------------------------------------------------------------
			# Process high level IO stats ---> FIXME Add a try and except for IndexError
			# SNMP block data is not available immediately
			# takes about 30 secs for snmp agent to respond with so scans within this time frame, 
			# will not populate list items 18 and 19 - which would trigger the IndexError exception
			#----------------------------------------------------------------------------------------		
			if len(systemStats) >= 18:								
			
				if self.lblocksRead == 0:
					self.lblocksRead= systemStats[19]
				else:
					blocksChanged = systemStats[19] - self.lblocksRead
					self.lblocksRead = systemStats[19]
					self.blocksReadAvg = blocksChanged / refreshRate
					
				if self.lblocksWritten == 0:
					self.lblocksWritten = systemStats[18]
				else:
					blocksChanged = systemStats[18] - self.lblocksWritten
					self.lblocksWritten = systemStats[18]
					self.blocksWriteAvg = blocksChanged / refreshRate

				

		else:
			self.errMsg = "SNMP query for system stats failed"
			self.hostActive = False
			return													# Leave the getData thread
		

		#------------------------------------------------------------------------------------------------------
		# Process the network stats data and add to this gluster host
		# Using interface table (iftable) - .1.3.6.1.2.1.2.2
		# nscache entry update for this is at .1.3.6.1.4.1.8072.1.5.3.1 concat with iftable oid
		#
		# You could therefore lower the 5 sec snmp update for if data using snmpset since this oid is managed
		# by within nscache table
		# i.e --> snmpset -c gluster -v2c  127.0.0.1 1.3.6.1.4.1.8072.1.5.3.1.2.1.3.6.1.2.1.2.2 i 1
		# if the stats look like they have wholes in (0b), when there should have been load, use the snmpset on 
		# each node (could at this to the snmpd startup preferably the rc.local file
		#------------------------------------------------------------------------------------------------------
		if not self.nicList:								# Only run this the first time a host is polled 
															# to get a list of NICs to use for the aggregation
			s.oid = netsnmp.Varbind('ifName')				# Query ifName table, then look for phys interfaces 
			interfaces = s.query()							# we want to use based on the whiteList global var
			ctr = 0
			for ifname in interfaces:
				if re.match(whiteList,ifname):
					self.nicList.append(ctr)
				ctr += 1	
		
		# Use 64bit network counters. -ve values will occur when the difference between current is at the start
		# of the 64 range, and last reading was at the end. This is caught and corrected
		s.oid = netsnmp.Varbind('ifHCInOctets')
		netInData = s.query()
		if netInData:
				
			netIn = sum([netInData[idx] for idx in self.nicList])
				
			if self.lnetIn == 0:
				self.lnetIn = netIn
				self.netInRate = 0
			else:
				
				if netIn > self.lnetIn:
					bytesChanged = netIn - self.lnetIn
				else: 

					# bytesChanged = (4294967296 - self.lnetIn) + netIn		# for counter32 variant
					bytesChanged = (18446744073709600000 - self.lnetIn) + netIn
					
				self.lnetIn = netIn
				self.netInRate = bytesChanged / float(refreshRate)
		else:
			self.errMsg = "ERR: snmp query for memory net in data failed"
			self.hostActive = False
			return													# Leave the getData thread
			
		# Using 64bit High capacity (HC) network counters - as above
		s.oid = netsnmp.Varbind('ifHCOutOctets')
		netOutData = s.query()
		if netOutData:
		
			netOut = sum([netOutData[idx] for idx in self.nicList])
			
			if self.lnetOut == 0:
				self.lnetOut = netOut
				self.netOutRate = 0
			else:
				
				if netOut > self.lnetOut:
					bytesChanged = netOut - self.lnetOut
				else:
					bytesChanged = (18446744073709600000 - self.lnetOut) + netOut
					
				self.lnetOut = netOut
				self.netOutRate = bytesChanged / float(refreshRate)
		else:
			self.errMsg = "ERR: snmp query for net out data failed"
			self.hostActive = False
			return

 

	def getState(self):
		""" Find out whether key gluster processes are active. 

		"""
		#print "getting state information"
		s = SNMPsession(destHost=self.hostName,community=SNMPCOMMUNITY)
		s.oid = netsnmp.Varbind('hrSWRunName')					# .1.3.6.1.2.1.25.4.2.1.2
		processList = s.query()
		
		if processList:
			if 'glusterd' in processList:
				self.state = 'connected'
			else:
				self.state = 'disconnected'
			if 'ctdbd' in processList:
				self.ctdb = 'Y'
			else:
				self.ctdb = '.'
			if 'smbd' in processList:
				self.samba = 'Y'
			else:
				self.samba = '.'
				
		else:
			self.errMsg = "query for process list bombed"
			self.hostActive = False
			return 
			
		# query of the hrSWRunName gives us the name of the process, but to look
		# for gluster nfs and gluster self heal pids we need the hrSWRunParameters
		
		s.oid = netsnmp.Varbind('hrSWRunParameters')			# .1.3.6.1.2.1.25.4.2.1.5
		paramList = s.query()
		
		self.nfs = "."
		self.selfHeal = "."
		self.georep = "."
		
		if paramList:	

			# Look at the list of param's for all the processes
			for parm in paramList:

				# Ignore items that are not string objects
				if isinstance(parm, basestring):
					
					if parm[:2] in ["-f", "-s"]:
						
						if "nfs" in parm:
							self.nfs = "Y"
						elif "glustershd" in parm:
							self.selfHeal = "Y"
					elif "gsyncd.py" in parm:
							self.georep = "Y"
							
		else:
			
			self.errMsg = "query for param list from process table failed"
			self.hostActive = False
			return
	
	
	
	def getDiskInfo(self, nameSpace):
		"""	Use SNMP to get the current usage across mounted filesystems """
	
		s = SNMPsession(destHost=self.hostName,community=SNMPCOMMUNITY)	
																# first time through look through the filesystem
		if not self.brickfsOffsets:								# descriptions, and if any match our bricks
																# record the offset in the brickfsOffset list
			
			s.oid = netsnmp.Varbind('hrStorageDescr') 			# .1.3.6.1.2.1.25.2.3.1.3
			filesystems = s.query()
			
			if filesystems:
				
				# Start at the end of the list and work backwards. Going forwards is problematic since
				# some systems don't report descr/size/used in sync. For example, in F17 descr and size
				# provide a field for Shared Memory, but used does not so using an index that starts at 
				# the beginning results in index out of range conditions.
				ctr = -1
				for fs in reversed(filesystems):
					ptr = self.hostName + ":" + fs

					if nameSpace.gCluster.brickXref.has_key(ptr):
						self.brickfsOffsets.append([ctr,ptr])

						self.brickInfo[ptr]=[0,0]
					ctr +=-1
			else:
				self.errMsg = "query to filesystems descr failed"
				self.hostActive = False
				return
				
		#print "diskinfo has found " + str(len(self.brickfsOffsets)) + " matching bricks"		# DEBUG
		
		

		s.oid = netsnmp.Varbind('hrStorageSize')				# .1.3.6.1.2.1.25.2.3.1.5
																
		sizeData = s.query()

		if sizeData:
			for ctr,ptr in self.brickfsOffsets:
		

				#if ctr <= len(sizeData):

				# The sizes returned by the query are in allocation units, which is 4k 
				# so by multipling by 4096 gives bytes
				self.brickInfo[ptr][0] = int(sizeData[ctr]) * 4096	
																
		else:
			self.errMsg = "query for filesystem size data failed"
			self.hostActive = False
			return 
		
		
		s.oid = netsnmp.Varbind('hrStorageUsed')				# .1.3.6.1.2.1.25.2.3.1.6
		usedData = s.query()
		
		if usedData:
			for ctr,ptr in self.brickfsOffsets:

				#if ctr<= len(usedData):
				self.brickInfo[ptr][1] = int(usedData[ctr]) * 4096
					

		else:
			self.hostActive = False
			self.errMsg = "query for filesystem used failed"
			return 

		
	
	def formatData(self,prefix=""):
		"""	Function to format a hosts statistics ready for display to the UI or stdout """
		

		if interactiveMode:

			displayStats = nodeStatus[self.state].encode('utf-8') + " " + self.fmtdName + " " \
						+ str(self.procCount).rjust(3) + "  " \
						+ str(self.cpuBusyPct).rjust(3) + " "  \
						+ convertBytes((self.memTotal*1024)).rjust(5) + "  " \
						+ str(self.memUsedPct).rjust(3) + " " \
						+ str(self.swapUsedPct).rjust(3) + "  " \
						+ self.ctdb + " " \
						+ self.samba + " " \
						+ self.nfs + " " \
						+ self.selfHeal + " " \
						+ self.georep + "  " \
						+ convertBytes(self.netInRate).rjust(5) + " " \
						+ convertBytes(self.netOutRate).rjust(5) + "  " \
						+ convertBytes(self.blocksReadAvg*BLOCKSIZE).rjust(5) + "  " \
						+ convertBytes(self.blocksWriteAvg*BLOCKSIZE).rjust(5) + "  "
						
		else:
			if FORMAT == 'readable':
				displayStats = prefix + " " + self.fmtdName + " " \
							+ str(self.procCount).rjust(3) + " " \
							+ str(self.cpuBusyPct).rjust(3) + " "  \
							+ convertBytes((self.memTotal*1024)).rjust(5) + "  " \
							+ str(self.memUsedPct).rjust(3) + "  " \
							+ str(self.swapUsedPct).rjust(3) + "  " \
							+ convertBytes(self.netInRate).rjust(5) + "  " \
							+ convertBytes(self.netOutRate).rjust(5) + "  " \
							+ convertBytes(self.blocksReadAvg*BLOCKSIZE).rjust(5) + "  " \
							+ convertBytes(self.blocksWriteAvg*BLOCKSIZE).rjust(5)
			else:
				displayStats = prefix + "," + self.hostName + "," \
							+ str(self.procCount) + "," \
							+ str(self.cpuBusyPct) + ","  \
							+ str(self.memTotal*1024) + "," \
							+ str(self.memUsedPct) + "," \
							+ str(self.swapUsedPct) + "," \
							+ str(self.netInRate) + "," \
							+ str(self.netOutRate) + "," \
							+ str(self.blocksReadAvg*BLOCKSIZE) + "," \
							+ str(self.blocksWriteAvg*BLOCKSIZE)
					
		return displayStats
		

	
			
def printHeader(headerType='readable'):
	
	if headerType == "readable":
		global screenX, ScreenY
		screenY,screenX = screenSize()						# test the screen size again incase window is resized
		hdrs=[]
	
		hdrs.append("                             CPU        Memory %  Network AVG   Disk I/O AVG")
		hdrs.append("  Time    Gluster Node   C/T  %   RAM  Real Swap    In    Out   Reads Writes")
		hdrs.append("-------- --------------- --- --- ----- ----|---- ------|------ ------|------")
		
		for line in hdrs:
			print line
		
		triggerRow = screenY - len(hdrs)
		
		return triggerRow
	
	else:
		print "TimeStamp,GlusterNode,Cores,CPU%,RAM,Real%,Swap%,NetInBytes,NetOutBytes,DiskReadAVG,DiskWriteAVG"


def serverOK(server):
	"""	check a given name/ip is ok to use, if not return blank """
	
	result = ''
	
	if validIPv4(server):						# format is IPv4, so
		dnsName = reverseDNS(server)			# try and get a friendly DNS name and use that
		if dnsName:
			result = dnsName
		else:									# but if that fails,. just use the valid IPv4
			result = server 
		
	elif forwardDNS(server):					# svr is not a valid IPv4 name, so assume it is a name and check DNS
		result = server
	
	else:
		pass

	return result							# return blank, server name or IP


def initScreen():
		
	screen = curses.initscr()
	#handleColors = curses.has_colors()
	
	curses.start_color()
	curses.use_default_colors()

	screen.nodelay(1)		# keyscan is non-blocking
	curses.noecho()			# Turn off echo to screen to allow, so keypresses can be captured
	curses.cbreak()			# Allow keys to be used instantly, without pressing ENTER
	screen.keypad(1)		# Keypad and arrow keys enabled
	curses.curs_set(0)		# Make cursor invisible
	return screen


def resetScreen(screen):
	"""	Return the console to a known state """
	curses.nocbreak()
	screen.keypad(0)
	curses.echo()
	curses.curs_set(2)		# turn cursor back on
	curses.endwin()			# end window session

def processConfigFile(fileName):
	"""	function that looks for a config file in the users home directory to build
		server groups and set environment variables up 
	
		Returns a list called variables, where each item is a variable assignment
				a dict indexed by a group name, containing a string of comma separated names
				
	"""
	

	serverGroups={}
	variables = []

	if os.path.exists(configFile):
		try:
			# Process the xml config file building a DOM structure to parse
			# check if config file exists, before trying to use it
			xmldoc = minidom.parse(configFile)
			
			# Process any parameter overrides from the config file
			parmList = xmldoc.getElementsByTagName('parm')
			
			for parm in parmList:
	
				varName = str(parm.attributes.keys()[0])
				varValue = parm.attributes[varName].value 
				
				if varValue.isdigit():
					varValue = int(varValue)
				else:
					varValue = "'"+ varValue + "'"
				
				varCmd = varName + " = " + str(varValue)
				variables.append(varCmd)
				
			
			# From the DOM, create a list of group objects
			groupList = xmldoc.getElementsByTagName('group')
			
			# Process each group stanza
			for group in groupList:
			
				groupName = str(group.attributes["name"].value)
	
				s = []
				serverList = group.getElementsByTagName('server')
				
				for server in serverList:
					serverName = str(server.attributes['name'].value)
					s.append(serverName)
					
				serverGroups[groupName] = ",".join(s)	
			
		except xml.parsers.expat.ExpatError, e:
			print "ERR: Config file has errors, please investigate\n"
			print "XML ERROR - " + str(e) + "\n"

	else:
		
		# User has asked for a config file based run, but no config file exists
		print "ERR: configuration file (" + configFile + ") not present, -g can not be used"			
	
	return variables, serverGroups	
		
	
def getGroupServers(targetGroup):	
	"""	Look for a config file (xml) in the current directory, and return a list of 
		servers that correspond to the required group name
	"""
	
	# define a list to hold the servers found in the config file for a given group
	s = []
	
	if os.path.exists(configFile):
	
		try:
			# Process the xml config file building a DOM structure to parse
			# check if config file exists, before trying to use it
			#if os.path.exists(
			xmldoc = minidom.parse(configFile)
			
			# From the DOM, create a list of group objects
			groupList = xmldoc.getElementsByTagName('group')
			
			# Process each group stanza
			for group in groupList:
			
				groupName = group.attributes["name"].value 
				if groupName == targetGroup:
					
					# found the right group, time to process the server entries
					serverList = group.getElementsByTagName('server')
					for server in serverList:
						serverName = server.attributes['name'].value
						s.append(serverName)
						#print "Owning Group " + groupName + " - server " + serverName	# DEBUG
					break  
	
		
			
		except xml.parsers.expat.ExpatError, e:
			print "ERR: Config file has errors, please investigate\n"
			print "XML ERROR - " + str(e) + "\n"
			
	else:
		
		# User has asked for a config file based run, but no config file exists
		print "ERR: configuration file (" + configFile + ") not present, -g can not be used"
		
	serverString = ",".join(s)
	
	return serverString	

def worker(connection,nameSpace,hostName):
	""" Process forked by the main process to just perform the data gathering
		Once the data is collected from SNMP the resulting object is passed back
		on the pipe to the main process.
	"""
	
	thisHost = GLUSTERhost(hostName=hostName)
	
	while True:
		try:	

			# Get the system stats for this host
			thisHost.getData()
			
			if thisHost.hostActive:
				
				if nameSpace.interactiveMode:
					
					# Get the filesystem data
					thisHost.getDiskInfo(nameSpace)
					
					if thisHost.hostActive:
						
						# Get the status of the nodes (look for key processes on the node)
						thisHost.getState()
			
			# if snmp fails in any of the above steps the hostActive flag is false, so 
			# change the nodes state and reset it's stats until snmp starts working again
			if not thisHost.hostActive:
				thisHost.state = 'unknown'
				thisHost.reset()
				pass 
			
			dataFeed = thisHost
			connection.send(dataFeed)
			#syslog.syslog("sent data to main process for " + thisHost.hostName)		# DEBUG
			sleep(refreshRate)
			pass 

			
		except KeyboardInterrupt,e:
			break
			
		except:
			break 

	sys.exit(12)

def refreshInfoWindow(win):
	"""	Routine to refresh the contents of the info window based on the aggregated
		metrics held by the cluster object (which is fed by the node and volume objects) """

	timestamps = []
	for node in gCluster.nodes:
		if node.timeStamp is not None:
			timestamps.append(node.timeStamp)
	
	if len(timestamps) > 0:	
		timestamps.sort()
		# Grab the lowest and highest timestamps across the nodes
		minTime = timestamps[0]
		maxTime = timestamps[-1]
		
		delta = maxTime - minTime
		deltaSecs = delta.days*86400+delta.seconds
	
		# Put a ceiling on the max secs of clock skew
		if deltaSecs > 999:
			deltaSecs = 999
	
	else:
		deltaSecs = 0
		
	infoLine1_p1 = "gtop - " + gCluster.version[:11] + " " + \
				str(gCluster.peerCount).rjust(3) + " nodes,"

	infoLine1_p2 = " active" + \
				" CPU%:" + str(gCluster.avgCPU).rjust(3) + " Avg," + \
				str(gCluster.peakCPU).rjust(3) + " peak" + " Skew:" + \
				str(deltaSecs).rjust(3) + "s " + \
				strftime(timeTemplate, gmtime())
				
	infoLine2 =	"Activity: Network:" + convertBytes(gCluster.aggrNetIn).rjust(5) + " in," + \
				convertBytes(gCluster.aggrNetOut).rjust(5) + " out" + \
				"    Disk:" + convertBytes(gCluster.aggrDiskR*BLOCKSIZE).rjust(5) + " reads," + \
				convertBytes(gCluster.aggrDiskW*BLOCKSIZE).rjust(5) + " writes        "
				
	infoLine3 = "Storage:" + str(len(gCluster.volumes)).rjust(2) + " volumes," + \
				str(len(gCluster.brickXref)).rjust(3) + " bricks / " + \
				convertBytes(gCluster.rawCapacity).rjust(5) + " raw," + \
				convertBytes(gCluster.usableCapacity).rjust(5) + " usable," + \
				convertBytes(gCluster.usedCapacity).rjust(5) + " used," + \
				convertBytes(gCluster.freeCapacity).rjust(5) + " free"
				
	win.addstr(0,0,infoLine1_p1)
	
	# If the active node count is not right, highlight the value on screen
	if gCluster.activeNodes != gCluster.peerCount:
		win.addstr(str(gCluster.activeNodes).rjust(3),curses.A_STANDOUT)
	else:
		win.addstr(str(gCluster.activeNodes).rjust(3))
	
	win.addstr(infoLine1_p2)
	
	win.addstr(1,0,infoLine2)
	win.addstr(2,0,infoLine3)
	win.noutrefresh()
	
	return 

def refreshNodePad(pad,dh,vh,cursor,toprow):
	"""	Function to display the node data to the screen """
	
	ypos = 0 
	tgt = cursor + toprow
	for node in gCluster.nodes:
						
		# format this nodes output and display
		nodeData = node.formatData()
		
		if ypos == tgt:
			pad.addstr(ypos,0,nodeData,rowHighlight)
		else:
			pad.addstr(ypos,0,nodeData)
		

		ypos += 1
					
	pad.noutrefresh(toprow,0,vh+5,0,vh+5+dh,80)



def refreshVolumePad(pad,vh,cursor,toprow):
	"""	function to write out the volume data to a given window area on the screen """
	
	ypos = 0
	tgt = cursor + toprow
			
	for volume in gCluster.volumes:

		volData = volume.formatVol()
		if ypos == tgt:
			pad.addstr(ypos,0,volData,rowHighlight)
		else:
			pad.addstr(ypos,0,volData)
																	
		ypos +=1
							
				
	pad.noutrefresh(toprow,0,4,0,vh+2,80)		

	return

def getWindowSizes(screen):
	"""	use the volume and nodes counts to determine the volume and data window sizes """
	
	# get the dimensions of the screen, and adjust height by 3 due to the fixed info area
	screenh,screenw = screen.getmaxyx()
	screenh -=3
	
	numOfNodes = len(gCluster.nodes)
	numOfVolumes = len(gCluster.volumes)
	
	volHeight = VolumeHeight = int(screenh * (VOLUMEAREAPCT/float(100)))
	maxDataHeight = int(screenh * (NODEAREAPCT/float(100)))
	
	
	if numOfNodes < maxDataHeight:
		freeLines = maxDataHeight - numOfNodes
	else:
		freeLines = 0
	
	# Add 1 to number of volumes to account for the headings line
	volLinesNeeded = (numOfVolumes + 1) - VolumeHeight
	if volLinesNeeded > 0 and freeLines > 0:
		for loop in range(freeLines):
			volHeight += 1
			volLinesNeeded -= 1
			if volLinesNeeded < 0:
				break 
	
	dataHeight = screenh - (infoHeight + volHeight)

	return volHeight, dataHeight

def main(gCluster):
	""" Main processing and Contol loop
	"""
	# Point to the mibs directory (Fedora, RHEL6)
	os.environ['MIBDIRS'] = '/usr/share/snmp/mibs'

	# define a flag variable set when exception trapped to provide better diagnostics
	errorType = ""

	# flag used for diagnostics
	dump=False										

	# Setup server process (another pid) for shared objects between processes
	mgr = Manager()
	
	# Create a namespace that parent objects can be attached to for visibility in the child processes
	ns = mgr.Namespace()
	ns.gCluster = gCluster
	ns.interactiveMode = interactiveMode

	for node in gCluster.nodes:
		
		parentCon, childCon = Pipe()
		node.parentCon, node.childCon = parentCon, childCon
		

		p = Process(target=worker, args=(node.childCon,ns,node.hostName))

		p.daemon=True
		p.start()
		
		#syslog.syslog("gtop has forked process" + str(p.pid))		# DEBUG
		gCluster.processList.append(p)
	
		

														
	if interactiveMode:
		# Define a flag to describe the error - debugging only
		errorType = ""
		
		# set locale up, so the unicode block/arrow symbols can be used in the UI
		locale.setlocale(locale.LC_ALL,"")
		
		stdscr = initScreen()
		vh,dh = getWindowSizes(stdscr)

		# used to indicate a row that should be highlighted
		volumeCursor, nodeCursor = 0, 0			


		# define the variables used to toggle the sort sequence within data and volume windows
		sortVolName = True
		sortVolFree = False
		sortVolSize = False
		sortNodeName = True
		sortNodeCPU = False
		sortNodeNetIn = False
		sortNodeNetOut = False
		sortNodeDiskR = False
		sortNodeDiskW = False
		
		infoWindow = curses.newwin(infoHeight,80,0,0)
		volumePad = curses.newpad(MAXVOLS,80)

		nodePad = curses.newpad(MAXNODES,80)

		pVolTop = 0
		pNodeTop = 0
		refreshInfoWindow(infoWindow)


		stdscr.addstr(3,0,"Volume           Bricks   Type   Size   Used   Free   Volume Usage              ",titleHighlight) 
		stdscr.addstr(5,0,"Please wait...",curses.A_BLINK)

		stdscr.addstr(vh+3,0,"                       CPU       Memory %   Daemons     Network     Disk I/O")
		stdscr.addstr(vh+4,0,"S Gluster Node     C/T  %   RAM  Real|Swap C-S-N-H-G   In  | Out  Reads | Writes",titleHighlight)

		stdscr.noutrefresh()			


		# flush the updates to the screen
		curses.doupdate()								
		#exit(0) 	# DEBUG
	else:
		
		# Set "batch mode" counters and write column headers to stdout
		rowNum = 1										
		if showHeaders:									
			triggerRow = printHeader()
		else:
			printHeader(headerType='raw')					

	startTime = int(time.time())
	
	nodeRcvd = []
		
	while True:
		try:
			
			for node in gCluster.nodes:
				
				# Check if there is anything ready from the worker processes associated with each node
				if node.parentCon.poll():
					
					#syslog.syslog("data received on connection for " + node.hostName)
										
					# We have a object passed from subprocess, add this nodes name to a list to 
					# signify it's been seen. if there are slower processes  we could get mutiple 
					# receives from the same host - but we should only count the most recent which is why a 
					# list not counter is used
					if node.hostName not in nodeRcvd:
						nodeRcvd.append(node.hostName)
						
					
					# Grab the workers node object
					updatedNode = node.parentCon.recv()
					
					# Appy the workers node attributes to the local copy of the host object
					node.__dict__.update(updatedNode.__dict__)
					
					# Process the brick information to update the local xlator objects ready for roll-up into volume stats
					for brickName in node.brickInfo:
						xl = gCluster.brick2Xlator[brickName]
						xl.size = node.brickInfo[brickName][0]
						xl.used = node.brickInfo[brickName][1]

			if len(nodeRcvd) == len(gCluster.nodes):
				
				# reset the 'node seen' list
				nodeRcvd = []
				
				# Handle the output - UI or stdout

				if interactiveMode:

					#-----------------------------------------------------------------------------------
					# Update the screen
					#-----------------------------------------------------------------------------------					
					refreshNodePad(nodePad,dh,vh,nodeCursor,pNodeTop)
					
					# process the bricks/volumes to update roll-up stats
					raw = 0
					usable = 0
					used = 0 
					for volume in gCluster.volumes:
						volume.updateVol()
						raw += volume.rawSize
						usable += volume.usableSize
						used += volume.usedSize
					
					refreshVolumePad(volumePad,vh,volumeCursor,pVolTop)
					
					# Set the high level capacity information for the whole cluster	
					gCluster.rawCapacity = raw
					gCluster.usableCapacity = usable
					gCluster.usedCapacity = used
					gCluster.freeCapacity = usable - used
						
					# Update the rollup stats based on current node metrics
					gCluster.updateStats()
					
					# Manage the active node count
					gCluster.updateActive()
		
					refreshInfoWindow(infoWindow)
					
					# flush all screen changes to the physical screen
					curses.doupdate()
					
				else:
					
					#-----------------------------------------------------------------------------------
					# Send output to stdout
					#-----------------------------------------------------------------------------------
					tstamp = strftime(timeTemplate, gmtime())
					if timeStamps:
						prefix = tstamp
					else:
						prefix = "" 
					
					if BGMODE in ['summary','all']:
						gCluster.updateStats()
						displayStats = gCluster.formatStats(prefix)
						print displayStats
						if showHeaders:
							rowNum += 1
						
					if BGMODE in ['nodes','all']:
						
						for node in gCluster.nodes:
							
							displayStats = node.formatData(prefix)
							print displayStats
							if showHeaders:
								rowNum += 1
							
					if showHeaders:							# if headers are needed then 
						if rowNum > triggerRow:
							triggerRow = printHeader()
							rowNum = 1			

				pass 
			
								
			# In between sample refreshes allow the user to sort the node and volume data
			if interactiveMode:
				keypress = stdscr.getch()

				# check for user selecting q for quit
				if keypress in [ord('q'),ord('Q')]:
					break 

				# DOWN arrow Pressed
				elif keypress == 258:
					# check if I'm at the bottom of the list already?
					if (volumeCursor + pVolTop) < (len(gCluster.volumes) -1):
						
						# If at the bottom of the volume area, change the pad 
						# ofset
						if volumeCursor == (vh -2):
							pVolTop += 1
						else:
							# just move the highlighted row
							volumeCursor +=1
							
						refreshVolumePad(volumePad,vh,volumeCursor,pVolTop)
						volumePad.refresh(pVolTop,0,4,0,vh,80)
						
				# UP arrow Pressed		
				elif keypress == 259:
					
					if (volumeCursor + pVolTop) > 0:
						if volumeCursor == 0:
							pVolTop -=1
						else:
							volumeCursor -= 1
							
						refreshVolumePad(volumePad,vh,volumeCursor,pVolTop)
						volumePad.refresh(pVolTop,0,4,0,vh,80)						

				# '+' pressed
				elif keypress == 43:
					if (nodeCursor + pNodeTop) < (len(gCluster.nodes) -1):
						if nodeCursor == dh :
							pNodeTop += 1
						else:
							
							nodeCursor +=1
							
						refreshNodePad(nodePad,dh,vh,nodeCursor,pNodeTop)
						nodePad.refresh(pNodeTop,0,vh+5,0,vh+5+dh,80)

				# '-' pressed - CHANGES		
				elif keypress == 45:
					if (nodeCursor + pNodeTop) > 0:
						if nodeCursor == 0:
							pNodeTop -= 1
						else:
							nodeCursor -= 1
							
						refreshNodePad(nodePad,dh,vh,nodeCursor,pNodeTop)
						nodePad.refresh(pNodeTop,0,vh+5,0,vh+5+dh,80)			

	
				elif keypress in [ord('v'),ord('V')]:
					sortVolName = not sortVolName
					if sortVolName:
						gCluster.volumes.sort(key=lambda volume: volume.name)
					else:
						gCluster.volumes.sort(key=lambda volume: volume.name,reverse=True)
						
					volumeCursor = 0							# reset highlight
					pVolTop = 0									# reset the pad offset 
					refreshVolumePad(volumePad,vh,volumeCursor,pVolTop)
					volumePad.refresh(pVolTop,0,4,0,vh,80)
					
				elif keypress in [ord('s'),ord('S')]:
					sortVolSize = not sortVolSize
					if sortVolSize:
						gCluster.volumes.sort(key=lambda volume: volume.usableSize)
					else:
						gCluster.volumes.sort(key=lambda volume: volume.usableSize,reverse=True)
						
					volumeCursor = 0							# reset highlight
					pVolTop = 0
					refreshVolumePad(volumePad,vh,volumeCursor,pVolTop)
					volumePad.refresh(pVolTop,0,4,0,vh,80)

				elif keypress in [ord('f'),ord('F')]:
					sortVolFree = not sortVolFree
					if sortVolFree:
						gCluster.volumes.sort(key=lambda volume: volume.freeSpace)
					else:
						gCluster.volumes.sort(key=lambda volume: volume.freeSpace,reverse=True)
						
					volumeCursor = 0							# reset highlight etc
					pVolTop = 0
					refreshVolumePad(volumePad,vh,volumeCursor,pVolTop)
					volumePad.refresh(pVolTop,0,4,0,vh,80)					
					
				elif keypress in [ord('n'),ord('N')]:
					sortNodeName = not sortNodeName
					if sortNodeName:
						gCluster.nodes.sort(key=lambda node: node.hostName)
					else:
						gCluster.nodes.sort(key=lambda node: node.hostName,reverse=True)
					
					pNodeTop = 0
					nodeCursor = 0
					
					refreshNodePad(nodePad,dh,vh,nodeCursor,pNodeTop)
					nodePad.refresh(0,0,vh+5,0,vh+5+dh,80)
						
				elif keypress in [ord('c'),ord('C')]:
					sortNodeCPU = not sortNodeCPU
					if sortNodeCPU:
						gCluster.nodes.sort(key=lambda node: node.cpuBusyPct)
					else:
						gCluster.nodes.sort(key=lambda node: node.cpuBusyPct,reverse=True)
						
					pNodeTop = 0
					nodeCursor = 0					
					
					refreshNodePad(nodePad,dh,vh,nodeCursor,pNodeTop)
					nodePad.refresh(0,0,vh+5,0,vh+5+dh,80)
					
				elif keypress in [ord('i'),ord('I')]:
					sortNodeNetIn = not sortNodeNetIn
					if sortNodeNetIn:
						gCluster.nodes.sort(key=lambda node: node.netInRate)
					else:
						gCluster.nodes.sort(key=lambda node: node.netInRate,reverse=True)
						
					pNodeTop = 0
					nodeCursor = 0
					
					refreshNodePad(nodePad,dh,vh,nodeCursor,pNodeTop)
					nodePad.refresh(0,0,vh+5,0,vh+5+dh,80)
										
				elif keypress in [ord('o'),ord('O')]:
					sortNodeNetOut = not sortNodeNetOut
					if sortNodeNetOut:
						gCluster.nodes.sort(key=lambda node: node.netOutRate)
					else:
						gCluster.nodes.sort(key=lambda node: node.netOutRate,reverse=True)
						
					pNodeTop = 0
					nodeCursor = 0
					
					refreshNodePad(nodePad,dh,vh,nodeCursor,pNodeTop)
					nodePad.refresh(0,0,vh+5,0,vh+5+dh,80)

				elif keypress in [ord('r'),ord('R')]:
					sortNodeDiskR = not sortNodeDiskR
					if sortNodeDiskR:
						gCluster.nodes.sort(key=lambda node: node.blocksReadAvg)
					else:
						gCluster.nodes.sort(key=lambda node: node.blocksReadAvg,reverse=True)
						
					pNodeTop = 0
					nodeCursor = 0					
					
					refreshNodePad(nodePad,dh,vh,nodeCursor,pNodeTop)
					nodePad.refresh(0,0,vh+5,0,vh+5+dh,80)
										
				elif keypress in [ord('w'),ord('W')]:
					sortNodeDiskW = not sortNodeDiskW
					if sortNodeDiskW:
						gCluster.nodes.sort(key=lambda node: node.blocksWriteAvg)
					else:
						gCluster.nodes.sort(key=lambda node: node.blocksWriteAvg,reverse=True)
						
					pNodeTop = 0
					nodeCursor = 0
					
					refreshNodePad(nodePad,dh,vh,nodeCursor,pNodeTop)
					nodePad.refresh(0,0,vh+5,0,vh+5+dh,80)
										

				elif keypress == curses.KEY_RESIZE:
					# user has attempted to resize the window, which is not supported (yet!)
					# so just tell them they're naughty and exit ;o)
					errorType = "resize"
					break 
					
				elif keypress in [ord('d'),ord('D')]:
					errorType="dump"
					break
				
			sleep(0.1)									# Pause for a 1/10 second
														
		except KeyboardInterrupt:						# Catch CTRL-C from the user to leave the program
			break
			
		except curses.error, e:							# Catch UI problems

			errorType = "curses"
			break  
			
		except Exception, e:							# DEBUG - Something bad happened, so dump the contents of the 
			errorType = "unknown"
			dump = True	
			break 

	if interactiveMode:
		del nodePad
		del infoWindow
		del volumePad
		resetScreen(stdscr)

	
	# Clear up the forked processes
	for p in gCluster.processList:
		#print "killing " + str(p.pid)									# DEBUG
		p.terminate()
		p.join()


	if 	errorType == "resize":
		print "ERR: Resizing the window is not currently supported"
	
	if errorType == "dump":
		print "node area " + str(dh)
		print "volume area " + str(vh)
		print "voltop is " + str(pVolTop)
		print "volume cursor is " + str(volumeCursor)
		print "node top is " + str(pNodeTop)
		print "node cursor is " + str(nodeCursor)
		gCluster.dump()
	
	elif errorType == "curses":											# DEBUG ONLY
		print "ERR: Exception in screen handling (curses). Program needs a window of 80x24"

		print "ERR: Message - ",e
		#print ', '.join([type(e).__name__, os.path.basename(top[0]), str(top[1])])
		
		print "vol window size is " + str(vh)
		print "data window size is " + str(dh)

		
	elif errorType == "unknown":
		print "ERR: Problem occurred - dump of cluster and volume objects follow"
		print e
		gCluster.dump()


	return

# ---------------------------------------------------------------------------------------------------------------------------------

if __name__ == "__main__":
	
	usageInfo = "%prog [options] argument \n\n" + \
				"This program uses snmp to gather and present various operational metrics\n" + \
				"from gluster nodes to provide a single view of a cluster, that refreshes\n" + \
				"every 5 seconds." 

	bgModeOptions = ['nodes', 'all', 'summary']
	dataFormatOptions = ['raw','readable']

	parser = OptionParser(usage=usageInfo,version="%prog 1.0.0")
	parser.add_option("-n","--no-heading",dest="showHeaders",action="store_false",default=True,help="suppress headings")
	parser.add_option("-s","--servers",dest="serverList",default=[],type="string",help="Comma separated list of names/IP (default uses gluster's peers file)")
	parser.add_option("-b","--bg-mode",dest="bgMode",default="nodes",type="string",help="Which data to display in 'batch' mode " + str(bgModeOptions) + ", (default is nodes)")
	parser.add_option("-f","--format",dest="dataFormat",default="readable",type="string",help="Output type raw or readable(default)")
	parser.add_option("-g","--server-group",dest="groupName",default="",type="string",help="Name of a server group define in the users XML config file)")

	(options, args) = parser.parse_args()
	
	# check for mutually exclusive options
	if options.serverList and options.groupName:
		print "-s and -g options are mutually exclusive, use either not both"
		exit(4)
		
	# if user provides a server or group list, check the background mode is OK to use	
	if options.serverList or options.groupName:
		if options.bgMode and options.bgMode in bgModeOptions:
			BGMODE = options.bgMode
		else:
			print "invalid option supplied on -b option. Valid options are " + str(bgModeOptions)
			exit(4)
		
		if options.dataFormat and options.dataFormat in dataFormatOptions:
			
			# Need to check if option is raw, if so then headers aren't needed, but an initial
			# csv based header row is - no pagination
			if options.dataFormat == "raw":
				options.showHeaders = False
				
			FORMAT = options.dataFormat
			
		else:
			print "Invalid output option specified. Valid options are - " + str(dataFormatOptions)
			exit(4)

	whiteList = ['eth','wlan','em','ib']						# wlan for testing ONLY!
	whiteList = r'|'.join([name + "*" for name in whiteList])
	baseInstall = '/var/lib/glusterd'
	
	SNMPCOMMUNITY = 'gluster'
	
	# Unicode solid block character
	block=u'\u2588'
	
	# Size of maximum bar for a volume at 100% full
	barWidth = 20									
	pctPerBlock = 100 / barWidth 	
	
	timeTemplate = 	'%H:%M:%S'
	
	# define the symbols used to describe node state


	# Could use os.environ['TERM'] - linux = console, xterm is GUI
	consoleIsTTY = os.environ['TERM'] == 'linux'
	if consoleIsTTY:
		titleHighlight=curses.A_REVERSE
		rowHighlight = curses.A_UNDERLINE
		nodeStatus = { 	'connected' : u'\u00BB',				# double arrow right
					'disconnected' : u'\u2219',					# solid circle
					'unknown' : u'\u003F'}						# Question Mark
	else:
		titleHighlight=curses.A_UNDERLINE
		rowHighlight = curses.A_BOLD
		nodeStatus = { 	'connected' : u'\u25B2',					# UP
						'disconnected' : u'\u25BC',					# DOWN
						'unknown' : u'\u003F'}						# Question Mark
	


	# Not all variations are listed...since not all variations are supported!
	volTypeShort = { 'Distributed-Replicated' : 'D-R',
					'Striped' : ' S ',
					'Distributed-Striped' : 'D-S',
					'Replicated' : ' R ',
					'Distributed' : ' D '}			

	# define a dict that uses a group name to hold a comma separated list of servers
	variables = []
	serverGroups = {}
	
	# configFile defines groups of servers that can be used 
	configFile = os.path.expanduser('~/gtoprc.xml')
	
	# define the number of rows in the info window (UI only)
	infoHeight = 3

	showHeaders = options.showHeaders
	BLOCKSIZE = 512
	
	# Use these ratios to determine the screen window proportions based on current screen height
	VOLUMEAREAPCT = 25   	# 20% of screen is volume data
	NODEAREAPCT   = 75		# 66% is for node data by default
	
	# Maximums used to define the virtual size of the volume and node display areas
	MAXVOLS  = 64
	MAXNODES = 64
	
	# Set refresh interval to align with SNMP agent refresh interval of 5 seconds
	refreshRate = 5								
	
	volDir = os.path.join(baseInstall,'vols')
	peersDir = os.path.join(baseInstall,'peers')

	# create a cluster object - this becomes the root object, linking cluster to volumes and volumes to bricks 
	gCluster = Cluster()						
												
	print "\ngtop starting"
	
	variables, serverGroup = processConfigFile(configFile)
	
	# Apply any overrides from the users config file
	if variables:
		print "Applying overrides from configuration file"
		for assignment in variables:
			print "\t" + assignment
			exec assignment
	
		
	
	# Check if user has supplied an override for the servers to monitor
	if options.serverList or options.groupName:			
												
		screenY,screenX = screenSize()
		interactiveMode = False
		timeStamps = True

		# if a group name has been given, build the server list from the config file
		if options.groupName:
			serverList = getGroupServers(options.groupName)
			
			# if the server list is empty flag to the user, no match on group
			if not serverList:
				print "ERR: Config file does not have servers associated with group '" + options.groupName + "'"
				
		else:
			serverList = options.serverList
		
		# If we have servers to process from the user, validate them (IP, DNS checks)	
		if serverList:
			print "Checking supplied server list is usable.."
			gCluster.validateServers(serverList)
		
	else:
		
		print "Checking for glusterfs peers file"
		
		# Populate cluster hosts from peers file
		gCluster.getGlusterPeers()				
		
		# If we have nodes - then program is running on a node so enable all the local gathering
		if gCluster.nodes:						
			interactiveMode = True
			screenY,screenX = screenSize()
			if screenY <= 9:
				print "ERR: console/xterm needs to be > 9 rows in size"
				exit(8)
				
			# Build a volume list based on the hosts vol file(s)
			gCluster.getGlusterVols()
			gCluster.volumes.sort(key=lambda volume: volume.name)		
			
			# Grab the glusterfs version from the running host
			gCluster.getVersion()				
		else:
			print "ERR: No gluster configuration present at " + peersDir
			pass 



	if gCluster.nodes:
		
		gCluster.nodes.sort(key=lambda node: node.hostName)		# sort the list of hosts, by host name
		
		print "Checking SNMP is available on the selected hosts.."
		
		# Check SNMP is responding on each host before we try and use them
		gCluster.SNMPcheck()					
		
		# If there are still nodes after all the checks they're OK to use
		if gCluster.nodes:						
		
		
			# Call the main processing loop
			main(gCluster)						

												
	else:										
		# no valid servers to talk to or left after validations...better tell the user!
	
		print "ERR: Unable to determine the hosts to scan. For this program to work you have"
		print "the following options"
		print "* use -s server1,server2"
		print "* use -g groupname (groups define in an XML config file in current directory)"
		print "* run on a gluster node\n"

	
	print "Program terminated."
	


	
