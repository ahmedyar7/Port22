"""
The clients would scans the peers before discovering them.
Meaning that they would browse for the clients
"""

import asyncio
from zeroconf import ServiceBrowser, Zeroconf
import time
import socket


class PeerListener:
    def __init__(self):
        self.peers = []

    def add_services(self, zc, type_, name):
        info = zc.get_service_info(type_, name)

        if info:

            ip = socket.inet_ntoa(info.addresses[0])
            self.peers.append((name, ip, info.port))

    def remove_service(self, zc, type_, name):
        pass

    def update_service(self, zc, type_, name):
        pass


def find_peers(timeout=3):
    zc = Zeroconf()
    listener = PeerListener()
    ServiceBrowser(zc, "_port22._tcp.local", listener)
    time.sleep(timeout)
    zc.close()
    return listener.peers
