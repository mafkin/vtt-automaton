#!/usr/bin/env bash
# Mix a session's podcast tracks into one mono WAV, to check that the tracks line up
# (docs/PODCAST.md: the clap test). Uses ffmpeg, or the linuxserver/ffmpeg image if ffmpeg
# isn't installed.
#   scripts/podcast-mix.sh data/recordings/<session-id> [out.wav]
set -euo pipefail
dir=${1:?usage: scripts/podcast-mix.sh data/recordings/<session-id> [out.wav]}
out=${2:-$dir/mix.wav}
shopt -s nullglob
tracks=("$dir"/tracks/*.ogg)
(( ${#tracks[@]} )) || { echo "No tracks in $dir/tracks" >&2; exit 1; }

args=()
for t in "${tracks[@]}"; do args+=(-i "$t"); done
filter="amix=inputs=${#tracks[@]}:normalize=0:duration=longest"

if command -v ffmpeg >/dev/null; then
  ffmpeg -hide_banner -loglevel error -y "${args[@]}" -filter_complex "$filter" -ac 1 "$out"
else
  docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:$PWD" -w "$PWD" linuxserver/ffmpeg \
    -hide_banner -loglevel error -y "${args[@]}" -filter_complex "$filter" -ac 1 "$out"
fi
echo "Mixed ${#tracks[@]} track(s) into $out"
