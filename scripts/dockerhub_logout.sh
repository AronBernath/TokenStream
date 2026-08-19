#!/usr/bin/env bash
# Log out from Docker Hub.

set -euo pipefail

docker logout docker.io 2>/dev/null || true
docker logout 2>/dev/null || true
