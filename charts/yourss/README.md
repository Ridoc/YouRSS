# yourss Helm chart

Deploys YouRSS+: a FastAPI/Jinja Youtube RSS front-end with a **SQLite account store**, passwordless magic-link login and self-service pages. One `Deployment`, one `Service`, an optional `Ingress`, two ReadWriteOnce PVCs (`db`, `backup`), an `emptyDir` cache and a `Secret` for the signing key, SMTP password and Turnstile secret.

```sh
helm template yourss ./charts/yourss
helm install my-yourss ./charts/yourss -f myvalues.yaml
kubectl port-forward service/my-yourss 8000:http
```

> **One writer only.** SQLite runs in WAL mode with one uvicorn worker. The chart uses `replicaCount: 1`, disables autoscaling, and sets `strategy.type: Recreate` so an upgrade never mounts the same RWO volume in two pods. Do not raise the replica count without moving to an external database.

## Application values

Each value under `yourss` maps to one `YOURSS_*` environment variable.

| Value | Default | Variable |
| --- | --- | --- |
| `yourss.image.repository` | `ghcr.io/ridoc/yourss` | |
| `yourss.image.tag` | `2026.11.26.2` | immutable rollout tag |
| `yourss.image.pullPolicy` | `IfNotPresent` | |
| `yourss.timezone` | `Europe/Paris` | `TZ` |
| `yourss.logLevel` | `INFO` | `LOGURU_LEVEL` |
| `yourss.customPagesEnabled` | `true` | `YOURSS_CUSTOM_PAGES_ENABLED` |
| `yourss.maxPageItems` | `12` | `YOURSS_MAX_PAGE_ITEMS` |
| `yourss.apiDocsEnabled` | `false` | `YOURSS_API_DOCS_ENABLED` |
| `yourss.cleanTitles` | `false` | `YOURSS_CLEAN_TITLES` |
| `yourss.cacheEnabled` | `false` | `YOURSS_CACHE_ENABLED` |
| `yourss.cacheFolder` | `/data/cache` | `YOURSS_CACHE_FOLDER` |
| `yourss.cacheRssTtl` | `PT30M` | `YOURSS_CACHE_RSS_TTL` |
| `yourss.cacheRssFallbackTtl` | `PT48H` | `YOURSS_CACHE_RSS_FALLBACK_TTL` |
| `yourss.cacheMetadataTtl` | `P7D` | `YOURSS_CACHE_METADATA_TTL` |
| `yourss.warmupEnabled` | `false` | `YOURSS_WARMUP_ENABLED` |
| `yourss.loginTtl` | `P90D` | `YOURSS_LOGIN_TTL` |
| `yourss.dbPath` | `/data/db/yourss.db` | `YOURSS_DB_PATH` |
| `yourss.secretKey` | `""` (generated) | `YOURSS_SECRET_KEY` |
| `yourss.publicBaseUrl` | `""` | `YOURSS_PUBLIC_BASE_URL` |
| `yourss.registrationEnabled` | `false` | `YOURSS_REGISTRATION_ENABLED` |
| `yourss.sessionTtl` | `P30D` | `YOURSS_SESSION_TTL` |
| `yourss.private` | `false` | `YOURSS_PRIVATE` |
| `yourss.noindex` | `false` | `YOURSS_NOINDEX` |
| `yourss.adminEmail` | `""` | `YOURSS_ADMIN_EMAIL` |
| `yourss.adminUsername` | `""` | `YOURSS_ADMIN_USERNAME` |
| `yourss.maxPagesPerUser` | `50` | `YOURSS_MAX_PAGES_PER_USER` |
| `yourss.maxPageSubscriptions` | `200` | `YOURSS_MAX_PAGE_SUBSCRIPTIONS` |
| `yourss.forwardedAllowIps` | `127.0.0.1` | `YOURSS_FORWARDED_ALLOW_IPS` |
| `yourss.usersFile` | `""` | `YOURSS_USERS_FILE` |
| `yourss.importExpectPages` | `0` | `YOURSS_IMPORT_EXPECT_PAGES` |
| `yourss.importExpectSubs` | `0` | `YOURSS_IMPORT_EXPECT_SUBS` |
| `yourss.videoDurations` | `false` | `YOURSS_VIDEO_DURATIONS` |
| `yourss.durationTtl` | `PT6H` | `YOURSS_DURATION_TTL` |
| `yourss.smtp.host` | `""` | `YOURSS_SMTP_HOST` |
| `yourss.smtp.port` | `587` | `YOURSS_SMTP_PORT` |
| `yourss.smtp.username` | `""` | `YOURSS_SMTP_USER` |
| `yourss.smtp.password` | `""` | `YOURSS_SMTP_PASSWORD` (Secret) |
| `yourss.smtp.starttls` | `true` | `YOURSS_SMTP_STARTTLS` |
| `yourss.smtp.ssl` | `false` | `YOURSS_SMTP_SSL` |
| `yourss.smtp.from` | `""` | `YOURSS_SMTP_FROM` |
| `yourss.turnstile.siteKey` | `""` | `YOURSS_TURNSTILE_SITE_KEY` |
| `yourss.turnstile.secretKey` | `""` | `YOURSS_TURNSTILE_SECRET_KEY` (Secret) |
| `yourss.users` | `[]` | legacy users/pages, mounted at `/yourss/users.yaml` |
| `yourss.extraEnv` | `[]` | extra `name` / `value` entries |

