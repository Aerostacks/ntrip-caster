# NTRIP Caster

`ntrip-caster` is the central RTCM distribution server that runs on `avalanche`.

## What this repository does

- accepts `SOURCE /MOUNTPOINT` uploads from `anchor`
- keeps an active source stream for each mountpoint
- serves NTRIP clients on `GET /MOUNTPOINT`
- forwards incoming RTCM bytes to every connected client
- returns a simple sourcetable on `GET /`

## Role in the system

The main RTK path is:

`u-blox base station -> anchor -> ntrip-caster -> synapse`

It can also serve other rover clients:

`u-blox base station -> anchor -> ntrip-caster -> rover clients`

## What the caster is not

- not an RTK correction source
- not a serial reader for the base station
- not a mission-data backend

## Main files

- `app.py`: the TCP NTRIP server
- `docker-compose.yaml`: deployment for `avalanche`
- `config/mountpoints.json.example`: mountpoint and auth configuration

## Authentication model

The current recommended setup uses source authentication only:

- `anchor` must know the mountpoint `source_password`
- NTRIP clients such as `synapse` connect without client username/password

This matches the current `synapse` NTRIP client implementation.

The caster still restricts who can publish because only a client that sends:

`SOURCE <source_password> /<mountpoint>`

for a configured mountpoint is accepted as the source stream.

## Why this repo exists

The goal of the caster is to provide one stable central endpoint where:

- `anchor` uploads RTCM corrections
- `synapse` connects as an NTRIP client
- additional rover clients can consume the same correction stream
