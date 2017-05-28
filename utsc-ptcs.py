#!/usr/bin/python
# UTSC | PTCS 
# University of Toronto Scarborough | Python Telescope Control System
#
# Copyright (c) 2014 Eric Dapp, Caden Armstrong, Hanno Rein
#
# UTSC | PCTS is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# UTSC | PCTS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with UTSC | PCTS.  If not, see <http://www.gnu.org/licenses/>.
#
import serial
#from PIL import ImageTk, Image
import os
import curses
import socket
import struct
import time
import datetime
import sys
import signal
import client
import threading
from conversions import *
relaymap = [5,3,11,7,13,15,19]
try:
    import RPi.GPIO as GPIO; 
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BOARD); 
    for n,pin in enumerate(relaymap):
        GPIO.setup(pin, GPIO.OUT)
        if n<4: # only turn off dome, not other equipment
            GPIO.output(pin, 1)
except:
    print("cannot access GPIO ports")

with open('apikey.txt', 'r') as content_file:
    apikey = content_file.read().strip()

from curses import wrapper

def updateDomeStatus():
    dome = "---"
    if not GPIO.input(relaymap[0]):
        dome = "<<<"
    elif not GPIO.input(relaymap[1]):
        dome = ">>>"
    elif not GPIO.input(relaymap[2]):
        dome = "^^^"
    elif not GPIO.input(relaymap[3]):
        dome = "vvv"

    dome = "Movement ("+dome+")  "

    if not GPIO.input(relaymap[4]):
        dome += " Light (on)   "
    else:
        dome += " Light (off)  "
    if not GPIO.input(relaymap[5]):
        dome += " Telescope (on)   "
    else:
        dome += " Telescope (off)  "
    if not GPIO.input(relaymap[6]):
        dome += " Camera (on)   "
    else:
        dome += " Camera (off)  "
    statusUpdate("Dome",dome)

def statusUpdate(k, value):
    ncurses_lock.acquire()
    for index, key in enumerate(statusitems):      
        if key == k:
            statuswin.move(1+index, 5+statustitlelen);   
            statuswin.clrtoeol(); 
            statuswin.addstr(1+index, 5+statustitlelen, value.replace('\n', ' '))           
    statuswin.border(0)
    statuswin.addstr(0, 1, " Status ")                    
    statuswin.refresh()
    ncurses_lock.release()

messagesN = 5
messagesi = 1
messages = []
messageswin = None
def showMessage(value):
    global messagesi
    atcl_asynch = value.split(chr(0x9F))
    if len(atcl_asynch)>1:
        for a in atcl_asynch:
            if len(a)>0:
                showMessage(a.replace('\n',' '))
        return
    else:
        value = atcl_asynch[0]
    if len(value)>2:
        if value[0:2]=="O7":
            return
    global messageswin
    ncurses_lock.acquire()
    if len(messages) >= messagesN:
        messages.pop(0)
        messagesi +=1 
    messages.append(value)
    for index, key in enumerate(messages):      
        messageswin.move(1+index, 2);   
        messageswin.clrtoeol(); 
        messageswin.addstr(1+index, 2, "%4d : %s" % (messagesi+index,key))           
    messageswin.refresh()
    ncurses_lock.release()
    
telescope_port = None    
telescope_states= [
    ['Alignment state',              '!AGas;'],  
    ['Alignment side',               '!AGai;'],
    ['Current right ascension',      '!CGra;'],
    ['Current declination',          '!CGde;'],
    ['Target right ascension',       '!CGtr;'],
    ['Target declination',           '!CGtd;']
]

stop_threads = False
ncurses_lock = threading.Lock()
telescope_lock = threading.Lock()
def telescope_communication():
    global telescope_port
    while stop_threads==False:
        if telescope_port is not None:
            ra, dec = None, None
            telescope_lock.acquire()
            data = telescope_port.read(2048) # empty buffer
            if len(data)>0:
                showMessage(data)
            for (index,element) in enumerate(telescope_states):
                key, command = element
                ret = "NNN"
                telescope_port.write(command) 
                time.sleep(0.05)
                ret = telescope_port.read(1024).strip() 
                atcl_asynch = ret.split(chr(0x9F))
                if len(atcl_asynch)>1:
                    ret = atcl_asynch[0]
                if len(ret)>0:
                    if ret[0] == chr(0x8F):
                        ret = "ATCL_ACK"
                    elif ret[0] == chr(0xA5):
                        ret = "ATCL_NACK"
                    else:
                        if ret[-1] == ";":
                            ret = ret[:-1]
                        try:
                            if command == '!CGra;':
                                ra = ra_str2raw(ret)
                            if command == '!CGde;':
                                dec = dec_str2raw(ret)
                        except:
                            ra, dec = None, None
                else:
                    ret = "N/A"
                
                
                if "Internal error" in ret:
                    print(ret)
                    ret = "N/A"
                statusUpdate(key, ret)
            telescope_lock.release()
            if ra is not None and dec is not None:
                if stellarium_socket is not None:
                    if stellarium_conn is not None:
                        data = struct.pack('<hhQIii',24,0,int(round(time.time() * 1000)), ra, dec, 0)
                        stellarium_conn.send(data)
        time.sleep(3)

    return