`YOURSS_SECRET_KEY`, `YOURSS_SMTP_PASSWORD` and `YOURSS_TURNSTILE_SECRET_KEY` render into one `Secret` (`<fullname>-env`). An empty signing key is generated once and stored in SQLite.

## Storage

| Value | Default | Notes |
| --- | --- | --- |
| `persistence.db.enabled` | `true` | creates `<fullname>-db` |
| `persistence.db.size` | `1Gi` | SQLite plus WAL/SHM |
| `persistence.db.storageClass` | `""` | cluster default |
| `persistence.db.existingClaim` | `""` | use an existing PVC |
| `persistence.backup.enabled` | `true` | creates `<fullname>-backup` |
| `persistence.backup.size` | `2Gi` | `scripts/backup-db.sh` output |
| `persistence.backup.storageClass` | `""` | cluster default |
| `persistence.backup.existingClaim` | `""` | use an existing PVC |

The DB and backup PVCs are `ReadWriteOnce`; the cache is an `emptyDir` at `/data/cache` and `yourss.cacheFolder` defaults there. The root filesystem is read-only; `/tmp`, `/data/db`, `/data/cache` and `/data/backup` are writable. Set `persistence.db.enabled: false` only for a throwaway instance; the deployment then uses an `emptyDir` and loses the DB on restart.

The backup PVC has no scheduler. Run `kubectl exec deploy/<release> -- backup-db.sh` from a CronJob or manually, and keep the output on `/data/backup`.

## Users and legacy import

`yourss.users` declares legacy users/pages and is rendered to a `Secret`, mounted read-only at `/yourss/users.yaml`. The app imports it **once** and sets `users_imported_at`; later edits to the Secret do not alter the database. Leave `yourss.usersFile` empty when using this Secret.

For a live migration, mount a vetted users.yaml at `yourss.usersFile` and set `importExpectPages` / `importExpectSubs` to the expected floor. A missing file, `REPLACE_ME`, a parse error or a count below the floor fails startup instead of silently starting empty.

## Kubernetes values

| Value | Default | Notes |
| --- | --- | --- |
| `replicaCount` | `1` | SQLite single writer; keep at 1 |
| `autoscaling.enabled` | `false` | leave off for SQLite |
| `strategy.type` | `Recreate` | no surge, no two writers |
| `podSecurityContext` | uid/gid/fsGroup `999` | matches the image |
| `securityContext` | read-only root, no caps, no privilege escalation | |
| `livenessProbe` | `/api/version` | a 503 healthz must not restart the pod |
| `readinessProbe` | `/healthz` | DB and cache writable |
| `service.type` / `service.port` | `ClusterIP` / `8000` | |
| `ingress.enabled` | `false` | usual `className`, `annotations`, `hosts`, `tls` |
| `resources.requests` | `50m` CPU, `256Mi` memory | |
| `resources.limits` | `512Mi` memory, no CPU limit | |
| `volumes`, `volumeMounts` | `[]` | extra mounts |
| `nodeSelector`, `tolerations`, `affinity` | `{}` / `[]` | |
