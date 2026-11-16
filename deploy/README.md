# Deployment (homelab k3s + Argo CD)

The app runs at **https://zeit.slin.io** in the namespace `arbeitszeit`, with its data in the shared
PostgreSQL (`postgres.database.svc.cluster.local`) and mail through the central relay.

```
deploy/
  argocd/application.yaml        Argo CD Application, applied once by hand
  k8s/base/                      Deployment, Service, ConfigMap, InfisicalSecret
  k8s/overlays/homelab/          IngressRoute for zeit.slin.io, AZ_BASE_URL, image tag (set by CI)
```

## How a change gets deployed

1. A push to `main` that touches `src/`, `tests/`, `pyproject.toml` or the `Dockerfile` runs
   `.github/workflows/images.yml`: tests on SQLite and PostgreSQL, then the image
   `ghcr.io/slin86/arbeitszeit:sha-<7>` (and `:latest`) is built and pushed.
2. The workflow sets that tag in `deploy/k8s/overlays/homelab/kustomization.yaml` and pushes the
   commit to `main` (`[skip ci]`).
3. Argo CD syncs and the pod is replaced (`Recreate`). The app creates missing tables and columns
   at start; there are no separate migrations.

Do not put comments into `overlays/homelab/kustomization.yaml`: `kustomize edit` rewrites the file
on every deploy. Settings of this installation go into `overlays/homelab/config.yaml`.

## First setup

### 1. Database

Create a role and a database in the shared PostgreSQL (as the superuser). Use a password without
`@`, `:` or `/`, because it goes into a URL.

```bash
PW=$(openssl rand -hex 24)
kubectl -n database exec -i postgres-0 -- psql -U postgres -v ON_ERROR_STOP=1 \
  -c "CREATE ROLE arbeitszeit LOGIN PASSWORD '$PW';" \
  -c "CREATE DATABASE arbeitszeit OWNER arbeitszeit;"
echo "postgresql+psycopg://arbeitszeit:$PW@postgres.database.svc.cluster.local:5432/arbeitszeit"
```

Check the password over the network, not through `localhost` (the stock image trusts local
connections, so that test passes with any password):

```bash
kubectl -n database exec -i postgres-0 -- env PGPASSWORD="$PW" sh -c \
  'psql -h "$(hostname -i)" -U arbeitszeit -d arbeitszeit -c "SELECT 1;"'
```

The nightly `postgres-backup` CronJob (`pg_dumpall`) includes the new database automatically.

### 2. Secrets in Infisical

Project `homelab-ei-fj`, environment `prod`, folder **`/arbeitszeit`**:

| Key | Value |
|---|---|
| `AZ_SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `AZ_DATABASE_URL` | the URL printed in step 1 |
| `AZ_ADMIN_EMAIL` | e-mail address of the first administrator |
| `AZ_ADMIN_PASSWORD` | their initial password; only used while the database has no users yet |

Optional, for sign-in with Microsoft, Google or another OIDC provider (redirect URI
`https://zeit.slin.io/auth/<azure|google|sso>/callback`): `AZ_OIDC_AZURE_TENANT_ID`,
`AZ_OIDC_AZURE_CLIENT_ID`, `AZ_OIDC_AZURE_CLIENT_SECRET`, `AZ_OIDC_GOOGLE_CLIENT_ID`,
`AZ_OIDC_GOOGLE_CLIENT_SECRET`, `AZ_OIDC_GENERIC_*`. See `.env.example`.

The app refuses to start without `AZ_SECRET_KEY`, because `AZ_HTTPS_ONLY` is on.

### 3. DNS

`zeit.slin.io` needs a public record pointing at the home connection, like `kontor.slin.io`
(deSEC). Inside the LAN, point the name at Traefik's LAN address in AdGuard, otherwise it leads to
the Fritzbox. The wildcard certificate `*.slin.io` already covers the name.

### 4. Container image

After the first run of the workflow, open the package `arbeitszeit` under
github.com/slin86 → Packages and check that it is **public**, so the cluster can pull it without
credentials (like the Kontor images).

### 5. Argo CD

```bash
kubectl apply -f deploy/argocd/application.yaml
```

Then sign in at https://zeit.slin.io with the admin from step 2 and change the password.

## Mail

Configured in `k8s/base/configmap.yaml`: `mail-relay.mail.svc.cluster.local:587`, plain SMTP
(`AZ_SMTP_SECURITY=none`), no login, sender `noreply@slin.io`. SES only accepts `@slin.io` senders.
The admin page "E-Mail" can send a test message.
