#!/bin/sh
# Download a GitHub Actions artifact with parallel connections (aria2c).
# The runner artifacts live on Azure blob storage; a single curl stream is
# often 10x slower than 16 ranged connections.
#
#   tools/gh-artifact.sh RUN_ID NAME OUT.zip
#
# Needs gh (authenticated) and aria2c.
set -eu
run=${1:?usage: gh-artifact.sh RUN_ID NAME OUT.zip}
name=${2:?usage: gh-artifact.sh RUN_ID NAME OUT.zip}
out=${3:?usage: gh-artifact.sh RUN_ID NAME OUT.zip}

# Prefer an explicit GH_TOKEN (a locked keyring makes `gh auth token` hang).
token=${GH_TOKEN:-${GITHUB_TOKEN:-}}
if [ -z "$token" ] && [ -r /tmp/ghtoken ]; then
	token=$(cat /tmp/ghtoken)
fi
if [ -n "$token" ]; then
	export GH_TOKEN="$token"
fi

url=$(gh api "repos/{owner}/{repo}/actions/runs/$run/artifacts" \
	--jq ".artifacts[] | select(.name == \"$name\") | .archive_download_url")
[ -n "$url" ] || { echo "no artifact named $name in run $run" >&2; exit 1; }

# The API answers with a redirect to a signed Azure blob URL; resolve it with
# curl (one cheap request) and let aria2 use 16 ranged connections, without
# the API token, which the blob storage rejects.
blob=$(curl -s -o /dev/null -w '%{redirect_url}' -H "Authorization: token $GH_TOKEN" "$url")
[ -n "$blob" ] || { echo "no redirect from the artifact API" >&2; exit 1; }
# Azure honours ranged requests but throttles bursts into short reads, so the
# size is verified and the attempt repeated with fewer connections and a fresh
# (signed) URL. 8 parallel streams is the fastest reliable setting here.
got=0
for conn in 8 4 1; do
	blob=$(curl -s -o /dev/null -w '%{redirect_url}' -H "Authorization: token $GH_TOKEN" "$url")
	[ -n "$blob" ] || { echo "no redirect from the artifact API" >&2; exit 1; }
	want=$(curl -sI "$blob" | sed -n 's/^[Cc]ontent-[Ll]ength: *\([0-9]*\).*/\1/p' | tr -d '\r')
	[ -n "$want" ] || { echo "no Content-Length for the blob" >&2; exit 1; }
	rm -f "$out"
	aria2c -x"$conn" -s"$conn" -k1M --max-tries=3 --allow-overwrite=true \
		--file-allocation=none -d "$(dirname "$out")" -o "$(basename "$out")" "$blob" >/dev/null 2>&1 || true
	got=$(stat -c %s "$out" 2>/dev/null || echo 0)
	[ "$got" = "$want" ] && break
	echo "short read with $conn connections: $got of $want" >&2
done
[ "$got" = "$want" ] || { echo "download incomplete after retries" >&2; exit 1; }
ls -l "$out"
