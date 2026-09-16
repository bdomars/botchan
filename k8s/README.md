# Kubernetes

A sample deployment to build your own on, not a turnkey install.

- **`base/`** — the bot, the API, its Service, and a ConfigMap. Reference this from your own kustomization.
- **`example/`** — a complete deployment built on the base: CloudNativePG, a generated secret, an Ingress. Copy it and edit.

The base deliberately has **no database, no Ingress, no Secret and no namespace**. Those belong to your kustomization.

## Quick start

```yaml
# kustomization.yaml
resources:
  - github.com/bdomars/botchan//k8s/base?ref=main
namespace: botchan

secretGenerator:
  - name: botchan-discord
    envs:
      - discord.env
```

```bash
kubectl create namespace botchan
kubectl apply -k .
```

The pods stay in `CreateContainerConfigError` until both secrets below exist.

## The two secrets

**`botchan-discord`** holds the Discord credentials and the API's signing keys. Its keys are used as environment variables directly, so they must be named exactly:

| Key | How to get it |
| --- | --- |
| `DISCORD_CLIENT_ID` | Discord Developer Portal, OAuth2 |
| `DISCORD_CLIENT_SECRET` | Discord Developer Portal, OAuth2 |
| `DISCORD_TOKEN` | Discord Developer Portal, Bot |
| `SESSION_SECRET` | `openssl rand -base64 32` |
| `TOKEN_ENCRYPTION_KEY` | `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |

**`botchan-db-app`** holds the database connection, in the shape CloudNativePG uses: `username`, `password`, `host`, `port` and `dbname`. The deployments build `DATABASE_URL` from those five keys.

A CNPG `Cluster` named `botchan-db` creates a secret called `botchan-db-app` with exactly these keys, so **with CNPG there's nothing to wire up**. See `example/cnpg-cluster.yaml`. Older CNPG versions only put `username` and `password` in that secret. If yours does, add the other three keys yourself or point the deployments at your own secret.

`example/secrets.example.yaml` has templates for both. Keep real values out of git: use `secretGenerator` with a local env file, SOPS, or External Secrets. The `example/discord.env` in this repo holds placeholders only, so copy the example directory somewhere else before putting real credentials in it.

### Pointing at a secret with another name

The five database variables come first in every container, in the same order, so one patch covers both Deployments. The API's migration init container needs the same treatment:

```yaml
patches:
  - target:
      kind: Deployment
    patch: |
      - op: replace
        path: /spec/template/spec/containers/0/env/0/valueFrom/secretKeyRef/name
        value: my-postgres-secret   # DB_USER
      - op: replace
        path: /spec/template/spec/containers/0/env/1/valueFrom/secretKeyRef/name
        value: my-postgres-secret   # DB_PASSWORD
      - op: replace
        path: /spec/template/spec/containers/0/env/2/valueFrom/secretKeyRef/name
        value: my-postgres-secret   # DB_HOST
      - op: replace
        path: /spec/template/spec/containers/0/env/3/valueFrom/secretKeyRef/name
        value: my-postgres-secret   # DB_PORT
      - op: replace
        path: /spec/template/spec/containers/0/env/4/valueFrom/secretKeyRef/name
        value: my-postgres-secret   # DB_NAME
  - target:
      kind: Deployment
      name: botchan-api
    patch: |
      - op: replace
        path: /spec/template/spec/initContainers/0/env/0/valueFrom/secretKeyRef/name
        value: my-postgres-secret
      # ...and the same for env entries 1 to 4
```

If the key names differ too, replace `/key` on the same paths.

### Using a ready-made DATABASE_URL

If you already keep a whole URL in one secret key, replace the composed variable, which is env entry 5 in every container:

```yaml
patches:
  - target:
      kind: Deployment
    patch: |
      - op: replace
        path: /spec/template/spec/containers/0/env/5
        value:
          name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: my-postgres-secret
              key: url
```

The five `DB_*` variables are then unused, and can stay. The URL must use the `postgresql+asyncpg://` scheme. CNPG's own `uri` key doesn't: it says `postgresql://`, which the API's SQLAlchemy engine rejects.

## Settings

`base/configmap.yaml` carries the non-secret settings, with localhost defaults. Point them at your hostname, as `example/kustomization.yaml` does:

| Key | Notes |
| --- | --- |
| `PUBLIC_BASE_URL` | Where the web UI is reached |
| `DISCORD_REDIRECT_URI` | Must match the redirect URI registered with Discord |
| `SECURE_COOKIES` | Keep `"true"` when served over HTTPS |
| `LOG_LEVEL` | Bot log level |

## Images

```yaml
images:
  - name: ghcr.io/bdomars/botchan
    newTag: sha-1a2b3c4
  - name: ghcr.io/bdomars/botchan-api
    newTag: sha-1a2b3c4
```

Both images are published by CI on every push to `main`, tagged `latest` and `sha-<commit>`: `ghcr.io/bdomars/botchan` from `Dockerfile`, and `ghcr.io/bdomars/botchan-api` from `Dockerfile.api`. Pin a `sha-` tag in production, so a rollout is something you choose rather than something `latest` does to you.

## Things worth knowing

- **Keep the bot at one replica.** Two bots on one Discord token would both manage the same voice channels and fight over creating and deleting them. The bot Deployment uses the `Recreate` strategy for the same reason, so a rollout never has two running at once.
- **Migrations run as an init container** on the API, on every rollout, so the API never serves against an old schema. Alembic does nothing when the database is already current.
- **Passwords aren't URL-encoded.** `DATABASE_URL` is assembled by string substitution, so a password containing `@ : / ? # %` will break it. CNPG-generated passwords are alphanumeric. Note that a `%` also breaks Alembic, which reads the URL through a config parser.
- **The API's probes request `/`**, which is the static web UI and needs no login. They check that the server responds, not that the database is reachable.
- **Neither service needs Kubernetes API access**, so both run with `automountServiceAccountToken: false`, a read-only root filesystem, no added capabilities and as a non-root user.
- **Only memory has a limit.** Throttling the bot's CPU causes gateway heartbeat timeouts and reconnects.
