# Isolated Stealth Crawls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Enable undetected JavaScript crawling inside a public-egress-only browser container and make robots enforcement configurable.

**Architecture:** Keep queue processing and persistence in the existing worker. Put Crawl4AI browser execution behind an internal endpoint, with internet access exclusively through a validating, address-pinning proxy. Never enable JavaScript in the database-connected worker.

**Tech Stack:** Python, Crawl4AI/Patchright, HTTPX, Podman Compose, pytest.

**Spec:** docs/superpowers/specs/2026-11-09-isolated-stealth-crawls-design.md

## Global Constraints
Preserve public URL validation, requested-domain restrictions, request spacing, paywall detection and queue leases. Robots enforcement defaults false. Browser has no application credentials or unrestricted egress. Keep local guarded crawling available with JavaScript disabled.

## Review Focus
- Redirects and browser navigation retain domain and public-address checks with robots disabled.
- DNS rebinding and mixed IPv4/IPv6 answers cannot reach private networks.
- Browser startup failure produces a crawl error rather than an unsafe local fallback.
- Scripts and service workers cannot bypass the egress gateway.
- Strict robots mode still handles unavailable robots files conservatively.

### Task 1: Explicit robots policy
Files: crawl/fetch.py, crawl/worker.py, crawl/cli.py under packages/providers/src/weave_providers; packages/providers/tests/test_crawl.py.
Interface: PoliteFetcher(..., respect_robots: bool = False); worker and CLI consume WEAVE_CRAWL_RESPECT_ROBOTS consistently.
- [x] Add failing tests: disabled mode fetches disallowed articles without robots requests, strict mode rejects them, both reject private targets and out-of-domain redirects.
- [x] Run focused pytest and confirm expected failures.
- [x] Implement policy without bypassing validator/domain checks; validate environment values.
- [x] Run crawler tests, including strict-mode robots failure tests.

### Task 2: Enforced browser egress
Files: new deploy/crawl gateway and browser service modules/Dockerfiles; compose.yaml; new gateway tests.
Interface: internal browser endpoint consumed by worker; explicit proxy endpoint used by browser; no host ports.
- [x] Add failing gateway tests for loopback, metadata, private IPv4/IPv6, mixed DNS answers, CONNECT destinations, malformed authorities and resolution-to-connection pinning.
- [x] Implement a restricted forwarding gateway that connects only to the resolved and validated address; deny unsupported tunneling destinations/protocols.
- [x] Create browser service using UndetectedAdapter; bounded requests/timeouts, isolated contexts, no arbitrary Python or hook execution from callers.
- [x] Attach browser exclusively to an internal network; gateway alone has external routing. Keep worker database access outside browser container.
- [x] Run gateway tests and validate resolved Compose configuration.

### Task 3: Worker integration and verification
Files: crawl/fetch.py, crawl/worker.py; packages/providers/tests/test_crawl.py and test_crawl_worker.py; docs/crawl-security.md; compose.yaml.
Interface: isolated fetcher returns existing Fetched results and preserves cancellation/error behavior; local fetcher retains JS-disabled guarding.
- [x] Add failing tests for isolated rendering results, failed browser startup without local fallback, cancellation and redirect/domain rejection.
- [x] Integrate isolated fetcher, enable JavaScript only remotely, preserve existing metadata transport and progress handling.
- [x] Run provider suite and repository pytest command; report all failures.
- [ ] Fresh image build: blocked by Podman image storage exhaustion. Existing-image/source-mounted internal topology passed public HTTPS, JavaScript rendering, private-target denial and direct-egress denial smoke checks.
- [x] Update security/deployment documentation with configuration and verified boundaries; inspect diff before completion.

## Execution results

- Provider suite: 178 passed, 15 skipped.
- Other packages: API 85, clusterer 56, core 122, enrichers 96, eval 96,
  extractors 42, inference 109 passed. Core reported one warning.
- Bare pytest: 14 pre-existing collection collisions in core/test_runs;
  enrichers/test_base, test_clairic, test_nuextract, test_pipeline, test_report,
  test_system_one, test_window; eval/test_evaluate, test_gold, test_tracks;
  extractors/test_pipeline, test_shape, test_system_one. Separate package runs passed.
- Compose configuration, compileall and git diff whitespace checks passed.
- Runtime smoke passed using existing image plus read-only source mount.
- Fresh Dockerfile build remains unverified: Podman store has no space left.
- Fresh reviewer found no Critical issues. Worker config issue regraded Important,
  fixed with failing-then-passing regression and final green provider suite.
- Deferred minor: unrelated-domain RPC requests return busy (503) instead of invalid
  domain (400) while the render slot is occupied; they perform no navigation.
