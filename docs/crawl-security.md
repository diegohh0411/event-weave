# Crawl isolation and network safety

Compose runs the queue worker separately from `crawl-browser`. The browser uses
Crawl4AI 0.9.5's Patchright `UndetectedAdapter`, its normal browser user agent,
JavaScript and `magic` interaction settings. These improve compatibility; they do
not guarantee that a site will accept a crawl. Paywall detection remains enabled.

`WEAVE_CRAWL_RESPECT_ROBOTS=false` is the CLI/worker default. Set it to `true`
before `podman compose up` to enforce robots rules for discovery, articles and
browser navigation. Invalid values fail with an explicit error. Request spacing
remains two seconds per domain. Direct `PoliteFetcher` library callers retain
strict robots behavior unless they explicitly pass `respect_robots=False`.
Robots sitemap declarations may still be fetched as discovery hints in either mode.

## Network boundary

The browser has only an internal Compose network, no published ports, credentials,
application mounts or container-engine socket. It runs as UID 10001 with a read-only
filesystem, temporary browser state, dropped capabilities and resource limits.
The queue worker shares the internal network to call the narrow `/fetch` endpoint;
it retains database access. The browser service accepts only a URL, domain and
robots boolean, with no caller-provided scripts, browser flags or hooks. It creates
a fresh browser per request and permits one rendering request at a time.

`crawl-egress` alone connects this network to the internet. Its HTTP forward proxy
permits GET/HEAD to port 80 and CONNECT to port 443. It resolves each destination
once, rejects an empty answer set or any non-global/multicast address (including
mixed public/private and IPv6 transition answers), and dials the checked numeric
address. It does not forward client HTTP bodies or subsequent pipelined requests.
Connections and headers have time/size limits. The gateway has no application
network or database credentials. Chromium uses this proxy with implicit loopback
bypass disabled; QUIC and unproxied WebRTC UDP are disabled as well.

The worker routes metadata/robots requests through the same gateway. Public URL
validation, redirect/domain guards, browser subrequest guards and WebSocket
blocking remain in place. Internal Podman DNS does not resolve public hostnames:
the browser service checks URL syntax locally and delegates DNS/address enforcement
to the gateway. Service workers cannot obtain unrestricted network egress; the
internal network and gateway remain the boundary even if interception is bypassed.
Do not attach the browser to an external/application network or expose its RPC port.

## Local operation

Without `WEAVE_CRAWL_BROWSER_URL`, CLI/worker use a local guarded browser with
JavaScript disabled. There is no automatic local fallback when a configured remote
browser fails. Set `WEAVE_CRAWL_BROWSER_URL` and `WEAVE_CRAWL_EGRESS_PROXY` together
when deploying outside Compose. The caller is responsible for reproducing the
internal-network topology; setting environment variables alone is not isolation.

Local HTTPX/Playwright URL checks resolve again at connection time and therefore
are not a DNS-rebinding boundary without the gateway. Local execution is suitable
for trusted crawl targets; use the isolated deployment for public/untrusted jobs.

## Verification on 2026-11-09

Provider tests cover robots modes, spacing, redirects, validation, remote failures,
cancellation, RPC input restrictions, private/mixed/transition DNS answers and
numeric-address connection pinning. The final provider suite passed 178 tests (15 skipped), including multicast
rejection and fail-fast worker configuration regressions. The other seven package
suites passed 606 tests when invoked separately (one existing core warning).
The bare repository pytest invocation cannot collect these suites together because
test module/helper names collide; separate package runs avoid those collisions.

With the existing Crawl4AI image and the new source mounted read-only, a Podman
internal-network browser fetched public HTTPS and rendered the JavaScript-only
quotes.toscrape.com page. Direct public TCP failed. Proxy requests to metadata,
loopback, private and container DNS targets returned 403. This verifies the network
and application path, but not a fresh image build: building the updated Dockerfile
was blocked by `no space left on device` in Podman's image store. No unrelated
images were removed. Rebuild with `podman compose build crawl-browser crawl-egress
crawler` after making space, then start normally with `podman compose up`.

Repeat the runtime check with `podman compose exec -T crawl-browser python < deploy/crawl/smoke.py`.
