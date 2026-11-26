#!/usr/bin/env bash
# Read the target before preparing its image on the relay. Copies public keys only.
set -Eeuo pipefail
umask 077
SOURCE=$(cd -- "$(dirname -- "$0")" && pwd -P)
exec python3 "$SOURCE/profile.py" collect "$@"
