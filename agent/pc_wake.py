"""Owner-configured unicast Wake-on-LAN; packet delivery is not boot proof."""
import ipaddress
import json
from pathlib import Path
import re
import socket


def pc_power_command(text):
    return bool(re.fullmatch(r'(?:(?:대비스|자비스|데비스)\s*)?(?:내\s*)?(?:PC|컴퓨터|컴)\s*(?:켜|켜줘|켜기|온라인|on)',
                             (text or '').strip(),re.I))


def wake_pc(config_path, socket_factory=socket.socket):
    config=json.loads(Path(config_path).read_text(encoding='utf-8'))
    address=ipaddress.IPv4Address(config['address'])
    if address.is_multicast or address.is_unspecified or address.is_loopback or str(address)=='255.255.255.255':
        raise ValueError('invalid configured PC address')
    mac=config['mac']
    if not isinstance(mac,str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',mac):
        raise ValueError('invalid configured PC MAC')
    packet=b'\xff'*6+bytes.fromhex(mac.replace(':',''))*16
    with socket_factory(socket.AF_INET,socket.SOCK_DGRAM) as sender:
        sender.settimeout(5)
        sent=sender.sendto(packet,(str(address),9))
    if sent!=len(packet):raise OSError('incomplete wake packet')
    return {'packet_sent':True,'boot_confirmed':False}
