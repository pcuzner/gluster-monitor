#H1 gluster-monitor - gtop


This repo hosts an SNMP based CLI tool for monitoring capacity and node performance of a glusterfs cluster

#H2 Background

gtop is a python program written to provide a means of monitoring the high level activity 
within a gluster cluster.

Gluster itself does not currently expose performance metrics so in order to provide a meaningful 
indication of workload, the node's system statistics are gathered over SNMP and aggregated. The data is presented in thre areas in the UI  
* *Cluster Area* - aggregated view of total load
* *Volume Area* - capacity view for the cluster
* *Node Area* - node performance metrics averaged over a 5 seconds sample interval  

The image below shows how the UI looks, and also follows a workflow to illustrate the way that node states can transition depending upon glusterd and snmp availability.

![alt text](https://github.com/pcuzner/gluster-monitor/blob/master/gtop-example.gif "gtop UI")  



#H2 Installation

The following packages need to be installed on each of the gluster nodes  

* net-snmp
* net-snmp-utils
* net-snmp-python  

The snmpd daemon needs to be started at boot, and have a known community string defined 
to allow gtop to poll for data. An example of a working snmpd.conf is provided in the repo.

It is recommended that gtop is installed on all gluster nodes. The gtop script requires the following scripts to be found in the user's PATH  
- gtop.py
- gtop_utils.py
- gtop_iputils.py

Optionally a configuration file can be placed in the users home directory called gtoprc.xml. An example of this file is provided in the repo, and can be useful when applying overrides to the script.  

#H2 Usage

*gtop* uses the optionparser module to enable the tool to run in two modes  

1. UI    : This is the default, when run on a gluster node.  
2. BATCH : by starting gtop with a -s or -g flag, the program will only gather system stats and write them for stdout.  

The options available on the command can be shown by invoking >gtop -h  
```
[root@rhs5-1 ~]# gtop -h
Usage: gtop [options] argument 

This program uses snmp to gather and present various operational metrics
from gluster nodes to provide a single view of a cluster, that refreshes
every 5 seconds.

Options:
  --version             show program's version number and exit
  -h, --help            show this help message and exit
  -n, --no-heading      suppress headings
  -s SERVERLIST, --servers=SERVERLIST
                        Comma separated list of names/IP (default uses
                        gluster's peers file)
  -g GROUPNAME, --server-group=GROUPNAME
                        Name of a server group define in the users XML config
                        file)
```


In either mode, the program first looks for a configuration file in the users
home directory - gtoprc.xml. This file provides overrides for some gtop settings - 
snmp community string, disk block size, and also allows server groups to be defined 
to simplify invocation for batch mode. 



#H3 UI Mode
The UI uses the ncurses environment to handle the screen display, and provides a console 
that is split into 3 main areas;

- Cluster Info : The top section of the display shows, cluster wide metrics such as
node information, cpu average/peak, and total network/disk bandwidth. Added in 0.99, the cluster area also
shows a time skew field. This indicates the max time difference across the nodes in the cluster. Normally this 
value is <= the sample interval (5s) - but if ntpd is not configured larger skews can be seen. The reason for
introducing this feature is to help identify geo-replication issues - since geo-replication requires a common time 
source for all nodes/bricks to work correctly.  
- Volume Info  : The middle section's focus is on the volumes provided by gluster. Each
volume entry shows attributes such as # bricks, Usable Size, freespace
and includes a simple bar chart illustrating % utilised.
- Node Info    : The bottom of the screen provides the system metrics for each node, and some
indication as to the physical configuration. Each node entry shows; cores/threads, RAM,
 followed by utilisation metrics covering CPU Busy, Memory, network and disk and includes daemon 
 monitoring flags for CTDB, Samba, NFS, Self-Heal and Geo-Replication

Once launched the volume and Node areas support sorting (forward and reverse), based on specific keys;

*Volume Area*  
F/f : Freespace
V/v : volume name
U/u : UsableSize  

*Node Area*  
N/n : node name
C/c : CPU busy
I/i : Network in average
O/o : Network out average
R/r : Disk Read 
W/w : Disk Write  

In addition to sorting, the node and volume areas are scrollable to cater for large cluster environments. The node area
is scrolled using the + or - keys, and the volume area uses the up/down arrow keys.


To quit the UI, use 'q' or CTRL-C.


#H3 BATCH Mode  

Running in batch mode can provide a remote view of the cluster, or potentially allow multiple clusters
to be grouped and displayed together when using the server group definitions in the configuration file.

gtop in batch mode simply writes the node statistics only to stdout, and so could be redirected to a file
to collect system stats for later analysis in a spreadsheet, or processing with gnuplot, or matplotlib.

To quit batch mode, use CTRL-C.

A User Guide is also provided in Libreoffice (.odt) format.

#H2 Known Issues  

The program's design makes the following compromises;
 
1. The system stats are refreshed by the snmp agent that is hard set to 5 second
   sample intervals. This limits the granularity of gtop for problem determination and
   correlation with client side metrics.  
   
2. snmpd needs to be running on each of the gluster nodes. Under load (80+% CPU), 
   the snmpd daemon can fail to respond in a timely manner for gtop. In these circumstances
   gtop resets the stats for the node and marks the node as unknown for that sample run. Nodes 
   marked as not responding continue to be polled, so as and when they 'recover' data is 
   made available in the interface.
   
It's also worth noting that the netsnmp bindings for python are synchronous, which can block
the data gathering process. To address this, gtop uses the multiprocessing module, placing the snmp interation
in separate processes - this way a delay on one node's sample does not impact the other snmp gathering
sessions. 

As a result of this approach, when gtop starts you wil see the following types of processes active;
- parent process started by the user
- subprocess handling memory sharing between parent and child processes
- n x subprocesses for node gathering. 

The side effect for this approach is better scalability and more effective use of multiple cores.



Feedback
--------
Comments and contributions to the code are welcome and encouraged. 


Author
paul dot cuzner at gmail dot com
