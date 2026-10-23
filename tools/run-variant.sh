#!/bin/sh
# Flash one prepared variant and pull its log: run-variant.sh a|b|c|d|e OUT.txt
set -eu
v=${1:?variant}
out=${2:?out.txt}
here=$(cd "$(dirname "$0")" && pwd)
img="$here/../shim/out/variant-$v.img"
[ -f "$img" ] || { echo "no $img" >&2; exit 1; }
cp "$img" "$here/../shim/out/shim-boot.img"
exec "$here/picasso-modem-test.sh" "$here/../shim/out/shim-boot.img" "$out" "${3:-3600}"
