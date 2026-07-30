# Credential-backed single-node deployment

This runbook turns the modeled GDW stack into a live, authenticated deployment on one Docker host. It proves exact source and image identity, HTTPS ingress, fail-closed operator authentication, a healthy observability stack, and receipt-ledger recovery across an application-container restart.

It does **not** prove high availability, multi-node durability, tenant isolation, production-scale capacity, or cryptographic receipt signing. Those remain `UNVERIFIED` or `UNAVAILABLE` until separately measured.

## 1. Prepare exact source and secrets

Use a protected, settled default-branch commit. Copy `deployment/.env.live.example` to an ignored file outside the repository. Generate independent random secret files for the API token and Grafana password, restrict both files and the environment file to the deployment account, and never pass a secret value on a command line.

Compose sources both file paths from the deployment environment and mounts the
files read-only. The secret values are absent from `docker inspect`
environment metadata. The
application gives `GDW_API_TOKEN_FILE` precedence over the legacy direct
environment variable. A one-shot password-sync container applies the mounted
Grafana secret to new and existing Grafana databases before Terraform
provisioning runs.

On Linux, assign both files to the deployment account and a dedicated numeric
group, set mode `0440`, and set `GDW_SECRETS_GID` to that group ID. Compose adds
that supplemental group to each container that must read a secret. On Windows,
use an ACL limited to the deployment account and `SYSTEM`.

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

The verifier checks TLS through the system trust store, exact `/build-info` identity, a `401 Bearer` challenge without credentials, authenticated receipt creation, ledger recovery across `docker compose restart app`, and the container's health, read-only root, dropped capabilities, and restart policy. It never writes the bearer token into the receipt.

## Rollback

Stop the dedicated tunnel process first, then stop only the GDW Compose project:

```bash
docker compose --env-file /secure/gdw-live.env -f monitoring/docker-compose.yml down
```

Do not use `--volumes` during a normal rollback because that deletes the local receipt and telemetry volumes.
