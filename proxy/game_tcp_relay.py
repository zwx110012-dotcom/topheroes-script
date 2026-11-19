"""Loopback-only TCP forwarding for the launched Top Heroes process.
No routes, DNS settings, TLS certificates or VPN configuration are modified.
"""
import asyncio, ipaddress, json, pathlib, signal, sys, time

ROOT = pathlib.Path(__file__).resolve().parent
LOG = ROOT / 'tcp-relay.log'
READY = ROOT / 'relay-ready.json'
import os
from resolve_proxy import resolve_proxy
PROXY_HOST = '127.0.0.1'
PROXY_PORT = int(os.environ['TOPHEROES_PROXY_PORT']) if 'TOPHEROES_PROXY_PORT' in os.environ else resolve_proxy()
if not 1 <= PROXY_PORT <= 65535: raise ValueError('Invalid proxy port')
listeners = {}
stats = {'opened': 0, 'failed': 0, 'active': 0}

def log(message):
    with LOG.open('a', encoding='utf-8') as f:
        f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + message + '\n')

def valid_host(host):
    if not host or len(host) > 253 or any(c in host for c in '\r\n\t /\\'): return False
    try:
        ip = ipaddress.ip_address(host)
        return ip.version == 4 and (ip.is_global or ip in ipaddress.ip_network('198.18.0.0/15'))
    except ValueError:
        return host.endswith(('.topwargame.com', '.topwarapp.com'))

async def socks_connect(host, port):
    reader, writer = await asyncio.wait_for(asyncio.open_connection(PROXY_HOST, PROXY_PORT), 5)
    try:
        writer.write(b'\x05\x01\x00'); await writer.drain()
        if await asyncio.wait_for(reader.readexactly(2), 5) != b'\x05\x00': raise IOError('SOCKS authentication unavailable')
        try:
            addr = ipaddress.ip_address(host)
            target = b'\x01' + addr.packed
        except ValueError:
            encoded = host.encode('idna')
            target = b'\x03' + bytes([len(encoded)]) + encoded
        writer.write(b'\x05\x01\x00' + target + port.to_bytes(2, 'big')); await writer.drain()
        header = await asyncio.wait_for(reader.readexactly(4), 12)
        if header[:2] != b'\x05\x00': raise IOError('SOCKS CONNECT failed code=' + str(header[1]))
        if header[3] == 1: await reader.readexactly(4)
        elif header[3] == 3: await reader.readexactly((await reader.readexactly(1))[0])
        elif header[3] == 4: await reader.readexactly(16)
        else: raise IOError('Invalid SOCKS address type')
        await reader.readexactly(2)
        return reader, writer
    except BaseException:
        writer.close()
        raise

async def forward(game_reader, game_writer, host, port):
    remote_writer = None
    stats['active'] += 1
    try:
        remote_reader, remote_writer = await socks_connect(host, port)
        stats['opened'] += 1
        log('CONNECTED target=' + host + ':' + str(port))
        async def pump(reader, writer):
            while True:
                chunk = await reader.read(65536)
                if not chunk: return
                writer.write(chunk); await writer.drain()
        tasks = [asyncio.create_task(pump(game_reader, remote_writer)), asyncio.create_task(pump(remote_reader, game_writer))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        stats['failed'] += 1
        log('FAILED target=' + host + ':' + str(port) + ' type=' + type(e).__name__)
    finally:
        stats['active'] -= 1
        game_writer.close()
        if remote_writer: remote_writer.close()

async def control(reader, writer):
    try:
        line = (await asyncio.wait_for(reader.readline(), 5)).decode('utf-8').strip()
        host, port_text = line.split('\t')
        port = int(port_text)
        if not valid_host(host) or not 1 <= port <= 65535: raise ValueError('Invalid game target')
        key = (host, port)
        if key not in listeners:
            if len(listeners) >= 32: raise ValueError('Target limit reached')
            listeners[key] = await asyncio.start_server(lambda r,w: forward(r,w,host,port), '127.0.0.1', 0)
        local_port = listeners[key].sockets[0].getsockname()[1]
        writer.write((str(local_port) + '\n').encode('ascii')); await writer.drain()
    except Exception as e:
        writer.write(b'ERROR\n')
        try: await writer.drain()
        except Exception: pass
        log('CONTROL_FAILED type=' + type(e).__name__)
    finally: writer.close()

async def main():
    server = await asyncio.start_server(control, '127.0.0.1', 0, limit=1024)
    port = server.sockets[0].getsockname()[1]
    READY.write_text(json.dumps({'controlPort': port, 'pid': __import__('os').getpid()}), encoding='utf-8')
    log('READY controlPort=' + str(port))
    try:
        async with server: await server.serve_forever()
    finally:
        for item in listeners.values(): item.close()
        log('STOPPED ' + json.dumps(stats))

if __name__ == '__main__': asyncio.run(main())
