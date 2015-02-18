# -*- coding: utf-8 -*-

"""
---------------------------------------------------------------------------------
"THE BEER-WARE LICENSE" (Revision 42):
<adam.bambuch2@gmail.com> wrote this file. As long as you retain this notice you
can do whatever you want with this stuff. If we meet some day, and you think
this stuff is worth it, you can buy me a beer in return Adam Bambuch
---------------------------------------------------------------------------------
"""

import sys
import random
import logging
import math
import threading
import time

import irc.bot
import tclib

import config

start = True

class IrcBot(threading.Thread, irc.bot.SingleServerIRCBot):
    def __init__(self):
        threading.Thread.__init__(self)
        irc.bot.SingleServerIRCBot.__init__(self, [(config.irc_server, config.irc_port)],
                                            config.irc_nickname,
                                            config.irc_nickname)
        self._channel = config.irc_channel
        self._channel_autorejoin = config.irc_autorejoin
        self._tcw = TCWorker(self)
        self._tcw.start()
        
    def run(self):
        irc.bot.SingleServerIRCBot.start(self)
                
    def on_welcome(self, control, event):
        control.join(self._channel)
        
    def on_nicknameinuse(self, control, event):
        new_nick = "%s_%d" % (control.get_nickname()[:26], random.randint(1000, 9999))
        control.nick(new_nick)
        
    def _on_kick(self, control, event):
        if not self._channel_autorejoin:
            return
        
        channel = event.target
        control.join(channel)
        
    def on_pubmsg(self, control, event):
        msg = event.arguments[0]
        user = event.source.split("!")[0]
        
        if msg.startswith(config.irc_command_prefix):
            msg = msg.replace(config.irc_command_prefix, "", 1)
            self._do_command(user, msg)
        else:
            self._tcw.send_msg(user, msg)
            
    def send_msg(self, user, msg):
        self.connection.privmsg(self._channel, msg)
        
    def _do_command(self, user, msg):
        global start
        
        if msg == "restart" and user in config.irc_owners:
            self._tcw.die()
            start = True
            self.die()
    
class TCWorker(threading.Thread):
    def __init__(self, ircbot):
        threading.Thread.__init__(self)
        
        try:
            self._wow_ver = tclib.WoWVersions(version = config.tc_version)
        except tclib.exceptions.WoWVersionsError as e:
            logging.error(e.message)
            sys.exit(3)
            
        self._realmserver = config.tc_realmserver
        self._realmport = config.tc_realmport
        self._realm = config.tc_realm
        self._username = config.tc_username
        self._password = config.tc_password
        self._character = config.tc_character
        self._channel = config.tc_channel
        self._ircbot = ircbot
        
        self._status = ""
        self._world = None
        self._connected = False
        self._con_lock = threading.RLock()
        self._die = False
        
    def run(self):
        while True:
            if self._connected:
                try:
                    self._world.err()
                except tclib.exceptions.StreamBrokenError as e:
                    with self._con_lock:
                        self._connected = False
                    logging.warning(e.message)
                    self._status = "Disconnected"
                    self._log_status()
            else:
                self.connect()
            if self._die:
                break
            time.sleep(1)
            
    def die(self):
        self.disconnect()
        self._die = True
                  
    def connect(self):
        with self._con_lock:
            self._status = "Connecting"
            r = tclib.Realm(self._username, self._password, self._realmserver, self._realmport, self._wow_ver)
            r.start()
            r.join(60)
            if not r.done():
                self._status = "Unable to connect to Realm List Server; Reconnecting"
                self._log_status()
                r.die()
                return False
            
            try:
                r.err()
            except (tclib.exceptions.LogonChallangeError,
                    tclib.exceptions.LogonProofError,
                    tclib.exceptions.StreamBrokenError,
                    tclib.exceptions.CryptoError) as e:
                self._status = "Unable to connect to Realm List Server; Reconnecting"
                self._log_status()
                logging.debug("%s - %s", type(e), str(e))
                return False
            if self._realm not in r.get_realms():
                self._status = "Realm %s not found" % self._realm
                self._log_status()
                return False
            realm_i = r.get_realms()[self._realm]
            w = tclib.World(realm_i["host"],
                            realm_i["port"],
                            self._username,
                            r.get_S_hash(),
                            self._wow_ver,
                            realm_i["id"])
            w.start()
            try:
                players = w.wait_get_my_players()
            except (tclib.exceptions.TimeoutError, tclib.exceptions.StreamBrokenError) as e:
                self._status = "Unable to connect to World Server; Reconnecting"
                self._log_status()
                logging.debug("%s - %s", type(e), str(e))
                w.disconnect()
                return False
                
            try:
                w.login(self._character)
            except tclib.exceptions.BadPlayer as e:
                self._status = "Character %s not found; Reconnecting" % self._character
                self._log_status()
                w.disconnect()
                return False
            
            w.send_join_channel(self._channel)
            w.callback.register(tclib.const.SMSG_MESSAGECHAT, self._handle_message_chat)
            w.callback.register(tclib.const.SMSG_GM_MESSAGECHAT, self._handle_message_chat)
            self._world = w
            self._status = "Connected"
            self._log_status()
            self._connected = True
            
            return True
    
    def send_msg(self, user, msg):
        with self._con_lock:
            if not self._connected:
                return
            
            for i in range(int(math.ceil(len(msg) / 200.0))):
                send = str(user + ": " + msg[i*255:(i+1)*255])
                self._world.send_message_chat(tclib.const.CHAT_MSG_CHANNEL,
                                              send,
                                              self._channel)
            
    def disconnect(self):
        with self._con_lock:
            if self._connected:
                self._world.disconnect()
            
    def reconnect(self):
        with self._con_lock:
            if self._connected:
                self._world.disconnect()
                self.connect()
            
    def _log_status(self):
        logging.warning("TC: %s", self._status)
        
    def _handle_message_chat(self, opcode, msg_type, data):
        if opcode not in (tclib.const.SMSG_MESSAGECHAT,
                          tclib.const.SMSG_GM_MESSAGECHAT):
            return
        if data["channel"].lower() != self._channel.lower():
            return
        
        user = data["source"].name
        msg = data["msg"]
        
        self._ircbot.send_msg(user, msg)
        

if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    
    while start == True:
        start = False
        bot = IrcBot()
        bot.start()
        bot.join()
    