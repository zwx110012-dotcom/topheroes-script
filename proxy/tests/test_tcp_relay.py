import asyncio, importlib.util, pathlib, sys, tempfile, os
os.environ['TOPHEROES_PROXY_PORT'] = '1'  # Import without consulting the system proxy.
root=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(root.parent))
spec=importlib.util.spec_from_file_location('relay',root.parent/'game_tcp_relay.py')
relay=importlib.util.module_from_spec(spec);spec.loader.exec_module(relay)
relay.LOG=root/'mock-relay.log'
async def mock_socks(reader,writer):
    try:
        assert await reader.readexactly(3)==b'\x05\x01\x00'
        writer.write(b'\x05\x00');await writer.drain()
        h=await reader.readexactly(4);assert h==b'\x05\x01\x00\x03'
        host=(await reader.readexactly((await reader.readexactly(1))[0])).decode()
        port=int.from_bytes(await reader.readexactly(2),'big')
        assert (host,port)==('test.topwargame.com',3963)
        writer.write(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x01');await writer.drain()
        while chunk:=await reader.read(4096):writer.write(chunk);await writer.drain()
    finally:writer.close()
async def main():
    socks=await asyncio.start_server(mock_socks,'127.0.0.1',0)
    relay.PROXY_PORT=socks.sockets[0].getsockname()[1]
    control=await asyncio.start_server(relay.control,'127.0.0.1',0,limit=1024)
    port=control.sockets[0].getsockname()[1]
    p=await asyncio.create_subprocess_exec(str(root.parent/'VerifyTcpHook.exe'),str(port),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
    output,_=await asyncio.wait_for(p.communicate(),15)
    print(output.decode(errors='replace'))
    assert p.returncode==0
    for server in relay.listeners.values():server.close()
    control.close();socks.close()
asyncio.run(main())
