"""Public-only forward proxy. Resolve once, reject mixed answers, dial numeric IPs.

Only GET/HEAD over HTTP and CONNECT to port 443 are supported. No credentials,
request bodies or persistent HTTP proxy connections are accepted.
"""
import asyncio
import contextlib
import ipaddress
import socket
from urllib.parse import urlsplit

from .public_urls import _global_address, validate_public_url


async def resolve_public(host, port):
    answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(a[4][0] for a in answers))


async def connect_public(host, port, *, resolve=resolve_public, connect=asyncio.open_connection):
    if port not in (80, 443):
        raise ValueError('unsupported port')
    answers = await resolve(host, port)
    if not answers or any(not _global_address(address) or ipaddress.ip_address(address).is_multicast
                          for address in answers):
        raise ValueError('non-public destination')
    # Numeric addresses cannot be rebound by a second DNS lookup.
    for address in answers:
        try:
            return await connect(address, port)
        except OSError:
            continue
    raise OSError('destination unreachable')


def parse_request(raw):
    lines = raw.decode('ascii').split('\r\n')
    method, target, version = lines[0].split(' ')
    if version not in ('HTTP/1.0', 'HTTP/1.1'):
        raise ValueError('unsupported HTTP version')
    tunnel = method == 'CONNECT'
    if method not in ('CONNECT', 'GET', 'HEAD'):
        raise ValueError('unsupported method')
    url = 'https://' + target if tunnel else target
    # Syntax validation only here; connect_public owns DNS and connection pinning.
    validate_public_url(url, resolver=lambda h, p: ['93.184.215.14'])
    parts = urlsplit(url)
    if tunnel:
        if parts.port != 443 or parts.path or parts.query or parts.fragment:
            raise ValueError('invalid CONNECT authority')
    elif parts.scheme != 'http' or parts.fragment:
        raise ValueError('only HTTP absolute targets supported')
    headers = []
    blocked = {'host', 'connection', 'proxy-connection', 'proxy-authorization',
               'proxy-authenticate', 'keep-alive', 'upgrade', 'te', 'trailer'}
    for line in lines[1:]:
        if not line:
            break
        name, value = line.split(':', 1)
        if not name or any(c not in "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ" for c in name):
            raise ValueError('invalid header')
        if any(ord(c) < 32 and c != '\t' for c in value):
            raise ValueError('invalid header value')
        lower = name.lower()
        if lower in ('content-length', 'transfer-encoding'):
            raise ValueError('request bodies unsupported')
        if lower not in blocked:
            headers.append(line)
    if tunnel:
        return parts.hostname, 443, None
    path = parts.path or '/'
    if parts.query:
        path += '?' + parts.query
    headers.extend([f'Host: {parts.hostname}', 'Connection: close'])
    data = f'{method} {path} HTTP/1.1\r\n' + '\r\n'.join(headers) + '\r\n\r\n'
    return parts.hostname, 80, data.encode('ascii')


async def relay(reader, writer):
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()


async def handle(reader, writer):
    upstream = None
    established = False
    try:
        async with asyncio.timeout(60):
            raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout=10)
            host, port, data = parse_request(raw)
            remote, upstream = await asyncio.wait_for(connect_public(host, port), timeout=15)
            if data is None:
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                await writer.drain()
            else:
                upstream.write(data)
                await upstream.drain()
            established = True
            if data is not None:
                # No downstream body or second request can be forwarded/smuggled.
                await relay(remote, writer)
            else:
                tasks = [asyncio.create_task(relay(reader, upstream)),
                         asyncio.create_task(relay(remote, writer))]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
    except (ValueError, OSError, UnicodeError, asyncio.TimeoutError, asyncio.IncompleteReadError,
            asyncio.LimitOverrunError):
        if not established:
            with contextlib.suppress(OSError):
                writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                await writer.drain()
    finally:
        for stream in (upstream, writer):
            if stream:
                stream.close()
                with contextlib.suppress(OSError):
                    await stream.wait_closed()


async def main():
    semaphore = asyncio.Semaphore(128)
    async def bounded(reader, writer):
        if semaphore.locked():
            writer.close()
            return
        async with semaphore:
            await handle(reader, writer)
    server = await asyncio.start_server(bounded, '0.0.0.0', 8080, limit=16384)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main())
