#!/usr/bin/env bash
# Create public repo (if missing) and push. Requires a GitHub PAT with repo access.
#
# Classic PAT: enable "repo" scope
# Fine-grained PAT: Repository access "All repositories", Permissions:
#   - Contents: Read and write
#   - Administration: Read and write (to create new repo)
#
# Usage:
#   export GITHUB_TOKEN='github_pat_...'
#   ./scripts/publish.sh

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "Set GITHUB_TOKEN first (do not commit it)."
  exit 1
fi

USER="$(curl -s -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/user | python3 -c "import sys,json; print(json.load(sys.stdin).get('login',''))")"
if [[ -z "$USER" || "$USER" == "None" ]]; then
  echo "Invalid GITHUB_TOKEN (401). Create a new PAT with repo permissions."
  exit 1
fi

REPO="btc-predict"
REMOTE="https://x-access-token:${GITHUB_TOKEN}@github.com/${USER}/${REPO}.git"

echo "Authenticated as: $USER"

STATUS="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $GITHUB_TOKEN" "https://api.github.com/repos/${USER}/${REPO}")"
if [[ "$STATUS" == "404" ]]; then
  echo "Creating public repo ${USER}/${REPO}..."
  CREATE="$(curl -s -X POST -H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github+json" \
    https://api.github.com/user/repos \
    -d "{\"name\":\"${REPO}\",\"description\":\"Local BTC day-trading intelligence: news, signals, backtests\",\"private\":false}")"
  URL="$(echo "$CREATE" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r.get('html_url') or '')")"
  if [[ -z "$URL" ]]; then
    echo "Create failed. Response:"
    echo "$CREATE"
    echo ""
    echo "Create the empty repo manually at https://github.com/new (name: btc-predict), then re-run."
    exit 1
  fi
  echo "Created: $URL"
elif [[ "$STATUS" != "200" ]]; then
  echo "Unexpected repo check status: $STATUS"
  exit 1
else
  echo "Repo exists: https://github.com/${USER}/${REPO}"
fi

git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/${USER}/${REPO}.git"
git push "https://x-access-token:${GITHUB_TOKEN}@github.com/${USER}/${REPO}.git" main

echo ""
echo "Done: https://github.com/${USER}/${REPO}"