alignment_mode = "goto"
stellarium_socket = None
stellarium_conn = None
def stellarium_communication():
    global stellarium_socket
    global stellarium_conn
    global alignment_mode
    while stop_threads==False:
        # Poll socket for Stellarium
        if stellarium_socket is not None:
            if stellarium_conn is None:
                try:
                    stellarium_conn, addr = stellarium_socket.accept()
                    stellarium_conn.settimeout(0)
                    #socket.setblocking(0)
                    statusUpdate("Stellarium", "Connection established from %s:%d."% addr)
                except socket.error as e:
                    pass
            else:
                time.sleep(0.01)
                data = ""
                try:
                    data = stellarium_conn.recv(1024)
                except:
                    pass
                if len(data)==20:   # goto command
                    data = struct.unpack('<hhQIi',data)
                    ra_string, dec_string = ra_raw2str(data[-2]), dec_raw2str(data[-1])
                    statusUpdate("Stellarium", "Received from stellarium: %s %s" % (ra_string,dec_string))
                    if dec_string[-2:]=="60":
                        dec_string = dec_string[:-2]+"59"
                    telescope_lock.acquire()
                    time.sleep(0.01)
                    telescope_port.write('!CStr' + ra_string + ';')
                    time.sleep(0.01)
                    telescope_port.write('!CStd' + dec_string + ';')
                    time.sleep(0.01)
                    if alignment_mode=="align":
                        telescope_port.write('!AFrn;')
                        alignment_mode = "goto"
                        statusUpdate("Alignment mode", "GoTo next coordinates.")
                    elif alignment_mode=="goto": 
                        telescope_port.write('!GTrd;')
                    time.sleep(0.1)
                    data = telescope_port.read(1024) # empty buffer
                    if len(data)>0:
                        showMessage(data)
                    telescope_lock.release()
                elif len(data)==0:
                    pass
                else:
                    statusUpdate("Stellarium","Unknown command received of length %d."%len(data))
        else:
            stellarium_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            stellarium_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            port = 10001
            try:
                stellarium_socket.settimeout(0)
                stellarium_socket.bind(("127.0.0.1", port))
                stellarium_socket.listen(1)
                statusUpdate("Stellarium", "Server waiting for connection on port %d."%port)
            except socket.error as e:
                statusUpdate("Stellarium", "Socket error (%s)"%e.strerror)
                stellarium_socket = None
                time.sleep(5)
        time.sleep(0.1)


def finish():
    print("Finishing...")
    global stop_threads
    stop_threads = True
    if stellarium_socket is not None:
        if stellarium_conn is not None:
            stellarium_conn.close()
            try:
                stellarium_socket.shutdown(socket.SHUT_RD)
            except:
                pass
        stellarium_socket.close()
    exit(1)
    return
    
def start_manual_alignment_e():
    statusUpdate("Alignment mode", "Align to next coordinates (East)")
    global alignment_mode
    alignment_mode = "align"
    telescope_lock.acquire()
    telescope_port.write('!ASasEast;')
    telescope_lock.release()
def start_manual_alignment_w():
    statusUpdate("Alignment mode", "Align to next coordinates (West)")
    global alignment_mode
    alignment_mode = "align"
    telescope_lock.acquire()
    telescope_port.write('!ASasWest;')
    telescope_lock.release()

menuwin = None

