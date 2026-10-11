# GDW native operator presentation

The actual source-owned operator pages are `app/static/calibration-console.html`
at `/calibration` and `app/static/master-suite.html` at `/master-suite`.
The existing root JSON landing, service APIs, governance, numerical definitions,
receipt code, source identity and production-witness controls remain unchanged.

## Presentation and state

Both pages share the accepted KANCHAY paper/graphite roles and exact native Orbit
32/16 artwork. The 32px cut renders at 40px with 10px clear space. The static signal
study is original decorative vector work, visibly labelled illustrative; it is
not an operational chart, kernel mark, or measured trace. The earlier unexplained
18% calibration bar is removed.

The scorecard labels now match their existing source denominators. The field
`runtime_transitions_with_valid_signed_manifest_pct` is a percentage of **valid,
unexpired manifests** in `MasterSuiteService.dashboard_state`, not a measured
percentage of runtime acknowledgements. The first two metrics are already
percentages; rejection and abstention are fractions converted only for display.
No service arithmetic is changed. The service's returned empty-denominator zero
is disclosed separately from unavailable data.

Entry requests no operator data and opens no credential prompt. Read state is an
explicit same-origin GET. Optional 15-second refresh starts only on selection,
pauses while the document is hidden, and stops after a failed read. Requests have
a six-second deadline, a 256KiB decoded-response limit, fixed destinations, and
redirect rejection. Partial or malformed fields remain unavailable. Failed reads
clear old data; missing booleans never become BLOCKED or IDLE, and a failed alert
read never becomes an empty list. Returned alert counts describe the response,
not an assertion that all active alerts were retrieved.
Missing or invalid generation times explicitly clear the previous response's
timestamp; the local read time remains a separate observation.

Connect session is an explicit native dialog. A token remains in page memory,
never browser storage or the URL. Only the fixed operator status endpoints may
receive it. Clear session and page unload remove it. Build details are a separate
unauthenticated, bounded GET requested on disclosure/retry; their source commit
link requires a nonzero lowercase 40-character hex revision on the fixed GitHub
repository. The declaration is not independently authenticated by the page.

The source-defined MODELED / NOT_ASSESSED / unavailable broker and live-authority
limits remain visible before any read. Modeled HMAC authentication does not become
independent public-key approval, compliance, market access, or a production
readiness claim through the visual system.

## Asset and delivery closure

`app/presentation.py` exposes exactly seven public asset paths. It accepts GET and
HEAD only, fixes MIME types, and opens each bounded regular file using the same
descriptor after no-follow checks for both parent directories and the file.
Request names select fixed source-owned filename/MIME pairs. Each descriptor has
its own finally scope, so a failed child open, read or close still releases its
open parents; failure-injection tests exercise these paths.
Unknown paths do not become filesystem reads. Existing startup/source validation
still runs before these routes; existing operator paths remain bearer-protected.
Only the seven literal static asset paths join the public-shell exemption set.

`PROJECTION.json` records the installed subset separately from the unchanged
upstream `SOURCE.json`. Tokens bind the accepted bundle revision
`4309f6fce89179331b0b3ed8c99494e76c5be256`, source
`27d12e77fc0f9f1a0e5fae8b34adf2380d51d070`. Native icon bytes bind
`fd62fe5856ed8e5f660eaae5a9c52957cd71b0a7`. The projection is unsigned local source
metadata, not a GDW cryptographic receipt.

The existing Dockerfile copies the complete `app` tree. The existing Hatch wheel
includes `app` and `gdw_x`; `ci/check_native_assets.py --wheel ...` checks the actual
wheel's two pages, reader module and all seven assets against source bytes. The
canonical artifact-weight generator additionally includes the new native asset
subtree and browser checker. No existing limit or gate is removed.

The captured historical Hugging Face projection has the same two HTML blobs but
different Docker/API/source-identity bytes. Its earlier deployment log describes
manual uploads. This source PR neither establishes a complete current publisher
binding nor authorizes a new Hugging Face writer. Source acceptance, a separately
qualified publication path, provider readback and live acceptance remain distinct.

## Qualification

`Native Frontend` checks out the exact candidate, builds and checks its wheel, and
renders the installed package on loopback. Its Python child receives a clean
environment, disposable data/log directories and an explicit fixture token;
OpenTelemetry exporters are disabled. Browser transport permits the one local
origin and GET only. Fixtures exercise valid, absent, malformed, oversized,
partial, hostile-text and authentication states. One additional browser check
reads the real disposable local service with its explicit fixture credential.
No operational POST, provider probe, production witness, external credential or
publisher is used.

The suite covers six widths from 320 to 2560px, both themes, visible text contrast,
44px controls, document overflow, keyboard dialog containment/restoration,
source-declaration recovery, reduced motion, forced colors and no-JavaScript
recovery. It checks the actual served bytes from the installed package before
rendering. These are bounded rendered fixtures, not a complete assistive-technology
certification or live deployment proof. Browser request counters do not claim to
instrument every Python-process socket.

Each run creates a fresh private directory below `GDW_QA_OUT` (or the platform
temporary directory). Metadata writes use exclusive creation and mode 0600;
screenshots and reports stay within that private directory. Only unsigned
observation files and screenshots are uploaded by the contents-read workflow.

Local commands after installing the existing project test dependencies and
Playwright:

```sh
python ci/check_native_assets.py
pytest -q tests/test_native_presentation.py
python ci/update_weight_manifest.py --write
python ci/gdw_gate.py
node ci/qualify_native_frontend.mjs
```

`GDW_QA_PYTHON_PATH` optionally selects an isolated installed-package directory;
the browser server runs outside the checkout so it exercises that installed
package. The source gates and Docker/production witness workflows retain their
separate authority and continue to apply.
