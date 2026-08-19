# Security Policy

## Secrets

Do not commit real provider API keys, machine API keys, passwords, session tokens, MCP tokens, object-storage credentials, or private keys.

Use `.env.example` as the committed template and keep local values in `.env`. The repository ignores `.env`, `.env.*`, and generated runtime data under `data/`.

Generate local shared secrets with a cryptographically strong generator, for example:

```bash
openssl rand -hex 32
```

If a real secret is committed or shared publicly, rotate it immediately. Removing it from the latest commit is not enough once it has been pushed; treat the secret as compromised and rotate it at the provider or service where it was issued.

## Reporting Vulnerabilities

For now, report security issues privately to the project maintainer. Do not open a public issue for active vulnerabilities or leaked credentials.