def main(stdscr):
    stdscr.clear()
    stdscr = curses.initscr()
    curses.noecho()
    curses.cbreak()
    stdscr.nodelay(True)
    stdscr.keypad(True)

    stdscr.addstr(1, 2, "UTSC | PTCS", curses.A_BOLD)
    stdscr.addstr(2, 2, "University of Toronto Scarborough | Python Telescope Control System", curses.A_BOLD)
    stdscr.refresh()

    menuitems = [
            "e/w/g              - Toggle between manual align (East/West) and GoTo",
            "Left/Right/Up/Down - Control dome",
            "1/2/3              - Control peripherals (light/telescope/camera) ",
            "q                  - Exit",
            ]
    global menuwin
    menuwin = curses.newwin(len(menuitems)+2,curses.COLS-3,4,2)                                  
    menuwin.border(0)
    menuwin.addstr(0, 1, " Menu ")                    
    for index, item in enumerate(menuitems):                        
        msg = '%s' % (item)                            
        menuwin.addstr(1+index, 2, msg)                    
    menuwin.refresh()

   
    global statusitems
    statusitems = [
            'Time',
            'Telescope', 
            'Stellarium', 
            'Alignment mode', 
            'Dome', 
    ] + [k for k,c in telescope_states]
    global statuswin
    statuswin = curses.newwin(len(statusitems)+2,curses.COLS-3,menuwin.getbegyx()[0]+menuwin.getmaxyx()[0],2)     
    statuswin.border(0)
    statuswin.addstr(0, 1, " Status ")                    
    global statustitlelen
    statustitlelen = max([len(k) for k in statusitems])
    for index, key in enumerate(statusitems):      
        statuswin.addstr(1+index, 2, ("%%-%ds: "%(statustitlelen+1)) % key)           
    updateDomeStatus()                    
    statusUpdate("Alignment mode", "GoTo next coordinates.")
   
    global messageswin
    messageswin = curses.newwin(messagesN+2,curses.COLS-3,statuswin.getbegyx()[0]+statuswin.getmaxyx()[0],2)     
    messageswin.border(0)
    messageswin.addstr(0, 1, " Telescope messages ")                    
    messageswin.refresh()

    # Open Telescope Port
    global telescope_port
    if os.uname()[0]=="Darwin":
        port_name = '/dev/tty.usbserial'
        #port_name = '/dev/ttyAMA0'
    else:
        port_name = '/dev/ttyS0'
    try:
        telescope_port = serial.Serial(port_name, 19200, timeout = 0.01) 
        statusUpdate('Telescope', "Opened "+port_name)                    
    except:
        statusUpdate('Telescope', "Unable to open port at "+port_name)                    


    telescope_thread = threading.Thread(target=telescope_communication)
    telescope_thread.start()
    stellarium_thread = threading.Thread(target=stellarium_communication)
    stellarium_thread.start()

    lastkey = None
    while True:
        c = stdscr.getch()
        if lastkey is not None:
            td = datetime.datetime.now() - lastkey
            if td.microseconds > 400000 or td.seconds > 0:
                GPIO.output(relaymap[0], 1)
                GPIO.output(relaymap[1], 1)
                updateDomeStatus()                    
                lastkey = None


        if c == curses.KEY_LEFT:
            GPIO.output(relaymap[0], 0)
            updateDomeStatus()                    
            lastkey = datetime.datetime.now()
        elif c == curses.KEY_RIGHT:
            GPIO.output(relaymap[1], 0)
            updateDomeStatus()                    
            lastkey = datetime.datetime.now()
        elif c == curses.KEY_UP:
            if GPIO.input(relaymap[3])==1:
                GPIO.output(relaymap[2], not GPIO.input(relaymap[2]))
                updateDomeStatus()                    
                lastkey = datetime.datetime.now()
        elif c == curses.KEY_DOWN:
            if GPIO.input(relaymap[2])==1:
                GPIO.output(relaymap[3], not GPIO.input(relaymap[3]))
                updateDomeStatus()                    
                lastkey = datetime.datetime.now()
        elif c == ord('1'):
            current = GPIO.input(relaymap[4])
            GPIO.output(relaymap[4], not current)
            updateDomeStatus()                    
        elif c == ord('2'):
            current = GPIO.input(relaymap[5])
            GPIO.output(relaymap[5], not current)
            updateDomeStatus()                    
        elif c == ord('3'):
            current = GPIO.input(relaymap[6])
            GPIO.output(relaymap[6], not current)
            updateDomeStatus()                    
        elif c==-1:
            # No user interaction. 
            statusUpdate('Time', time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))                    
            # Wait for next update
            time.sleep(0.05)
        elif c == ord('q'):
            finish()
        elif c == ord('e'):
            start_manual_alignment_e()
        elif c == ord('w'):
            start_manual_alignment_w()
        elif c == ord('g'):
            alignment_mode = "goto"
            statusUpdate("Alignment mode", "GoTo next coordinates.")
    

wrapper(main)
finish()
exit(0)


class Menu():                                                          
    def __init__(self):
        self.position = 0                                                    
        self.menuitems = [
            #('s','Start Stellarium server (CTRL+1 to align/goto)',         telescope.start_server),
            #('O','Open serial port for RoboFocus',  telescope.open_robofocus_port), 
            ('e','Manual alignment',                telescope.set_alignment_side), 
            ('t','Toggle Stellarium mode',          telescope.toggle_stellarium_mode),
            #('o','Open serial port for telescope',  telescope.open_port), 
            ('d','Dome control',                    telescope.dome), 
            #('a','Align from target',               telescope.align_from_target), 
            # ('v','Void alignment',                  telescope.void_alignment),
            # ('p','Write telescope readout to file', telescope.write_telescope_readout),
            #('b','Return to previous target',       telescope.previous_alignment),
            #('g','Go to target',                    telescope.go_to_target), 
            #('m','Move RoboFocus',                  telescope.robofocus_userinput),
            #('R','Target right ascension',          telescope.set_target_rightascension), 
            #('d','Target declination',              telescope.set_target_declination), 
            #('C','Execute custom telescope command',telescope.send_custom_command),
            #('r','Read camera settings',            telescope.read_camera),
            #('I','Set camera ISO',                  telescope.define_iso),
            #('S','Set camera shutter speed',        telescope.shutter_speed),
            #('N','Set camera number of pictures',   telescope.numberofpictures),
            #('c','Capture images',                  telescope.capture_images),
            ('q','Exit',                            telescope.exit)
            ]
        self.window = curses.newwin(len(self.menuitems)+2,67,4,2)                                  
        self.window.keypad(1)                                                
        self.window.timeout(100)    # in ms
        
    def display(self):                                                       
        self.window.border(0)
        for index, item in enumerate(self.menuitems):                        
            if index == self.position:                                   
                mode = curses.A_REVERSE                                  
            else:                                                        
                mode = curses.A_NORMAL                                   
            msg = ' %s - %s ' % (item[0],item[1])                            
            self.window.addstr(1+index, 1, msg, mode)                    

        key = self.window.getch()                                        

        if key in [curses.KEY_ENTER, ord('\n')]:                         
            self.menuitems[self.position][2]()                           
        elif key == curses.KEY_UP:                                       
            self.position -= 1                                                   
            if self.position < 0:                                                
                self.position = 0                                                
        elif key == curses.KEY_DOWN:                                     
            self.position += 1                                                   
            if self.position >= len(self.menuitems):                               
                self.position = len(self.menuitems)-1                                
        else:
            for (index,m) in enumerate(self.menuitems):
                if ord(m[0])==key:
                    self.position=index
                    m[2]()


