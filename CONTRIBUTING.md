# Contributing

Thanks for your interest in improving this reference implementation. This is an
experiment repository, so contributions are welcome but should keep the codebase
easy to follow and easy to deploy.

## Getting started

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest -q
```

The pure-logic modules run with **no Azure dependency**. Please keep them that
way — new business logic (routing, chunking, degradation, config) should be unit
tested without requiring a live Azure subscription.

## Making changes

1. Create a topic branch.
2. Make focused, minimal changes with clear commit messages.
3. Add or update unit tests for any logic change (`tests/`).
4. Run `pytest -q` and, for infra changes, `az bicep build --file infra/main.bicep`.
5. Update the relevant docs (`README.md`, `docs/DEPLOYMENT.md`,
   `docs/CONFIGURATION.md`) when behavior or configuration changes.
6. Open a pull request describing the change and how you validated it.

## Conventions

- **Python:** 3.11+, standard library and the SDKs already in
  `requirements.txt`. Prefer `DefaultAzureCredential` for auth.
- **Infra:** Bicep only; keep parameters documented in
  `infra/main.parameters.json` and `docs/DEPLOYMENT.md`.
- **Secrets:** never commit secrets or a real `.env` file.
- **Docs:** keep the verified deployment steps in `docs/DEPLOYMENT.md` accurate;
  if you change infra, re-verify and update the guide.

## Code of conduct

By participating you agree to abide by the
[Code of Conduct](CODE_OF_CONDUCT.md).
