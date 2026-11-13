"""Owner-configured unicast Wake-on-LAN; packet delivery is not boot proof."""
import ipaddress
import json
from pathlib import Path
import re
import socket
import time

# The packet goes to the home router, which forwards UDP 9 to the PC's reserved
# LAN address. After a long power-off the router may no longer know which port
# the PC is on, so a long-offline wake is not promised; it asks for the button.
LEASE_SECONDS = 3600


def pc_power_command(text):
    return bool(re.fullmatch(r'(?:(?:대비스|자비스|데비스)\s*)?(?:내\s*)?(?:PC|컴퓨터|컴)\s*(?:켜|켜줘|켜기|온라인|on)',
                             (text or '').strip(),re.I))


def wake_pc(config_path, socket_factory=socket.socket, repeats=3, pause=time.sleep):
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
        for attempt in range(repeats):
            # UDP has no delivery receipt; a few copies cover a single lost packet.
            if attempt:pause(.2)
            if sender.sendto(packet,(str(address),9))!=len(packet):raise OSError('incomplete wake packet')
    return {'packet_sent':True,'boot_confirmed':False,'packets':repeats}


def offline_seconds(registry, node):
    """Seconds since the PC client last reported in, or None when unknown."""
    try:
        seen = next((n['last_seen'] for n in registry.list_nodes() if n['name'] == node), None)
        return max(0, registry.clock() - seen) if seen else None
    except Exception:
        return None


def wake_reply(label, offline):
    text = label + ' 깨우기 신호를 보냈어요. 부팅 성공은 아직 확인되지 않았어요.'
    if offline is not None and offline > LEASE_SECONDS:
        text += (f'\n꺼진 지 약 {offline / 3600:.0f}시간이 지났어요. 몇 분 안에 안 켜지면 본체 전원 버튼을 눌러 주세요.')
    return text
