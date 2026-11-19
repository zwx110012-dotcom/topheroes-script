"""Resolve the user's current loopback proxy without changing Windows settings."""
import re
import socket
import sys
import winreg


def system_proxy():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
        enabled = winreg.QueryValueEx(key, 'ProxyEnable')[0]
        if not enabled:
            return None
        return winreg.QueryValueEx(key, 'ProxyServer')[0]


def proxy_port(value):
    value = value.strip()
    if '=' in value:
        mapping = dict(part.strip().split('=', 1) for part in value.split(';') if '=' in part)
        value = mapping.get('http') or mapping.get('https') or mapping.get('socks') or ''
    match = re.fullmatch(r'(?:https?://|socks5?://)?(?:127\.0\.0\.1|localhost|\[::1\]):(\d{1,5})', value, re.I)
    if not match or not 1 <= int(match[1]) <= 65535:
        raise ValueError('Current system proxy must be a loopback HTTP/SOCKS mixed proxy.')
    return int(match[1])


def probe(port):
    with socket.create_connection(('127.0.0.1', port), 3) as connection:
        connection.settimeout(3)
        connection.sendall(b'\x05\x01\x00')
        response = b''
        while len(response) < 2:
            part = connection.recv(2-len(response))
            if not part:
                break
            response += part
        if response != b'\x05\x00':
            raise OSError('Current proxy does not support unauthenticated SOCKS5.')


def resolve_proxy():
    try:
        configured = system_proxy()
    except FileNotFoundError:
        configured = None
    port = proxy_port(configured) if configured else 10909
    probe(port)
    return port


if __name__ == '__main__':
    try:
        print(resolve_proxy())
    except (OSError, ValueError) as error:
        print('Game proxy unavailable: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
