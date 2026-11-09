"""The proxy must validate all DNS answers and dial the checked numeric address."""
import asyncio

import pytest


def test_proxy_rejects_private_mixed_and_transition_answers_before_connecting():
    from weave_providers.crawl.egress import connect_public
    for answers in (['127.0.0.1'], ['169.254.169.254'], ['10.0.0.1'], ['::1'],
                    ['fc00::1'], ['8.8.8.8', '192.168.0.1'], ['::ffff:127.0.0.1'],
                    ['64:ff9b::a00:1'], []):
        async def resolve(host, port):
            return answers
        async def connect(host, port):
            pytest.fail('unsafe destination reached connector')
        with pytest.raises(ValueError):
            asyncio.run(connect_public('public.example', 443, resolve=resolve, connect=connect))


def test_proxy_connects_to_validated_ip_without_resolving_hostname_again():
    from weave_providers.crawl.egress import connect_public
    async def resolve(host, port):
        assert (host, port) == ('public.example', 443)
        return ['93.184.215.14']
    async def connect(host, port):
        assert (host, port) == ('93.184.215.14', 443)
        return 'connected'
    assert asyncio.run(connect_public('public.example', 443, resolve=resolve,
                                      connect=connect)) == 'connected'


@pytest.mark.parametrize('raw_request', [
    b'CONNECT localhost:443 HTTP/1.1\r\n\r\n',
    b'CONNECT public.example:22 HTTP/1.1\r\n\r\n',
    b'CONNECT user@public.example:443 HTTP/1.1\r\n\r\n',
    b'GET http://127.0.0.1/ HTTP/1.1\r\n\r\n',
    b'GET http://public.example:8080/ HTTP/1.1\r\n\r\n',
    b'POST http://public.example/ HTTP/1.1\r\nContent-Length: 2\r\n\r\nxx',
    b'GET http://public.example/ HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n',
])
def test_proxy_rejects_unsupported_or_unsafe_requests(raw_request):
    from weave_providers.crawl.egress import parse_request
    with pytest.raises(ValueError):
        parse_request(raw_request)


def test_proxy_rewrites_http_authority_and_closes_connection():
    from weave_providers.crawl.egress import parse_request
    host, port, data = parse_request(
        b'GET http://public.example/a?q=1 HTTP/1.1\r\nHost: internal\r\n'
        b'Proxy-Authorization: secret\r\nConnection: keep-alive\r\n\r\n')
    assert (host, port) == ('public.example', 80)
    assert data.startswith(b'GET /a?q=1 HTTP/1.1\r\n')
    assert b'Host: public.example\r\n' in data
    assert b'Connection: close\r\n' in data
    assert b'internal' not in data and b'secret' not in data


def test_proxy_rejects_multicast_destinations():
    from weave_providers.crawl.egress import connect_public
    async def resolve(host, port):
        return ['224.0.0.1']
    async def connect(host, port):
        return 'unexpected connection'
    with pytest.raises(ValueError):
        asyncio.run(connect_public('public.example', 443, resolve=resolve, connect=connect))
