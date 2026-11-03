#!/usr/bin/env bash
set -euo pipefail
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="prufai-media:ffmpeg-6.1.1"
docker build --file "$repository_root/infra/media-probe.Dockerfile" --tag "$image" "$repository_root/infra"
docker run --rm --network=none --read-only --cap-drop=ALL "$image" -version
echo 'Set AUDIO_MEDIA_RUNTIME=docker and AUDIO_MEDIA_IMAGE=prufai-media:ffmpeg-6.1.1 for the API and integration tests.'
