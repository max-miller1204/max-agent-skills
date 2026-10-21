#!/usr/bin/env bash
# preflight.sh PROJECT_URL
# Checks gh auth scopes and that the board is fresh. Prints project info as JSON.
# Exit 2: missing scopes. Exit 3: board is not fresh.
source "$(dirname "$0")/common.sh"
[[ $# -eq 1 ]] || die "usage: preflight.sh PROJECT_URL"
parse_project_url "$1"

scopes=$(gh auth status 2>&1 | sed -n "s/.*Token scopes: //p" | head -1 | tr -d "'" | tr ',' ' ')
missing=()
for s in project repo workflow; do
  [[ " $scopes " == *" $s "* ]] || missing+=("$s")
done
if ((${#missing[@]})); then
  echo "gh token is missing scopes: ${missing[*]}" >&2
  echo "run: gh auth refresh -s $(IFS=,; echo "${missing[*]}")" >&2
  exit 2
fi

p=$(project_query 'id title url items(first: 1) { totalCount }
  fields(first: 50) { nodes { ... on ProjectV2FieldCommon { id name dataType }
    ... on ProjectV2SingleSelectField { options { id name } } } }')
[[ $p != null ]] || die "project not found or no access: $1"

problems=$(jq -r '
  [ (if .items.totalCount > 0 then "board has \(.items.totalCount) item(s)" else empty end),
    (.fields.nodes[] | select(.dataType | IN("TEXT","NUMBER","DATE","ITERATION","MULTI_SELECT","SINGLE_SELECT"))
      | select(.name != "Status") | "custom field \"\(.name)\" (\(.dataType)) exists"),
    (.fields.nodes[] | select(.name == "Status") | select(([.options[].name] | sort) != ["Done","In Progress","Todo"])
      | "Status options were changed: \([.options[].name] | join(", "))")
  ] | .[]' <<<"$p")
if [[ -n $problems ]]; then
  echo "This board is not fresh, so setup will not touch it:" >&2
  sed 's/^/  - /' <<<"$problems" >&2
  echo "github-projects-setup only configures new, empty boards (no migration). Create a new project and try again." >&2
  exit 3
fi

jq --arg t "$OWNER_TYPE" --arg o "$OWNER" --argjson n "$NUMBER" \
  '{ownerType: $t, owner: $o, projectNumber: $n, projectId: .id, title, projectUrl: .url,
    statusFieldId: (.fields.nodes[] | select(.name == "Status") | .id)}' <<<"$p"
