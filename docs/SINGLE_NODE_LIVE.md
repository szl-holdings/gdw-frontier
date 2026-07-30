# Credential-backed single-node deployment

This runbook turns the modeled GDW stack into a live, authenticated deployment on one Docker host. It proves exact source and image identity, HTTPS ingress, fail-closed operator authentication, a healthy observability stack, and receipt-ledger recovery across an application-container restart.

It does **not** prove high availability, multi-node durability, tenant isolation, production-scale capacity, or cryptographic receipt signing. Those remain `UNVERIFIED` or `UNAVAILABLE` until separately measured.

## 1. Prepare exact source and secrets

Use a protected, settled default-branch commit. Copy `deployment/.env.live.example` to an ignored file outside the repository. Generate independent random values for `GDW_API_TOKEN` and `GRAFANA_ADMIN_PASSWORD`, restrict that file to the deployment account, and never pass the token on a command line.

Build and capture the immutable Docker image ID:

```bash
docker build --pull -t "gdw-frontier:${SOURCE_SHA}" .
docker image inspect "gdw-frontier:${SOURCE_SHA}" --format '{{.Id}}'
```

Set `GDW_SOURCE_SHA`, `GDW_IMAGE_REF`, and `GDW_IMAGE_DIGEST` in the protected environment file to those exact observed values.

## 2. Start and verify the complete stack

```bash
docker compose \
  --env-file /secure/gdw-live.env \
  -f monitoring/docker-compose.yml \
  up -d --no-build

GDW_API_TOKEN="${GDW_API_TOKEN}" python ci/verify_stack.py \
  --app-url http://127.0.0.1:8080 \
  --grafana-url http://127.0.0.1:3300
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
GDW_API_TOKEN="${GDW_API_TOKEN}" python ci/verify_live_deployment.py \
  --public-url "https://gdw.example.com" \
  --expected-source "${SOURCE_SHA}" \
  --expected-image "${IMAGE_DIGEST}" \
  --env-file /secure/gdw-live.env
```

The verifier checks TLS through the system trust store, exact `/build-info` identity, a `401 Bearer` challenge without credentials, authenticated receipt creation, ledger recovery across `docker compose restart app`, and the container's health, read-only root, dropped capabilities, and restart policy. It never writes the bearer token into the receipt.

## Rollback

Stop the dedicated tunnel process first, then stop only the GDW Compose project:

```bash
docker compose --env-file /secure/gdw-live.env -f monitoring/docker-compose.yml down
```

Do not use `--volumes` during a normal rollback because that deletes the local receipt and telemetry volumes.
