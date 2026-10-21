#!/usr/bin/env bash
# create-views.sh --config FILE
# Creates the Linear-style views (layout, filter, visible fields). The default
# "View 1" becomes Triage. Grouping, sorting, field sums and roadmap dates are not
# exposed by the API; summary.sh prints those as manual steps. "Overdue / SLA" uses the
# overdue and sla-breach labels kept current by the urgent-sla Action, because project
# filters cannot OR across different fields (due date vs label).
source "$(dirname "$0")/common.sh"
[[ ${1:-} == --config && -n ${2:-} ]] || die "usage: create-views.sh --config FILE"
load_config "$2"

p=$(project_query 'fields(first: 50) { nodes { ... on ProjectV2FieldCommon { id name } } }
  views(first: 50) { nodes { id name number } }')
fid() { jq -r --arg n "$1" '.fields.nodes[] | select(.name == $n) | .id' <<<"$p"; }
ids() { local out=() n; for n in "$@"; do out+=("$(fid "$n")"); done; printf '%s\n' "${out[@]}" | jq -R . | jq -sc 'map(select(. != ""))'; }

# name | layout | filter | visible fields (comma separated, table/board only)
VIEWS=$(cat <<'V'
Triage|TABLE_LAYOUT|status:Triage|Title,Labels,Priority,Estimate,Repository,Created
Backlog|TABLE_LAYOUT|status:Backlog|Title,Priority,Estimate,Labels,Assignees,Cycle
Active cycle|BOARD_LAYOUT|cycle:@current|Title,Assignees,Priority,Estimate,Labels
Upcoming cycle|BOARD_LAYOUT|cycle:@next|Title,Assignees,Priority,Estimate,Labels
My issues|TABLE_LAYOUT|assignee:@me -status:Done,Canceled|Title,Status,Priority,Estimate,Cycle,Due date
All issues|TABLE_LAYOUT||Title,Status,Priority,Estimate,Assignees,Cycle,Labels,Due date
Projects|TABLE_LAYOUT|label:project|Title,Assignees,Status,Health,Target date,Sub-issues progress
Roadmap|ROADMAP_LAYOUT|label:project|
Cycles|TABLE_LAYOUT|has:cycle|Title,Cycle,Status,Estimate,Assignees
Overdue / SLA|TABLE_LAYOUT|label:overdue,sla-breach -status:Done,Canceled|Title,Labels,Due date,Priority,Status,Assignees,Updated
V
)

first_view=$(jq -r '.views.nodes | sort_by(.number) | .[0] | select(.name == "View 1") | .id // empty' <<<"$p")

while IFS='|' read -r name layout filter fields; do
  if jq -e --arg n "$name" '.views.nodes[] | select(.name == $n)' <<<"$p" >/dev/null; then
    log "view \"$name\" exists, skipping"; continue
  fi
  conf=null
  if [[ -n $fields ]]; then
    IFS=, read -ra fl <<<"$fields"
    conf=$(jq -cn --argjson v "$(ids "${fl[@]}")" '{visibleFieldIds: $v}')
  fi
  if [[ -n $first_view ]]; then
    vid=$first_view; first_view=""
    log "renaming View 1 to \"$name\""
  else
    log "creating view \"$name\""
    vid=$(gql 'mutation($p: ID!, $n: String!, $l: ProjectV2ViewLayout!, $c: ProjectV2ViewConfigurationInput) {
        createProjectV2View(input: {projectId: $p, name: $n, layout: $l, configuration: $c}) { projectV2View { id } } }' \
      "$(jq -n --arg p "$PROJECT_ID" --arg n "$name" --arg l "$layout" --argjson c "$conf" '{p: $p, n: $n, l: $l, c: $c}')" |
      jq -r .data.createProjectV2View.projectV2View.id)
    conf=null
  fi
  gql 'mutation($v: ID!, $n: String, $l: ProjectV2ViewLayout, $f: String, $c: ProjectV2ViewConfigurationInput) {
      updateProjectV2View(input: {viewId: $v, name: $n, layout: $l, filter: $f, configuration: $c}) { projectV2View { id } } }' \
    "$(jq -n --arg v "$vid" --arg n "$name" --arg l "$layout" --arg f "$filter" --argjson c "$conf" \
      '{v: $v, n: $n, l: $l, f: $f, c: $c}')" >/dev/null
done <<<"$VIEWS"

project_query 'views(first: 50) { nodes { number name layout filter } }' |
  jq -r '.views.nodes | sort_by(.number) | .[] | "view \(.number): \(.name) [\(.layout | sub("_LAYOUT"; "") | ascii_downcase)] \(.filter // "")"'
