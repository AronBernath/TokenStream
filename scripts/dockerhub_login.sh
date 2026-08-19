#!/usr/bin/env bash
# Log in to Docker Hub (docker.io). Same credential pattern as GitLab CI: use env vars, never commit secrets.
#
# Usage:
#   export DOCKERHUB_USER="youruser"
#   export DOCKERHUB_PASS="your_password_or_access_token"
#   ./scripts/dockerhub_login.sh
#
# Prefer a Docker Hub access token over your account password.

set -euo pipefail

: "${DOCKERHUB_USER:?Set DOCKERHUB_USER}"
: "${DOCKERHUB_PASS:?Set DOCKERHUB_PASS (or use a Docker Hub access token as the value)}"

echo "$DOCKERHUB_PASS" | docker login -u "$DOCKERHUB_USER" --password-stdin
