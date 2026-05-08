#!/usr/bin/env bash
set -euo pipefail

TAG="${1}"

DOCKER_BUILDKIT=1 docker buildx build \
  --platform linux/arm64,linux/amd64 \
  -t "ghcr.io/aerostacks/ntrip-caster:${TAG}" \
  --push .
