# Changelog

## [0.1.1](https://github.com/AronBernath/TokenStream/compare/v0.1.0...v0.1.1) (2026-09-07)

### Security and Release Evidence

* add PR integration and smoke test pipelines for API, service, and compose validation
* add authz/security regression checks for scoped API keys and protected routes
* add unauthenticated and authenticated DAST coverage with ZAP for API and dev-ui boundaries
* add OpenGrep SAST reporting with CI helper script coverage
* add Syft SBOM generation, component inventory, package inventory, GitHub Actions inventory, license inventory, and unknown-license package reporting
* add Grype source and container image vulnerability reporting with permissive policy evidence
* add Trivy container image lint evidence for misconfiguration and secret checks
* add release image digest, keyless signature, and provenance evidence
* add release advisory inventory and evidence-backed update candidate generation

### Documentation

* consolidate public documentation under the Mintlify `docs` structure
* update internal CI and DevSecOps process documentation for implemented evidence workflows

### Bug Fixes

* install json schema validation dependency ([90e90e6](https://github.com/AronBernath/TokenStream/commit/90e90e6342f0030579968b26104b3bd8d3629e68))

## Changelog

All notable changes to TokenStream will be documented in this file.

This project uses [Semantic Versioning](https://semver.org/) and release automation based on Conventional Commits.
