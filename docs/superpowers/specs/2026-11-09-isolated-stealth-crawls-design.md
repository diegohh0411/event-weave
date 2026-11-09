# Isolated stealth crawls

Approved in conversation on 2026-11-09.

## Intent
Improve article retrieval with Crawl4AI's undetected browser and JavaScript while containing untrusted website code. Make robots enforcement configurable and disabled by default. Keep request spacing, public URL validation, requested-domain restrictions and paywall detection.

## Approaches
Selected: keep the database-connected crawl worker, move browser execution to a separate container with enforced public-only egress. Browser container has no database credentials, application mounts, engine socket or published ports. An internal network connects the worker to its browser endpoint and a restricted egress gateway provides internet access.
Rejected: enabling JavaScript in the existing database-connected worker exposes internal services. Browser request interception alone is insufficient because service workers and DNS rebinding can bypass application checks.

## Behavior and boundaries
Use Crawl4AI UndetectedAdapter rather than stacking incompatible stealth adapters. Browser JavaScript is enabled only through the isolated execution path; local guarded operation retains its conservative behavior. Robots policy is controlled by WEAVE_CRAWL_RESPECT_ROBOTS, default false, and applies consistently to metadata, articles and browser navigation. This policy never disables public address or domain checks. Invalid configuration fails clearly.

Use a validating HTTP CONNECT/HTTP egress gateway that resolves destinations and connects to the validated address, rejecting every non-global IPv4/IPv6 address, including mixed DNS answers. Browser has no direct external network route. No paywall bypass or challenge-solving service is introduced. Preserve existing crawl queue leases and document outcomes.

## Verification
Test policy modes, redirects, validation preservation, gateway DNS/address rejection and connection pinning. Verify JavaScript rendering and rejected private-network access through the actual container topology. Report container-runtime limitations separately from unit test results.
