#!/usr/bin/env bash
# link-repos.sh --config FILE --primary OWNER/REPO [OWNER/REPO...]
# Links each repo to the project and records them (and the primary repo) in the config.
source "$(dirname "$0")/common.sh"

config="" primary=""
repos=()
while (($#)); do
  case $1 in
    --config) config=$2; shift 2 ;;
    --primary) primary=$2; shift 2 ;;
    *) repos+=("$1"); shift ;;
  esac
done
[[ -n $config && -n $primary ]] || die "usage: link-repos.sh --config FILE --primary OWNER/REPO [OWNER/REPO...]"
load_config "$config"
[[ " ${repos[*]-} " == *" $primary "* ]] || repos=("$primary" "${repos[@]}")
if [[ $(jq -r .preset "$config") == solo && ${#repos[@]} -gt 1 ]]; then
  die "the solo preset uses exactly one repo; got ${repos[*]}"
fi

for r in "${repos[@]}"; do
  [[ $r == */* ]] || die "repos must be OWNER/REPO: $r"
  rid=$(gh repo view "$r" --json id --jq .id) || die "cannot access repo $r"
  log "linking $r"
  gql 'mutation($p: ID!, $r: ID!) { linkProjectV2ToRepository(input: {projectId: $p, repositoryId: $r}) { repository { nameWithOwner } } }' \
    "$(jq -n --arg p "$PROJECT_ID" --arg r "$rid" '{p: $p, r: $r}')" >/dev/null
done

tmp=$(mktemp)
jq --arg p "$primary" --args '.primaryRepo = $p | .repos = ((.repos + $ARGS.positional) | unique)' "${repos[@]}" <"$config" >"$tmp"
mv "$tmp" "$config"
jq -r '"linked: \(.repos | join(", ")) (primary: \(.primaryRepo))"' "$config"
