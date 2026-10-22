#!/bin/sh
# Dry-run a patch against the real xnu tree that CI builds, not against a
# tree reconstructed from the patch itself: a synthetic base cannot show a
# wrong context, and CI then silently skips the patch ("patch failed").
#
#   tools/check-patch.sh xnu/patches/0088-*.patch buildtree.tgz
#
# Get buildtree.tgz from a run whose kernel artifact exists:
#   gh api repos/<owner>/<repo>/actions/runs/<id>/artifacts \
#     --jq '.artifacts[]|select(.name=="xnu-buildtree").archive_download_url' \
#     | xargs curl -sL -H "Authorization: token $(gh auth token)" -o buildtree.zip
#   unzip -o buildtree.zip          # -> buildtree.tgz
set -eu

patch_file=${1:?usage: check-patch.sh PATCH buildtree.tgz}
tgz=${2:?usage: check-patch.sh PATCH buildtree.tgz}
[ -f "$patch_file" ] || { echo "no such patch: $patch_file" >&2; exit 1; }
[ -f "$tgz" ] || { echo "no such build tree: $tgz" >&2; exit 1; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# The files this patch set touches; --strip-components drops the top dir.
# New files (/dev/null) have nothing to extract.
files=$(sed -n 's|^--- a/||p' "$patch_file" | sed 's|^xnu/||' | sort -u)
set --
for f in $files; do
	set -- "$@" "xnu/$f"
done
tar xzf "$tgz" -C "$tmp" --strip-components=1 "$@" 2>/dev/null || true

if patch -p1 --dry-run -i "$(cd "$(dirname "$patch_file")" && pwd)/$(basename "$patch_file")" -d "$tmp" 2>&1; then
	echo "OK: applies cleanly"
else
	echo "FAILED: patch does not apply to the CI tree" >&2
	exit 1
fi
