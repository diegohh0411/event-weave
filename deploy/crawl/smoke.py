"""Run inside the browser container: podman compose exec -T crawl-browser python < deploy/crawl/smoke.py.

Uses public example/JS fixture sites. No changes are made to their content.
"""
import os
import socket

import httpx

proxy = os.environ['WEAVE_CRAWL_EGRESS_PROXY']
with httpx.Client(proxy=proxy, trust_env=False, timeout=15) as client:
    for target in ('http://169.254.169.254/', 'http://127.0.0.1:8081/health',
                   'http://crawl-browser/', 'http://10.0.0.1/'):
        assert client.get(target).status_code == 403, target
        print('proxy denied', target, flush=True)
try:
    connection = socket.create_connection(('1.1.1.1', 443), timeout=3)
except OSError:
    print('direct internet TCP denied', flush=True)
else:
    connection.close()
    raise AssertionError('browser has direct internet access')

for url, domain, marker in (
    ('https://example.com', 'example.com', 'Example Domain'),
    ('https://quotes.toscrape.com/js/', 'quotes.toscrape.com', 'Albert Einstein'),
):
    response = httpx.post('http://127.0.0.1:8081/fetch', json={
        'url': url, 'domain': domain, 'respect_robots': False,
    }, timeout=90, trust_env=False)
    response.raise_for_status()
    result = response.json()
    assert result['status'] == 200 and marker in (result['html'] or ''), result.get('error')
    print('rendered', url, flush=True)
