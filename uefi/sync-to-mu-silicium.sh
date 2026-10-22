#!/bin/sh
# Install this port's platform files into a Mu-Silicium checkout.
#   tools: uefi/sync-to-mu-silicium.sh [MU_DIR]   (default ~/mu-silicium)
set -eu
here=$(cd "$(dirname "$0")" && pwd)
mu=${1:-$HOME/mu-silicium}
[ -d "$mu/Silicon/Qualcomm" ] || { echo "not a Mu-Silicium tree: $mu" >&2; exit 1; }

# SoC package: a rename of KodiakPkg (SM7325), the closest sibling of lito.
if [ ! -d "$mu/Silicon/Qualcomm/LitoPkg" ]; then
	cp -r "$mu/Silicon/Qualcomm/KodiakPkg" "$mu/Silicon/Qualcomm/LitoPkg"
	for f in $(find "$mu/Silicon/Qualcomm/LitoPkg" -type f); do
		sed -i 's/Kodiak/Lito/g; s/kodiak/lito/g' "$f"
	done
	for f in $(find "$mu/Silicon/Qualcomm/LitoPkg" -name '*Kodiak*'); do
		mv "$f" "$(echo "$f" | sed 's/Kodiak/Lito/g')"
	done
fi

mkdir -p "$mu/Platforms/Xiaomi/picassoPkg"
cp -r "$here/picasso/picassoPkg/." "$mu/Platforms/Xiaomi/picassoPkg/"
cp "$here/picasso/picasso.dsc" "$here/picasso/picasso.fdf" \
   "$here/picasso/picasso.dec" "$here/picasso/DeviceBuild.py" \
   "$mu/Platforms/Xiaomi/picassoPkg/"
cp -r "$mu/Platforms/Xiaomi/lisaPkg/Include" "$mu/Platforms/Xiaomi/picassoPkg/" 2>/dev/null || true
for f in "$mu"/Platforms/Xiaomi/picassoPkg/Include/*; do sed -i 's/lisa/picasso/g' "$f"; done
mkdir -p "$mu/Silicium-ACPI/Platforms/Xiaomi"
[ -d "$mu/Silicium-ACPI/Platforms/Xiaomi/picasso" ] || \
	{ cp -r "$mu/Silicium-ACPI/Platforms/Xiaomi/lisa" "$mu/Silicium-ACPI/Platforms/Xiaomi/picasso"; \
	  for f in $(find "$mu/Silicium-ACPI/Platforms/Xiaomi/picasso" -type f); do sed -i 's/lisa/picasso/g' "$f"; done; }
cp "$here/picasso/Resources/Configs/picasso.toml" "$mu/Resources/Configs/"
[ -f "$mu/Resources/DTBs/picasso.dtb" ] || cp "$here/../dtb/boot_unpacked/dtb" "$mu/Resources/DTBs/picasso.dtb"
echo "installed picasso platform into $mu"
