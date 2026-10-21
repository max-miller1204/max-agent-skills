#!/usr/bin/env bash
# setup-repo.sh --config FILE OWNER/REPO [--check-secret]
# Creates the standard labels in the repo. With --check-secret, only reports
# whether the PROJECT_TOKEN Actions secret exists (exit 4 if not).
source "$(dirname "$0")/common.sh"

config="" repo="" check=false
while (($#)); do
  case $1 in
    --config) config=$2; shift 2 ;;
    --check-secret) check=true; shift ;;
    *) repo=$1; shift ;;
  esac
done
[[ -n $config && -n $repo ]] || die "usage: setup-repo.sh --config FILE OWNER/REPO [--check-secret]"
load_config "$config"

if [[ $check == true ]]; then
  if gh secret list -R "$repo" --json name --jq '.[].name' | grep -qx PROJECT_TOKEN; then
    echo "PROJECT_TOKEN is set in $repo"
  else
    echo "PROJECT_TOKEN is NOT set in $repo. Run: gh secret set PROJECT_TOKEN -R $repo" >&2
    exit 4
  fi
  exit 0
fi

jq -c '.labels[]' "$config" | while read -r l; do
  gh label create "$(jq -r .name <<<"$l")" -R "$repo" --color "$(jq -r .color <<<"$l")" \
    --description "$(jq -r .description <<<"$l")" --force >/dev/null
done
echo "labels ready in $repo: $(jq -r '[.labels[].name] | join(", ")' "$config")"
