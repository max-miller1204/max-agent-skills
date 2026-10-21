#!/usr/bin/env bash
# summary.sh --config FILE
# Prints what the board now has (fields, views, repos, built-in workflows, secrets)
# followed by the manual steps that the API cannot do.
source "$(dirname "$0")/common.sh"
[[ ${1:-} == --config && -n ${2:-} ]] || die "usage: summary.sh --config FILE"
load_config "$2"

p=$(project_query 'title url
  fields(first: 50) { nodes { ... on ProjectV2FieldCommon { name dataType } ... on ProjectV2SingleSelectField { options { name } } } }
  views(first: 50) { nodes { number name layout filter } }
  repositories(first: 50) { nodes { nameWithOwner } }
  workflows(first: 50) { nodes { name enabled } }')

jq -r '
  "# \(.title)\n\(.url)\n",
  "## Fields",
  (.fields.nodes[] | select(.dataType | IN("SINGLE_SELECT","NUMBER","DATE","ITERATION"))
    | "- \(.name) (\(.dataType | ascii_downcase))\(if .options then ": " + ([.options[].name] | join(", ")) else "" end)"),
  "", "## Views",
  (.views.nodes | sort_by(.number) | .[] | "- \(.name) [\(.layout | sub("_LAYOUT"; "") | ascii_downcase)] \(.filter // "(no filter)")"),
  "", "## Linked repos",
  (.repositories.nodes[] | "- \(.nameWithOwner)"),
  "", "## Built-in workflows (current state)",
  (.workflows.nodes[] | "- \(.name): \(if .enabled then "on" else "off" end)")' <<<"$p"

echo
echo "## Actions secret"
for r in $(jq -r '.repos[]' "$CONFIG"); do
  if gh secret list -R "$r" --json name --jq '.[].name' 2>/dev/null | grep -qx PROJECT_TOKEN; then
    echo "- $r: PROJECT_TOKEN set"
  else
    echo "- $r: PROJECT_TOKEN MISSING"
  fi
done
echo

secret_cmds=$(jq -r '.repos[] | "gh secret set PROJECT_TOKEN -R \(.)"' "$CONFIG")
jq -rn --rawfile t "$SKILL_DIR/references/manual-steps.md" --arg url "$PROJECT_URL" \
  --arg repos "$(jq -r '.repos | join(", ")' "$CONFIG")" --arg days "$(jq -r .settings.archiveDays "$CONFIG")" \
  --arg sec "$secret_cmds" \
  '$t | gsub("__PROJECT_URL__"; $url) | gsub("__REPOS__"; $repos) | gsub("__ARCHIVE_DAYS__"; $days) | gsub("__SECRET_COMMANDS__"; $sec)'
