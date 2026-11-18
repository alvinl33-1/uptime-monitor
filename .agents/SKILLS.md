# Uptime Monitor agent skills

One canonical folder, three readers:

```
.agents/skills/          ← the real files (Codex reads this path)
.claude/skills  → ../.agents/skills   (Claude Code)
.github/skills  → ../.agents/skills   (GitHub Copilot)
```

Add, edit or remove skills only in `.agents/skills/`.

These cover `AGENTS.md` priorities #3 (Azure deployment target) and #4 (deployment hardening).
Per `AGENTS.md`, use Bicep and Container Apps — not Terraform or Kubernetes — for now.

## Ship to Azure
| Skill | Use it for | Source |
|---|---|---|
| `compose-to-container-apps` | The plan: each Compose service → container app/job, managed Postgres + Redis, Key Vault, OIDC deploy. Start here. Repo map in `references/project-map.md` | custom |
| `azd-deployment` | `azure.yaml`, azd environments, remote builds, post-deploy checks | AAS library |
| `arm-templates` | Bicep modules, parameters, `what-if` | AAS library |
| `github-actions-advanced` | OIDC sign-in to Azure, hardened deploy job after the existing CI jobs | AAS library |
| `azure-identity-py` | `DefaultAzureCredential`, managed identity | AAS library |
| `azure-keyvault` | Webhook URL and Redis secrets | AAS library |
| `python-azure-runtime` | Code changes: passwordless psycopg/SQLAlchemy + Alembic, Managed Redis over TLS, telemetry, SSRF on Azure | custom |
| `azure-monitor-opentelemetry-py` | Application Insights setup for FastAPI and the worker loops | AAS library |

## Release and operations
| Skill | Use it for | Source |
|---|---|---|
| `pre-release-review` | Read-only go/no-go before a deploy | AAS library |
| `production-runtime-certification` | Evidence that a deployed revision is healthy | AAS library |
| `deployment-procedures` | Rollout and rollback principles | AAS library |
| `database-migrations-sql-migrations` | Planning Alembic migrations that run as a job before rollout | AAS library |
| `azure-monitor-audit` | Log Analytics, KQL, alert rules | AAS library |
