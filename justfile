# ntrip-caster

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
    -docker buildx inspect ntrip-caster-builder >/dev/null 2>&1 || docker buildx create --name ntrip-caster-builder --driver docker-container --use
    docker buildx use ntrip-caster-builder
    docker buildx inspect --bootstrap