class Status():                                                          
    def __init__(self):
        ypos = 4+telescope.menu.window.getmaxyx()[0]
        self.window_status = curses.newwin(6,67,ypos,2)                                  
        ypos += self.window_status.getmaxyx()[0]
        self.window_telescope = curses.newwin(3+len(telescope.telescope_states),67,ypos,2)                                  
        self.maxmessages = 9;
        self.messages = []
        self.push_message("PTCS initialized.")
        ypos += self.window_telescope.getmaxyx()[0]
        self.window_messages = curses.newwin(3+self.maxmessages,67,ypos,2)                                  

    def push_message(self,message):
        if isinstance(message, str):
            message = message.strip()
            if len(message)>0:
                timestamp = time.strftime("%H:%M:%S", time.gmtime())                    
                self.messages.insert(0,"%s %s" %(timestamp,message))
                if len(self.messages)>self.maxmessages:
                    self.messages.pop()
        
    def display(self):                                                       
        self.window_status.clear()
        self.window_status.border(0)
        # Time
        self.window_status.addstr(1, 2, "Time (UTC)")                    
        self.window_status.addstr(1, 20, time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))                    
        # Port Telescope
        self.window_status.addstr(2, 2, "Telescope port")                    
        portname = "Not open"
        if telescope.serialport:
            portname = telescope.serialport.name
        self.window_status.addstr(2, 20, portname )                    
        # Port Robofocus
        i = 3
        #self.window_status.addstr(3, 2, "RoboFocus port")                    
        #portname = "Not open"
        #if telescope.robofocus_serialport:
        #    portname = telescope.robofocus_serialport.name
        #self.window_status.addstr(3, 19, portname )                    
        # Server
        self.window_status.addstr(i, 2, "Stellarium server")                    
        serverstatus = "Not running"
        if telescope.socket is not None:
            if telescope.conn is not None:
                serverstatus = "Connected" 
            else:
                serverstatus = "Waiting for connection" 
        self.window_status.addstr(i, 20, serverstatus )                    
        i += 1
        self.window_status.addstr(i, 2, "Stellarium mode")                    
        if telescope.stellarium_mode==0:
            stellarium_mode = "Align to next coordinates"
        else:
            stellarium_mode = "Go to next coordinates"
        self.window_status.addstr(i, 20, stellarium_mode )                    
        i += 1
         
        #self.window_status.addstr(i, 2, "Camera")                    
        #self.window_status.addstr(i, 19, telescope.camera)                    
        self.window_status.refresh()
        
        # Status Messages
        self.window_messages.clear()
        self.window_messages.border(0)
        self.window_messages.addstr(1, 2, "Status messages", curses.A_BOLD)                    
        index = 0
        for (message_index,message) in enumerate(self.messages):
            notfirst = 0
            while len(message)>0:
                if notfirst:
                    message = "         " + message
                notfirst = 1
                if index<self.maxmessages:
                    self.window_messages.addstr(2+index, 4, message[:61])
                index += 1                   
                message = message[61:]
        self.window_messages.refresh()
        
        # Telescope readout
        self.window_telescope.clear()
        self.window_telescope.border(0)
        self.window_telescope.addstr(1, 2, "Telescope readout", curses.A_BOLD)                    
        for (index,element) in enumerate(telescope.telescope_states):
            if len(element[0]):
                self.window_telescope.addstr(index+2, 2, element[0])                    
            if len(element[2]):
                self.window_telescope.addstr(index+2, 28, element[2])                    
        self.window_telescope.addstr(1, 48, "RoboFocus readout", curses.A_BOLD)                    
        for (index,element) in enumerate(telescope.robofocus_states):
            self.window_telescope.addstr(index+2, 2+46, element[0])                    
            self.window_telescope.addstr(index+2, 2+46+10, element[2])                    
        
        self.window_telescope.addstr(4, 48, "Camera settings", curses.A_BOLD)                    
        self.window_telescope.addstr(5, 48, "ISO       %s"% telescope.camera_iso)                    
        if "bulb" not in telescope.camera_shutter:
            if "N/A" not in telescope.camera_shutter and "bulb":
                self.window_telescope.addstr(6, 48, "Shutter   %ss"% telescope.camera_shutter)                    
            else:
                self.window_telescope.addstr(6, 48, "Shutter   %s"% telescope.camera_shutter)                    
        else:
            self.window_telescope.addstr(6, 48, "Shutter   %ds (b)"% telescope.camera_longexpshutter)                    
        self.window_telescope.addstr(7, 48, "Num       %d/%d"% (telescope.camera_numtaken,telescope.camera_num))                   
        self.window_telescope.refresh()

