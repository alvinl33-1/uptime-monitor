---
name: compose-to-container-apps
description: Plan and build an Azure Container Apps deployment for an app that already runs under Docker Compose — mapping each service to a container app or job, Postgres/Redis to managed services, secrets to Key Vault, and CI to a GitHub Actions OIDC deploy. Use when moving this repo from Compose to Azure.
---

# Docker Compose → Azure Container Apps

This is the glue playbook for this repo. It decides *what* to build; use the companion skills for
*how*: `azd-deployment` (azure.yaml, provisioning, hooks), `arm-templates` (Bicep),
`azure-keyvault`, `azure-identity-*`, `github-actions-advanced` (OIDC deploy job),
`azure-monitor-audit` (logs, KQL, alerts), `pre-release-review` and
`production-runtime-certification` (before and after a release).

The repo-specific service mapping is in [references/project-map.md](references/project-map.md).
Read it first.

## Guardrails

- **Nothing is provisioned without explicit approval.** Provisioning, role assignments and deploys
  cost money and change a subscription. Produce the Bicep, `azure.yaml`, the `what-if` output and a
  cost estimate, then stop and ask. Respect any phase/cost guard in the repo's own plans.
- **Never expose an unauthenticated management API publicly.** Anything without auth gets
  `ingress.external: false` (environment-internal only) until auth exists.
- **No secrets in Bicep parameters, `azure.yaml`, workflow files, or logs.** Use Key Vault
  references and managed identity.
- Keep the Compose stack working for local development; Azure is an additional target.

## Mapping rules

| Compose concept | Azure Container Apps equivalent |
|---|---|
| Long-running HTTP service | Container app with ingress (`external: true` only if public) |
| Long-running non-HTTP loop (scheduler) | Container app, no ingress, `minReplicas: 1`, `maxReplicas: 1` |
| Queue worker | Container app, no ingress, KEDA scale rule on the queue (e.g. `redis` list length) |
| One-shot `command:` (migrations) | Container Apps **Job** (manual trigger), run by CI before the new revision |
| `postgres:*` service + volume | Azure Database for PostgreSQL Flexible Server (Entra auth, backups on) |
| `redis:*` service | Azure Managed Redis (TLS; port 10000 by default — confirm for your SKU) |
| Named volume for app state | Avoid; move state to Postgres/Blob. Replicas do not share local disk |
| `build:` | Image built in CI (or `remoteBuild`) and pushed to Azure Container Registry |
| `environment:` secrets | Container app secrets with `keyVaultUrl` + managed identity |
| `environment:` plain config | Container app env vars from Bicep parameters |
| `healthcheck:` | Liveness + readiness probes on the existing health endpoints |
| `depends_on` | No equivalent — apps must retry their dependencies on startup |
| `deploy.resources.limits` | `resources.cpu` / `resources.memory` per container |
| Service name DNS (`http://api:8000`) | Same environment: `http://<app-name>` via internal ingress (port 80, not the container port) |
| Logs to stdout | Log Analytics workspace attached to the environment |

## Minimum resource set

```
rg-<app>-<env>
├── Log Analytics workspace
├── Application Insights (workspace-based)
├── Container Apps environment (Consumption)
├── Azure Container Registry (Basic)   → AcrPull for the app identity
├── User-assigned managed identity     → shared by the apps
├── Key Vault (RBAC mode)              → Key Vault Secrets User for the identity
├── PostgreSQL Flexible Server (Burstable B1ms to start, Entra admin set)
├── Azure Managed Redis (only if the app needs Redis)
└── Container apps + jobs per the project map
```

Prefer one **user-assigned** identity so role assignments exist before the first revision starts.

## Workflow

1. **Inventory.** Read the Compose files, Dockerfiles, health endpoints and env vars. Update
   `references/project-map.md` if anything changed.
2. **Code changes the cloud needs** (small, reviewable PRs, each with tests):
   - Read every connection setting from env vars; no hostnames baked into images.
   - Passwordless DB auth through managed identity (see the repo's `*-azure-runtime` skill).
   - Retry dependencies at startup instead of relying on `depends_on`.
   - Make migrations a separate command, not something every replica runs at startup.
   - Move anything written to local disk that must survive restarts or be shared between replicas.
3. **Infrastructure.** `infra/main.bicep` + modules, `infra/main.parameters.json`, `azure.yaml`.
   Run `az bicep build` and `az deployment group what-if` locally; show the diff.
4. **CI deploy job** (after the existing quality gates):
   - `permissions: id-token: write, contents: read`; GitHub `environment: production` with
     required reviewers.
   - Sign in with `azure/login` using a federated credential whose subject is pinned to
     `repo:<owner>/<repo>:environment:production`. No client secrets.
   - Build → push to ACR tagged with the commit SHA → run the migration job → update the apps to that
     tag → smoke-test the health endpoints → fail the job on any non-200.
   - Pin every action to a full commit SHA, as the existing workflows do.
5. **Verify.** Use `production-runtime-certification`: revision is healthy, image tag matches the
   commit, migrations are at head, probes are green, critical routes respond, logs arrive in
   Log Analytics.
6. **Document.** Add a "Deploy to Azure" section to the README: prerequisites, one-time setup
   (federated credential, Entra DB admin), the deploy command, rollback
   (`az containerapp revision activate` the previous revision), and how to tear down
   (`azd down`).

## Cost notes

Consumption-plan container apps can scale to zero, but a scheduler with `minReplicas: 1`,
PostgreSQL Flexible Server and Managed Redis bill continuously. Put a budget alert on the resource
group before the first deploy, and tear the environment down when you are not using it.
