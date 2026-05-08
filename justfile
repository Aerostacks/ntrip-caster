# ntrip-caster
set windows-shell := ["bash", "-cu"]

remote := "r2d2@r2d2"
branch := `git rev-parse --abbrev-ref HEAD`

# Start ntrip-caster on remote
start:
    ssh {{remote}} "cd ~/unified && docker compose -f docker-compose.infra.yml up -d ntrip-caster"

alias up := start

# Stop ntrip-caster on remote
stop:
    ssh {{remote}} "cd ~/unified && docker compose -f docker-compose.infra.yml stop ntrip-caster"

alias down := stop

# Pull latest image on remote
update:
    ssh {{remote}} "docker pull ghcr.io/aerostacks/ntrip-caster:{{branch}}"

alias u := update

# Publish local project to remote registry
build:
    bash docker-build.sh {{branch}}
    git push

alias b := build

# Publish and deploy to remote server
deploy: build
    -just stop
    just update
    just start

alias d := deploy

# First-time setup of this repo
setup:
    -ssh {{remote}} "docker pull ghcr.io/aerostacks/ntrip-caster:{{branch}}"
    -docker buildx rm aerostacks
    docker buildx create --name aerostacks --driver docker-container
    docker buildx create --name aerostacks --append --node amd64 --platform linux/amd64 ssh://r2d2@r2d2
    docker buildx create --name aerostacks --append --node arm64 --platform linux/arm64 ssh://avalanche@avalanche
    docker buildx use aerostacks
    docker buildx inspect --bootstrap