telescope = None    # Singleton
class Telescope():
    def __init__(self, stdscreen):
        global telescope
        telescope = self
        self.camera         = "Never read"
        self.camera_iso     = "N/A"
        self.camera_shutter = "N/A"
        self.camera_longexpshutter = 5
        self.camera_num = 1
        self.camera_status = 0
        self.subid = None
        self.lastcheck = None
        self.camera_numtaken = 0
        self.camera_path = None
        self.last_telescope_update = 0
        self.last_robofocus_update = 0
        self.conn = None
        self.calibrationconn = None
        self.socket = None
        self.calibrationsocket = None
        self.serialport = None
        self.robofocus_serialport = None
        self.logfilename = "observations.log"
        self.stellarium_mode = 0  # align
        self.screen = stdscreen                                              
        curses.curs_set(0)
        self.screen.addstr(1, 2, "UTSC | PTCS", curses.A_BOLD)
        self.screen.addstr(2, 2, "University of Toronto Scarborough | Python Telescope Control System", curses.A_BOLD)
        self.telescope_states= [
            ['Alignment state',              '!AGas;', ""],  
            ['Alignment side',               '!AGai;', ""],
            ['Current right ascension',      '!CGra;', ""],
            ['Current declination',          '!CGde;', ""],
            ['Target right ascension',       '!CGtr;', ""],
            ['Target declination',           '!CGtd;', ""]
        ]
        self.robofocus_states= [
            ['Version',         'FV', ""],  
            ['Position',        'FD', ""]
        ]
        self.screen.refresh()
        self.screen.immedok(True)
        self.menu = Menu()                       
        self.status = Status()    
        self.open_port(tryDefault=True)
        self.start_server()
        ## Main loop
        while True:
            # Get telescope heartbeat
            if time.time() - self.last_telescope_update > 2.: # only update the infos every 2 seconds
                self.last_telescope_update = time.time()
                if self.serialport is not None:
                    try:
                        self.serialport.read(1024) # empty buffer
                        for (index,element) in enumerate(self.telescope_states):
                            self.serialport.write(element[1]) 
                            time.sleep(0.05)
                            ret = self.serialport.read(1024).strip() 
                            # DEBUG
                            if 0:
                                with open("debug_log.txt","a+") as debug_f:
                                    debug_f.write(ret)
                                    debug_f.write("\n")

                            atcl_asynch = ret.split(chr(0x9F))
                            if len(atcl_asynch)>1:
                                ret = atcl_asynch[0]
                            if len(ret)>0:
                                if ret[0] == chr(0x8F):
                                    ret = "ATCL_ACK"
                                if ret[0] == chr(0xA5):
                                    ret = "ATCL_NACK"
                                if ret[-1] == ";":
                                    ret = ret[:-1]
                            else:
                                ret = "N/A"
                            
                            
                            if "Internal error" in ret:
                                self.push_message(ret)
                                ret = "N/A"
                            element[2] = ret
                    except:
                        if self.serialport is not None:
                            if self.serialport.isOpen():
                                self.serialport.close()
                        self.push_message("Something is wrong. Closing serial connection to telescope.")
                else:
                    for (index,element) in enumerate(self.telescope_states):
                        element[2] = "N/A"
                # Send data to stellarium
                if self.socket is not None:
                    if self.conn is not None:
                        try:
                            for (desc, command, value) in self.telescope_states:
                                if command == '!CGra;':
                                    ra = ra_str2raw(value)
                                if command == '!CGde;':
                                    dec = dec_str2raw(value)
                            data = struct.pack('<hhQIii',24,0,int(round(time.time() * 1000)), ra, dec, 0)
                            telescope.conn.send(data)
                        except:
                            pass
            # Get RoboFocus heartbeat
            if time.time() - self.last_robofocus_update > 1.: # only update the infos every second
                self.last_robofocus_update = time.time()
                if self.robofocus_serialport is not None:
                    self.robofocus_parse()
                else:
                    for (index,element) in enumerate(self.robofocus_states):
                        element[2] = "N/A"
            # Poll socket for Stellarium
            if self.socket is not None:
                if self.conn is None:
                    try:
                        self.conn, addr = self.socket.accept()
                        self.conn.settimeout(0)
                        #socket.setblocking(0)
                        self.push_message("Connection established from %s:%d."% addr)
                    except socket.error as e:
                        pass
                else:
                    try:
                        time.sleep(0.01)
                        data = self.conn.recv(1024)
                        if len(data)==20:   # goto command
                            data = struct.unpack('<hhQIi',data)
                            ra_string, dec_string = ra_raw2str(data[-2]), dec_raw2str(data[-1])
                            self.push_message("Received from stellarium: %s %s" % (ra_string,dec_string))
                            if dec_string[-2:]=="60":
                                dec_string = dec_string[:-2]+"59"
                                self.push_message("Converted 60->59.")
                            self.send('!CStr' + ra_string + ';')
                            self.send('!CStd' + dec_string + ';')
                            if self.stellarium_mode==0:
                                self.align_from_target()
                                self.stellarium_mode=1
                            else: 
                                self.go_to_target()
                        elif len(data)==0:
                            pass
                        else:
                            self.push_message("Unknown command received of length %d."%len(data))
                    except socket.error as e:
                        pass
            # Poll calibration socket
            if self.calibrationsocket is None:
                self.calibrationsocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.calibrationsocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                port = 10002
                try:
                    self.calibrationsocket.settimeout(0)
                    self.calibrationsocket.bind(("127.0.0.1", port))
                    self.calibrationsocket.listen(1)
                    self.push_message("Server waiting for connection on port %d."%port)
                except socket.error as e:
                    self.push_message("Socket error (%s)"%e.strerror)
                    self.calibrationsocket = None
            else:
                try:
                    self.calibrationconn, addr = self.calibrationsocket.accept()
                    try:
                        self.push_message("Calibration connection established from %s:%d."% addr)
                        while True:
                            data = self.calibrationconn.recv(2048)
                            if data:
                                direction, ra_string, dec_string = data.split(";")
                                self.send('!ASas' + direction + ';')
                                self.stellarium_mode = 0 
                                if dec_string[-2:]=="60":
                                    dec_string = dec_string[:-2]+"59"
                                    self.push_message("Converted 60->59.")
                                self.send('!CStr' + ra_string + ';')
                                self.send('!CStd' + dec_string + ';')
                                self.align_from_target()
                                self.stellarium_mode=1
                                self.push_message("Alignment complete.")
                            else:
                                break
                    except Exception as e:
                        self.push_message(e)
                    finally: 
                        self.calibrationconn.close()
                        self.calibrationsocket = None
                except Exception as e:
                    self.push_message(e)
                    pass
            # Refresh display
            self.menu.display()
            self.status.display()
    
    def push_message(self, message):
        self.status.push_message(message)

    def get_param(self, prompt):
        win = curses.newwin(5, 62, 5, 5)
        curses.echo()
        curses.curs_set(2)
        win.border(0)
        win.addstr(1,2,prompt)
        r = win.getstr(3,2,55)
        curses.noecho()
        curses.curs_set(0)
        self.screen.refresh()
        return r

    #################### Stellarium communication functions ######################
    def toggle_stellarium_mode(self):
        self.stellarium_mode = not self.stellarium_mode
    
    def dome(self):
        i = 6
        GPIO.output(relaymap[i], 0)
        time.sleep(1.4)
        GPIO.output(relaymap[i], 1)

    def start_server(self):
        if self.socket == None:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            port = 10001
            try:
                self.socket.settimeout(0)
                self.socket.bind(("127.0.0.1", port))
                self.socket.listen(1)
                self.push_message("Server waiting for connection on port %d."%port)
            except socket.error as e:
                self.push_message("Socket error (%s)"%e.strerror)
                self.socket = None
        else:
            self.push_message("Server already running.")
    
    #################### Telescope communication functions ######################
    def open_port(self,tryDefault=False):
        if os.uname()[0]=="Darwin":
            default_port_name = '/dev/tty.usbserial'
        else:
            if socket.gethostname()=="rein007":
                default_port_name = '/dev/ttyAMA0'
            else:
                default_port_name = '/dev/ttyS0'
        if tryDefault:    
            port_name = default_port_name
        else:
            port_name = self.get_param("Telescope serial port [leave blank for '"+default_port_name+"']")
        try:
            if port_name == '':
                port_name = default_port_name
            self.serialport = serial.Serial(port_name, 19200, timeout = 0.01) 
            self.push_message("Successfully opened serial port for telescope.")
        except:
            self.serialport = None
            self.push_message("Opening serial port for telescope failed.")
    
    def send(self,data):
        if len(data)<1:
            return False
        elif self.serialport is not None:
            self.serialport.read(10240)
            self.serialport.write(data)
            self.push_message("Sent '%s' to telescope."%data)
            time.sleep(0.5)
            ret = self.serialport.read(1024)
            for r in ret.split(";"): 
                self.push_message("Received: %s."%r)

            return True
        else:
            self.push_message("Did NOT send data to telescope (port not open).")
            return False
    
    def set_alignment_side(self):
        direction = self.get_param("Set alignment side [West/East] (blank for West)")
        if len(direction) ==0:
            direction = "West"
        if direction == "W" or direction == "w":
            direction = "West"
        if direction == "E" or direction == "e":
            direction = "East"
        if direction == "West" or direction == "East": 
            self.send('!ASas' + direction + ';')
            self.stellarium_mode = 0 
        else:
            self.push_message("Not a valid alignment side.")

    def set_target_rightascension(self):
        ra = self.get_param("Set target Right Ascension [hh:mm:dd]")
        if len(ra)>0:
            self.send('!CStr' + ra + ';')
        else:
            self.push_message("Did not receive user input.")

