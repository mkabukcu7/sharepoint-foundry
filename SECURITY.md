# Security Policy

## Reporting a vulnerability

This repository is a **technical experiment / reference implementation** and is
not a production service. If you discover a security issue in the code or
documentation, please **do not open a public issue**. Instead, report it
privately to the maintainers (via your organization's internal security contact
or the repository owner) with:

- a description of the issue and its impact,
- steps to reproduce, and
- any suggested remediation.

We will acknowledge the report and work with you on a fix.

## Security posture of this reference

- **RBAC-only:** All data-plane services deploy with local authentication
  disabled (`disableLocalAuth = true`). Access is via Microsoft Entra RBAC.
- **Managed identity:** Runtime access uses a user-assigned managed identity;
  no keys or connection strings with embedded secrets are used.
- **Secrets:** Secrets (e.g., Tableau PAT) belong in **Key Vault**. Never commit
  a real `.env` file — see [`.env.example`](.env.example).
- **Network isolation:** Set `privateByDefault=true` to disable public network
  access for production. See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) and
  [`docs/02-architecture.md`](docs/02-architecture.md).

## Responsible AI

This solution integrates Content Safety / Prompt Shields and graceful
degradation. Review the governance and Responsible AI sections of
[`docs/02-architecture.md`](docs/02-architecture.md) before any production use,
and validate grounding sources, bias, transparency, and compliance for your
context.

## Do not use real data

All sample data in this repository is illustrative. Do not ingest confidential
or regulated data without completing your organization's data-classification,
privacy, and compliance review.
