# Credential-backed single-node deployment

This runbook turns the modeled GDW stack into a live, authenticated deployment on one Docker host. It proves exact source and image identity, HTTPS ingress, fail-closed operator authentication, a healthy observability stack, file-backed P-256 receipt signing, signed local backup/restore, and receipt-ledger recovery across an application-container restart.

It does **not** prove high availability, multi-node durability, tenant isolation, production-scale capacity, off-host disaster recovery, hardware-backed signing, or independent receipt witnessing. Those remain `UNVERIFIED` or `UNAVAILABLE` until separately measured.

## 1. Prepare exact source and secrets

Use a protected, settled default-branch commit. Copy
`deployment/.env.live.example` to an ignored file outside the repository.
Generate independent random secret files for the API token and Grafana
password, a distinct Master Suite HMAC key, then create the persistent P-256
receipt-signing key:

```bash
openssl rand -base64 -out /secure/gdw-api-token 48
openssl rand -base64 -out /secure/gdw-grafana-password 48
openssl rand -base64 -out /secure/gdw-master-suite-signing-key 48
python ci/generate_signing_key.py \
  --output /secure/gdw-receipt-signing-key.pem
```

The generator refuses to overwrite an existing key. Restrict all four files
and the environment file to the deployment account, and never pass a secret
value on a command line.

Compose sources all four file paths from the deployment environment and mounts the
files read-only. The secret values are absent from `docker inspect`
environment metadata. The
application gives `GDW_API_TOKEN_FILE` precedence over the legacy direct
environment variable, and the Master Suite reads its HMAC key from the mounted
file. A one-shot password-sync container applies the mounted
Grafana secret to new and existing Grafana databases before Terraform
provisioning runs.

For a new signed ledger or an existing non-empty unsigned receipt ledger, set
`GDW_ALLOW_UNSIGNED_LEDGER_MIGRATION=1` for the first signed startup only.
After readiness confirms a signed head and continuity state, set it back to
`0` and recreate the application container. Leaving it at `0` after
initialization prevents deleted signed state from being silently replaced.
The live verifier refuses to certify a deployment while this authorization
remains enabled.

On Linux, assign all four files to the deployment account and a dedicated numeric
group, set mode `0440`, and set `GDW_SECRETS_GID` to that group ID. Compose adds
that supplemental group to each container that must read a secret. On Windows,
use an ACL limited to the deployment account and `SYSTEM`.

Keep `GDW_WORKERS=1`. Master Suite state is process-local, and application
lifespan also holds an exclusive lease on its shared data root to reject an
external multi-worker ASGI launch before concurrent journal writes occur.

Build and capture the immutable Docker image ID:

```bash
test "$(git rev-parse HEAD)" = "${SOURCE_SHA}"
test -z "$(git status --porcelain)"
docker build --pull \
  --build-arg "GDW_SOURCE_SHA=${SOURCE_SHA}" \
  -t "gdw-frontier:${SOURCE_SHA}" .
docker image inspect "gdw-frontier:${SOURCE_SHA}" --format '{{.Id}}'
```

Set `GDW_SOURCE_SHA`, `GDW_IMAGE_REF`, and `GDW_IMAGE_DIGEST` in the protected environment file to those exact observed values. The live verifier independently requires the clean checkout, the image's OCI revision label, the running container image ID, the source-tree digest recomputed inside that container, and `/build-info` to agree.

## 2. Start and verify the complete stack

```bash
docker compose \
  --env-file /secure/gdw-live.env \
  -f monitoring/docker-compose.yml \
  up -d --no-build

python ci/verify_stack.py \
  --app-url http://127.0.0.1:8080 \
  --grafana-url http://127.0.0.1:3300 \
  --api-token-file /secure/gdw-api-token \
  --grafana-password-file /secure/gdw-grafana-password
```

All published ports default to `127.0.0.1`. Grafana anonymous access defaults to disabled. The application refuses to start in authenticated mode if its bearer token is shorter than 32 characters.

## 3. Configure least-exposed HTTPS ingress