#******DAN, ARI, KIM, NEW CAMERA DEF************************************
    def read_camera(self):
        # Kill procs
        if sys.platform == "darwin":
            print("Killing PTPCamera process")
            os.system("killall PTPCamera")
            os.system("killall Type4Camera")
        # Get Name
        os.system("gphoto2 --auto-detect > .gphoto.tmp")
        with open(".gphoto.tmp") as f:
            lines = f.readlines()
            if len(lines)>2:
                line = lines[2]
                if "usb:" in line:
                    telescope.camera = line.split("usb:")[0].strip()
                    # Get ISO Setting
                    os.system("gphoto2 --get-config=iso > .gphoto.tmp" )
                    with open(".gphoto.tmp") as f:
                        lines = f.readlines()
                        for line in lines:
                            if "Current:" in line:
                                telescope.camera_iso = line.split("Current:")[1].strip()
                    # Get Shutter speed
                    os.system("gphoto2 --get-config=shutterspeed > .gphoto.tmp" )
                    with open(".gphoto.tmp") as f:
                        lines = f.readlines()
                        for line in lines:
                            if "Current:" in line:
                                telescope.camera_shutter = line.split("Current:")[1].strip()
            else:
                self.push_message("No camera found.")


    def define_iso(self):
        iso_value = self.get_param("Set ISO value 100, 200, 400, 800, 1600, 3200, 6400:")
        if len(iso_value)>0:
            os.system("gphoto2 --set-config capture=on --set-config iso=" + iso_value )
        self.read_camera()

    def shutter_speed(self):
        shutter_value = self.get_param("Enter exposure time in s, e.g. 1, 5, 20, 1/10:")
        if len(shutter_value)>0:
            try:
                svi = int(shutter_value)
            except:
                svi = 1
            if svi<30:
                os.system("gphoto2 --set-config capture=on --set-config shutterspeed=" + shutter_value )
            else:
                os.system("gphoto2 --set-config shutterspeed=bulb")
                telescope.camera_longexpshutter = int(shutter_value)
        self.read_camera()

    def numberofpictures(self):
        num_value = self.get_param("Number of pictures [default 1]")
        if len(num_value)>0:
            telescope.camera_num = int(num_value)
        else:
            telescope.camera_num = 1

