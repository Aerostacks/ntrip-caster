# ntrip-caster
name := "ntrip-caster"
remote := "r2d2@r2d2"
branch := `git rev-parse --abbrev-ref HEAD`
tag := replace(branch, "/", "-")
image := "ghcr.io/aerostacks/" + name

start_cmd := "ssh " + remote + " \"cd ~/unified && docker compose -f docker-compose.infra.yml up -d ntrip-caster\""
stop_cmd := "ssh " + remote + " \"cd ~/unified && docker compose -f docker-compose.infra.yml stop ntrip-caster\""
lint_cmd := ""
needs_ssh_agent := "false"
build_cmd := "bash docker-build.sh " + branch
deploy_stop_cmd := "just stop || true"
deploy_pull_cmd := "ssh " + remote + " \"docker pull " + image + ":" + tag + "\""
deploy_start_cmd := "just start"
setup_extra_cmd := ""
format_cmd := "echo 'No formatter configured'"

# Start project
start:
    {{start_cmd}}

alias up := start

# Stop project
stop:
    {{stop_cmd}}

alias down := stop

# Build and push
build:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ if lint_cmd != "" { lint_cmd } else { "true" } }}
    {{ if needs_ssh_agent == "true" { "eval \"$(ssh-agent -s)\" && ssh-add ~/.ssh/id_ed25519" } else { "true" } }}
    {{build_cmd}}
    git push

alias b := build

# Build and deploy
deploy: build
    #!/usr/bin/env bash
    set -euo pipefail
    {{deploy_stop_cmd}}
    {{deploy_pull_cmd}}
    {{deploy_start_cmd}}

alias d := deploy

# First-time setup (multi-arch buildx)
setup:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ if setup_extra_cmd != "" { setup_extra_cmd } else { "true" } }}
    docker buildx rm aerostacks 2>/dev/null || true
    docker buildx create --name aerostacks --driver docker-container
    docker buildx create --name aerostacks --append --node amd64 --platform linux/amd64 ssh://r2d2@r2d2
    docker buildx create --name aerostacks --append --node arm64 --platform linux/arm64 ssh://avalanche@avalanche
    docker buildx use aerostacks
    docker buildx inspect --bootstrap

# Format codebase
format:
    {{format_cmd}}

alias f := format

# Run CI checks locally (lint + tests)
ci:
    ruff format --check .
    ruff check .
    pytest tests/

# Prune stale branches (>1w inactive, no open PR) in this repo
[no-cd]
prune-branches:
    #!/usr/bin/env bash
    set -euo pipefail
    cutoff=$(date -d '1 week ago' +%s 2>/dev/null || date -v-1w +%s)
    default_branch=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "main")
    git fetch --prune -q 2>/dev/null || true
    for branch in $(git for-each-ref --format='%(refname:short) %(committerdate:unix)' refs/heads/ | while read name ts; do
        [ "$name" = "$default_branch" ] && continue
        [ "${ts:-0}" -lt "$cutoff" ] && echo "$name"
    done); do
        pr_count=$(gh pr list --head "$branch" --state open --json number --jq 'length' 2>/dev/null || echo "0")
        if [ "$pr_count" = "0" ]; then
            echo "Deleting: $branch (no open PR, stale >1w)"
            git branch -D "$branch" 2>/dev/null || true
            git push origin --delete "$branch" 2>/dev/null || true
        else
            echo "Keeping: $branch ($pr_count open PR(s))"
        fi
    done
    echo "Done."
