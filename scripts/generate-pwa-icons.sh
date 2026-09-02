#!/usr/bin/env bash
# Generate PWA icon variants from frontend/public/logo.png.
#
# Requires ImageMagick (``magick`` or ``convert``). Run from the repository root:
#
#   bash scripts/generate-pwa-icons.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

SRC="${ROOT_DIR}/frontend/public/logo.png"
OUT_DIR="${ROOT_DIR}/frontend/public/pwa"

if ! command -v magick >/dev/null 2>&1 && ! command -v convert >/dev/null 2>&1; then
  echo "Error: ImageMagick (magick or convert) is required." >&2
  exit 1
fi

if command -v magick >/dev/null 2>&1; then
  MAGICK="magick"
else
  MAGICK="convert"
fi

if [[ ! -f "${SRC}" ]]; then
  echo "Error: Source logo not found at ${SRC}" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"

# Common icon sizes for the manifest. Use a black background to match the logo.
SIZES=(16 32 72 96 128 144 152 180 192 384 512)
for size in "${SIZES[@]}"; do
  outfile="${OUT_DIR}/pwa-${size}x${size}.png"
  if [[ "${size}" == "180" ]]; then
    outfile="${OUT_DIR}/apple-touch-icon.png"
  fi
  if [[ "${size}" == "32" ]]; then
    "${MAGICK}" "${SRC}" -resize "${size}x${size}" -background black -gravity center -extent "${size}x${size}" "${OUT_DIR}/favicon-32x32.png"
  fi
  if [[ "${size}" == "16" ]]; then
    "${MAGICK}" "${SRC}" -resize "${size}x${size}" -background black -gravity center -extent "${size}x${size}" "${OUT_DIR}/favicon-16x16.png"
  fi
  "${MAGICK}" "${SRC}" -resize "${size}x${size}" -background black -gravity center -extent "${size}x${size}" "${outfile}"
done

# Maskable variants keep the logo inside the safe zone by scaling it to 66% of
# the canvas and filling the remainder with a black background.
MASKABLE_SIZES=(192 512)
for size in "${MASKABLE_SIZES[@]}"; do
  target_size=$(( size * 66 / 100 ))
  outfile="${OUT_DIR}/maskable-${size}x${size}.png"
  "${MAGICK}" "${SRC}" -resize "${target_size}x${target_size}" -background black -gravity center -extent "${size}x${size}" "${outfile}"
done

echo "PWA icons written to ${OUT_DIR}"
