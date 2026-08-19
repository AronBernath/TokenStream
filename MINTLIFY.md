# Mintlify Deployment

TokenStream's public documentation source is stored in this repository and is configured for Mintlify with `docs.json` at the repository root.

## Repository Layout

- `docs.json` defines the Mintlify site settings and navigation.
- `overview/`, `quickstart/`, `concepts/`, `guides/`, `reference/`, `troubleshooting/`, and `review/` contain Mintlify MDX pages.
- `docs/` contains internal service notes and OpenAPI contracts. It is not the Mintlify navigation root.

## Connect Mintlify

1. Go to the Mintlify dashboard.
2. Create or open the TokenStream documentation project.
3. Install the Mintlify GitHub App from the Mintlify dashboard, not directly from GitHub.
4. Connect `AronBernath/TokenStream`.
5. Use the repository root as the documentation directory because `docs.json` is at the root.
6. Deploy from `main`.

Mintlify automatically deploys pushed changes from the connected branch. Pull requests receive preview builds when the GitHub App is connected.

## Local Preview

Install the Mintlify CLI, then run it from the repository root:

```bash
npm i -g mint
mint dev
```

The preview command must be run from the directory that contains `docs.json`.

## CI Guard

The GitHub `Docs` check validates that:

- `docs.json` exists at the repository root
- required Mintlify fields are present
- every page listed in navigation exists
- generated cleanup placeholder text is not present in published docs pages

This is a lightweight repository guard. Mintlify's own GitHub App remains the source of deployment previews and hosted documentation builds.