Create a dedicated named tunnel and DNS hostname. Do not reuse a tunnel owned by another service. Copy `deployment/cloudflared-config.example.yml` to a protected location, fill in the dedicated tunnel UUID, credential path, and hostname, then validate it:

```bash
cloudflared tunnel --config /secure/gdw-cloudflared.yml ingress validate
cloudflared tunnel --config /secure/gdw-cloudflared.yml run
```

The example denies `/metrics` at the public edge. Health, readiness, and build identity remain public; operator endpoints require the bearer token.

## 4. Mint the restart-durability receipt

Export the API token only into the verifier process and write the proof outside version control:

```bash
python ci/verify_live_deployment.py \
  --public-url "https://gdw.example.com" \
  --expected-source "${SOURCE_SHA}" \
  --expected-image "${IMAGE_DIGEST}" \
  --api-token-file /secure/gdw-api-token \
  --env-file /secure/gdw-live.env
```

The verifier checks TLS through the system trust store, rejects public
`/metrics` unless the edge returns the configured `404`, cross-checks the
clean checkout, image revision label, independently recomputed in-container
source-tree digest, and `/build-info`, and requires a `401 Bearer` challenge
without credentials. It then verifies the signed receipt head offline,
restores a signed backup from the separate backup volume into isolated tmpfs,
checks ledger recovery across `docker compose restart app`, and checks the
container's health, read-only root, dropped capabilities, and restart policy.
It never writes the bearer token or private signing key into a proof artifact.

## 5. Measure bounded write load

Run a declared request count and concurrency against the authenticated HTTPS
surface:

```bash
python ci/measure_load.py \
  --public-url "https://gdw.example.com" \
  --api-token-file /secure/gdw-api-token \
  --requests 500 \
  --concurrency 16 \
  --output output/proofs/load-measurement.json
```

The artifact verifies unique signed receipts and the exact ledger delta. It is
one bounded single-node measurement, not a universal capacity or HA claim.

## 6. Capture an independent read-only witness

Before storing any production value, configure the `production` environment's
deployment branch and tag rule to admit only the protected `main` branch. Do
not leave the environment unrestricted: any workflow job that names an
unrestricted environment can otherwise request its secrets, including from
pull-request-controlled repository code. Keep any intended required-reviewer
gate enabled as a separate approval boundary, and verify both rules in the
environment settings after every protection change.

Configure the `production` GitHub environment with the protected
`GDW_PRODUCTION_API_TOKEN` secret and the `GDW_PRODUCTION_URL`,
`GDW_PRODUCTION_SOURCE_SHA`, and `GDW_PRODUCTION_IMAGE_DIGEST` variables.
After the exact deployed identity has been verified locally, dispatch the
trusted default-branch witness event:

```bash
gh api --method POST \
  repos/szl-holdings/gdw-frontier/dispatches \
  -f event_type=gdw-production-witness
```

The production witness deliberately uses `repository_dispatch` rather than
`workflow_dispatch`. GitHub binds this event to the current default-branch
commit, so a caller cannot select a feature branch or tag whose repository code
would receive the production credential. The workflow independently checks the
repository, default-branch ref, checked-out commit, and clean checkout before
materializing that credential. Do not add `client_payload` fields: the workflow
consumes no client-supplied source, image, URL, ref, or payload; those values
remain protected environment variables. An HTTP `204` from the dispatch API
means only that GitHub accepted the event. It is not witness success; retain and
verify the resulting exact-SHA Actions run and proof artifact.

The hosted verifier checks public health, readiness, ingress denial for
`/metrics`, fail-closed operator authentication, exact source/image identity,
and the current signed ledger head. It verifies the P-256 signature offline
without writing a production record, then retains the proof as a run-scoped
GitHub artifact. This is an independent network witness; it does not prove
high availability, multi-node durability, hardware-backed signing, or
off-host disaster recovery.

## Rollback

Stop the dedicated tunnel process first, then stop only the GDW Compose project:

```bash
docker compose --env-file /secure/gdw-live.env -f monitoring/docker-compose.yml down
```

Do not use `--volumes` during a normal rollback because that deletes the local
receipt, backup, and telemetry volumes.
