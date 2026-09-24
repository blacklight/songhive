#!/usr/bin/env bash
# Generate PWA icon variants from frontend/public/icon.svg.
#
# Requires ImageMagick (``magick`` or ``convert``). Run from the repository root:
#
#   bash scripts/generate-pwa-icons.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

SRC="${ROOT_DIR}/frontend/public/icon.svg"
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
  echo "Error: Source icon not found at ${SRC}" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"

# Rasterize the SVG at a given size on a transparent background. The density
# supersamples the 512px viewBox so downscaling stays sharp.
rasterize() {
  local size="$1"
  "${MAGICK}" -background none -density 288 "${SRC}" -resize "${size}x${size}" "png:-"
}

# Common icon sizes for the manifest. The background is transparent so the
# icon blends into launchers, splash screens and notifications.
SIZES=(16 32 72 96 128 144 152 180 192 384 512)
for size in "${SIZES[@]}"; do
  outfile="${OUT_DIR}/pwa-${size}x${size}.png"
  if [[ "${size}" == "180" ]]; then
    outfile="${OUT_DIR}/apple-touch-icon.png"
  fi
  if [[ "${size}" == "32" ]]; then
    rasterize "${size}" > "${OUT_DIR}/favicon-32x32.png"
  fi
  if [[ "${size}" == "16" ]]; then
    rasterize "${size}" > "${OUT_DIR}/favicon-16x16.png"
  fi
  rasterize "${size}" > "${outfile}"
done

# Maskable variants keep the hexagon inside the safe zone by scaling it to 80%
# of the canvas; the rest stays transparent so launcher masks can clip to any
# shape without a black background showing through.
MASKABLE_SIZES=(192 512)
for size in "${MASKABLE_SIZES[@]}"; do
  target_size=$(( size * 80 / 100 ))
  outfile="${OUT_DIR}/maskable-${size}x${size}.png"
  rasterize "${target_size}" | \
    "${MAGICK}" - -background none -gravity center -extent "${size}x${size}" "${outfile}"
done

echo "PWA icons written to ${OUT_DIR}"
