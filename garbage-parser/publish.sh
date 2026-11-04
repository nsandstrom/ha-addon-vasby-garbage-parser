#!/usr/bin/env bash
# Bump the add-on version, commit it and tag the commit. Nothing is pushed.
#
# Usage: ./publish.sh patch|minor|major

set -euo pipefail

CONFIG="config.yaml"   # next to this script, in the add-on folder

usage() {
  echo "Usage: $0 patch|minor|major" >&2
  exit 1
}

[ $# -eq 1 ] || usage
BUMP=$1
case "$BUMP" in
  patch|minor|major) ;;
  *) usage ;;
esac

cd "$(dirname "$0")"

[ -f "$CONFIG" ] || { echo "Cannot find $CONFIG" >&2; exit 1; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || { echo "Not inside a git repository" >&2; exit 1; }

# The release commit must contain exactly the version change, and the tag must
# point at a tree that matches what is on disk, so require a clean tree.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "There are uncommitted changes to tracked files. Commit or stash them first." >&2
  exit 1
fi

CURRENT=$(sed -nE 's/^version:[[:space:]]*"?([0-9]+\.[0-9]+\.[0-9]+)"?[[:space:]]*$/\1/p' "$CONFIG" | head -n 1)
[ -n "$CURRENT" ] || { echo "No MAJOR.MINOR.PATCH version found in $CONFIG" >&2; exit 1; }

IFS=. read -r MAJOR MINOR PATCH <<< "$CURRENT"
case "$BUMP" in
  patch) PATCH=$((PATCH + 1)) ;;
  minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
  major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
esac
VERSION="$MAJOR.$MINOR.$PATCH"
TAG="v$VERSION"
MESSAGE="version $VERSION"

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "Tag $TAG already exists." >&2
  exit 1
fi

# Replace only the version number, keeping any quotes and spacing as they were.
TMP=$(mktemp)
sed -E "s/^(version:[[:space:]]*\"?)$CURRENT(\"?[[:space:]]*)$/\1$VERSION\2/" "$CONFIG" > "$TMP"
cat "$TMP" > "$CONFIG"
rm -f "$TMP"

if ! git add -- "$CONFIG" || ! git commit -q -m "$MESSAGE"; then
  git reset -q HEAD -- "$CONFIG" 2>/dev/null || true
  git checkout -q -- "$CONFIG"
  echo "Commit failed. $CONFIG was restored." >&2
  exit 1
fi
git tag "$TAG"

echo "Version $CURRENT -> $VERSION"
echo "Committed \"$MESSAGE\" and tagged $TAG. Nothing was pushed."