#def rename(name, num):
#renamecmd = "mv %s %s%i.jpg"%("capt0000.jpg",name,num)
#os.system(renamecmd)

    def capture_images(self):
        filename = self.get_param("Filename [default: test]")
        self.read_camera()
        if len(filename)<1:
            filename = "test"
        folder = 'pictures/'
        if not os.path.exists(folder):
            self.push_message("Creating folder '"+folder+"'.")
            os.system("mkdir "+folder)
        telescope.camera_path = ''+folder+''+filename
        telescope.camera_numtaken = 0
        telescope.camera_status = 1

    def camera_check(self):
        if telescope.camera_status == 0:
            return
        if telescope.camera_numtaken >= telescope.camera_num:
            if os.path.isfile(".gphoto.tmp"):
                self.push_message("All pictures taken.")
                telescope.camera_status = 0
            return
             
        if telescope.camera_status==1 or os.path.isfile(".gphoto.tmp"):
            os.system("rm -f .gphoto.tmp")
            if "bulb" not in telescope.camera_shutter:
                self.push_message("Taking picture %d of %d." %(telescope.camera_numtaken+1,telescope.camera_num))
                os.system("(gphoto2 --capture-image-and-download --force-overwrite --filename=%s_%04d.jpg >/dev/null; echo 1 > .gphoto.tmp) &"%(telescope.camera_path,telescope.camera_numtaken))
            else:
                self.push_message("Taking picture %d of %d (%ds long exp)." %(telescope.camera_numtaken+1,telescope.camera_num,telescope.camera_longexpshutter))
                # gphoto2 --wait-event=2s --set-config eosremoterelease=Immediate --wait-event=5s --set-config eosremoterelease=Off --wait-event-and-download=5s
                os.system("(gphoto2 --wait-event=2s --set-config eosremoterelease=Immediate --wait-event=%ds --set-config eosremoterelease=Off --force-overwrite --filename=%s_%04d.jpg --wait-event-and-download=5s >/dev/null; echo 1 > .gphoto.tmp) &"% (telescope.camera_longexpshutter, telescope.camera_path,telescope.camera_numtaken))
            telescope.camera_status = 2
            telescope.camera_numtaken += 1
            

        # Bulb mode not implemented yet:
        #    cmd = "gphoto2 --set-config shutterspeed=bulb"
        #    cmd = "gphoto2 --set-config bulb=1 eosremoterelease=Immediate --wait-event=120s --set-config eosremoterelease=Off --wait-event-and-download=2s"
        #

        # Live previewing not implemented yet:
        #root = tk.Tk()
        #root.geometry('400x400')
        #canvas = tk.Canvas(root,width=400,height=400)
        #canvas.pack()
        #pilImage = Image.open(telescope.camera_path+"_"+str(a)+".jpg").resize((400, 400),Image.ANTIALIAS)
        #image = ImageTk.PhotoImage(pilImage)
        #imagesprite = canvas.create_image(0,0,image=image,anchor=tk.NW)
        #root.after(1000, lambda: root.destroy()) # Destroy the widget after 30 seconds
        #root.mainloop()


