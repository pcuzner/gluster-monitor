gluster-monitor
===============

Repo hosting an SNMP based CLI tool for monitoring capacity and node performance of a gluster cluster

  +-------------------------------------------------------------------------------------+
  |gtop - 3.3.0.5rhs   2 nodes,  2 active  CPU%:  2 Avg,  2 peak         10:01:17       |
  |Activity - Network:  18K in,  18K out    Disk:  94K reads,   6K writes               |
  |Storage - 9 volumes, 18 bricks /   11G raw,   8G usable, 560M used,   8G free        |
  |Volume           Bricks   Type   Size   Used   Free   Volume Usage                   |
  |ctdb                 2     R     491M    25M    466M  █ 5%                           |
  |ftp                  2     R     991M    33M    959M  █ 3%                           |
  |repl                 2     R    1015M   195M    820M  ███ 19%                        |
  |temp1                2     D    1007M    51M    955M  █ 5%                           |
  |temp2                2     D    1007M    51M    955M  █ 5%                           |
  |temp3                2     D    1007M    51M    955M  █ 5%                           |
  |temp4                2     D    1007M    51M    955M  █ 5%                           |
  |temp5                2     D    1007M    51M    955M  █ 5%                           |
  |temp6                2     D    1007M    51M    955M  █ 5%                           |
  |                                                                                     |
  |                        CPU   Memory %        Network AVG   Disk I/O AVG             |
  |S Gluster Node     C/T   %    RAM   Real|Swap     In  | Out    Reads | Writes        |
  |▲ rhs5-1            2     2  491M    92   31       10K     9K    94K      0b         |
  |▲ rhs5-2            2     2  491M    93   31        8K     9K     0b	     6K         |
  |                                                                                     |
  +-------------------------------------------------------------------------------------+



Background
----------
gtop is a python program written to provide a means of monitoring the high level activity 
within a gluster cluster. Gluster itself does not currently expose performance 
metrics so in order to provide a meaningful indication of workload the node's system statistics
are gathered over SNMP and aggregated.

The downside to this approach is 
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
sessions. As a result of this approach, when gtop starts you wil see the following types of processes active;
- parent process started by the user
- subprocess handling memory sharing between parent and child processes
- n x subprocess for node gathering. 

The side effect for this approach is better scalability and more effective use of multiple cores.


Installation
------------
The following packages need to be installed on each of the gluster nodes
net-snmp
net-snmp-utils
net-snmp-python

The snmpd daemon needs to be started at boot, and have a known community string defined 
to allow gtop to poll for data. An example of a working snmpd.conf is provided.

It is recommended that gtop is installed on all gluster nodes

Usage
-----
gtop uses the optionparser module to enable the tool to run in two modes
1. UI    : This is the default, when run on a gluster node.
2. BATCH : by starting gtop with a -s or -g flag, the program will only gather 
           system stats and write them for stdout. 

In both of these modes, the program first looks for a configuration file in the users
home directory - gtoprc.xml. This file provides overrides for some gtop settings - 
snmp community string, disk block size, and also allows server groups to be defined 
to simplify invocation for batch mode. An example of the config file is provided.

UI.
The UI uses the ncurses environment to handle the screen display, and provides a console 
that is split into 3 main areas;

- Cluster Info : The top section of the display shows, cluster wide metrics such as
                 node information, cpu average/peak, and total network/disk bandwidth
- Volume Info  : The middle section's focus is on the volumes provided by gluster. Each
                 volume entry shows attributes such as # bricks, Usable Size, freespace
                 and includes a simple bar chart illustrating % utilised.
- Node Info    : The bottom of the screen provides the system metrics for each node, and some
                 indication as to the physical configuration. 
                 Each node entry shows; cores/threads, RAM, followed by utilisation metrics covering
                 CPU Busy, Memory, network and disk

Once launched the volume and Node areas support sorting (forward and reverse), based on specific keys;

Volume Area
F/f : Freespace
V/v : volume name
U/u : UsableSize

Node Area
N/n : node name
C/c : CPU busy
I/i : Network in average
O/o : Network out average
R/r : Disk Read 
W/w : Disk Write

To quit the UI, 'q' or CTRL-C is supported.

BATCH.
Running in batch mode can provide a remote view of the cluster, or potentially allow multiple clusters
to be grouped and displayed together when using the server group definitions in the configuration file.

gtop in batch mode simply writes the node statistics only to stdout, and so could be redirected to a file
to collect system stats for later analysis in a spreadsheet, or processing with gnuplot, or matplotlib.

To quit batch mode, use CTRL-C.

A User Guide is also provided in Libreoffice (.odt) format.

Feedback
--------
Comments and contributions to the code are welcome and encouraged. 

