#!/usr/bin/env bash
# Tag and push to Docker Hub only when PUSH_DOCKERHUB=1 (set in GitLab before_script).
set -euo pipefail
[ "${PUSH_DOCKERHUB:-0}" = "1" ] || exit 0
docker tag "$1" "$2"
docker push "$2"