#******NEW CAMERA DEF************************************

    def set_target_declination(self):
        dec = self.get_param("Set target Declination [+dd:mm:ss]")
        if len(dec)>0:
            self.send('!CStd' + dec + ';')
        else:
            self.push_message("Did not receive user input.")

    def align_from_target(self):
        self.send('!AFrn;')

    def go_to_target(self):
        self.send('!GTrd;')
    
    def void_alignment(self):
        self.send('!AVoi;')

    def previous_alignment(self):
        self.send('!GTol;')
    
    def send_custom_command(self):
        command = self.get_param("Command (ommit ! and ;):")
        if len(command)>0:
            command = "!" + command + ";"
            self.send(command)
        else:
            self.push_message("Did not receive user input.")

    def write_telescope_readout(self):
        with open(self.logfilename, 'a') as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S\t", time.gmtime()))                  
            for (desc, command, value) in self.telescope_states:
                f.write(value+"\t")
            f.write(value+"\n")
            self.push_message("Telescope readout saved.")
            f.close()

    #################### Robofocus communication functions ######################
    def open_robofocus_port(self):
        if os.uname()[0]=="Darwin":
            default_port_name = '/dev/tty.usbserial'
        else:
            default_port_name = '/dev/ttyUSB0'
        port_name = self.get_param("RoboFocus serial port [leave blank for '"+default_port_name+"']")
        try:
            if port_name == '':
                port_name = default_port_name
            self.robofocus_serialport = serial.Serial(port_name, 9600, timeout = 0.01) 
            self.push_message("Successfully opened serial port for RoboFocus.")
            self.robofocus_get_version()
        except:
            self.robofocus_serialport = None
            self.push_message("Opening serial port for RoboFocus failed.")
            
    def robofocus_send(self,c):            
        Z = 0
        for i in c:
            Z += ord(i)
        Z = Z%256   # checksum
        self.push_message("Sent '%s' to RoboFocus."%c)
        self.robofocus_serialport.write(c+chr(Z)) 

    def robofocus_read(self):
        r = self.robofocus_decode_readout(self.robofocus_serialport.read(1024))
        self.push_message("Read '%s' from RoboFocus."%r)
        return r

    def robofocus_parse(self):
        self.robofocus_decode_readout(self.robofocus_serialport.read(1024))
        
    def robofocus_decode_readout(self,r):
        if len(r)>0:
            if r[0]=="I" or r[0]=="O": # ignore in/out characters
                return self.robofocus_decode_readout(r[1:])
        ret = ""
        if len(r)>=9:
            Z = 0
            for i in r[:8]:
                Z += ord(i)
            Z = Z%256  # checksum
            if Z==ord(r[8]):
                ret = r[:8]
            else:
                print "Checksum did not match."
            for (index,element) in enumerate(self.robofocus_states):
                if ret[0:2] == element[1]:
                    value = ret[2:]
                    while value[0]=="0":
                        value = value[1:]
                    element[2] = value

        if len(r)>9:
            ret += self.robofocus_decode_readout(r[9:])
        return ret

    def robofocus_get_version(self):
        self.robofocus_serialport.read(1024) # empty buffer
        self.robofocus_send("FV000000") 
        time.sleep(0.15)
        return self.robofocus_read()

    def robofocus_move_in(self,steps):
        self.robofocus_serialport.read(1024) # empty buffer
        self.robofocus_send("FI%06d"%(steps))
        return 

    def robofocus_move_out(self,steps):
        self.robofocus_serialport.read(1024) # empty buffer
        self.robofocus_send("FO%06d"%(steps))
        return 

    def robofocus_userinput(self):
        steps = self.get_param("Move RoboFocus [+=out,-=in]")
        try: 
            steps = int(steps)
        except:
            steps = 0
        self.robofocus_move(steps)

    def robofocus_move(self,steps):
        if steps>=0:
            return self.robofocus_move_out(steps)
        if steps<0:
            return self.robofocus_move_in(-steps)
        
    #################### Cleanup functions ######################
    def exit(self):
        if self.socket is not None:
            if self.conn is not None:
                self.conn.close()
                try:
                    self.socket.shutdown(socket.SHUT_RD)
                except:
                    pass
            self.socket.close()
        if self.calibrationsocket is not None:
            if self.calibrationconn is not None:
                self.calibrationconn.close()
                try:
                    self.calibrationsocket.shutdown(socket.SHUT_RD)
                except:
                    pass
            self.calibrationsocket.close()
        if self.serialport is not None:
            if self.serialport.isOpen():
                self.serialport.close()
        if self.robofocus_serialport is not None:
            if self.robofocus_serialport.isOpen():
                self.robofocus_serialport.close()
        exit()

        
if __name__ == '__main__':                                                       
    curses.wrapper(Telescope)
