# Mintlify Deployment

TokenStream's public documentation source is stored under `docs/` and is configured for Mintlify with `docs/docs.json`.

## Repository Layout

- `docs.json` defines the Mintlify site settings and navigation.
- `overview/`, `quickstart/`, `concepts/`, `guides/`, `reference/`, `troubleshooting/`, and `review/` contain Mintlify MDX pages.
- Service notes, OpenAPI contracts, CRA evidence files, and example corpus material live alongside the public docs under this `docs/` tree.

## Connect Mintlify

1. Go to the Mintlify dashboard.
2. Create or open the TokenStream documentation project.
3. Install the Mintlify GitHub App from the Mintlify dashboard, not directly from GitHub.
4. Connect `AronBernath/TokenStream`.
5. Use `docs/` as the documentation directory because `docs.json` is inside `docs/`.
6. Deploy from `main`.

Mintlify automatically deploys pushed changes from the connected branch. Pull requests receive preview builds when the GitHub App is connected.

## Local Preview

Install the Mintlify CLI, then run it from `docs/`:

```bash
npm i -g mint
cd docs
mint dev
```

The preview command must be run from the directory that contains `docs.json`.

## CI Guard

The GitHub `Docs` check validates that:

- `docs/docs.json` exists
- required Mintlify fields are present
- every page listed in navigation exists
- generated cleanup placeholder text is not present in published docs pages

This is a lightweight repository guard. Mintlify's own GitHub App remains the source of deployment previews and hosted documentation builds.
