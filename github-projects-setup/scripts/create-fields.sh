#!/usr/bin/env bash
# create-fields.sh --project-url URL --cycle-days N --cycle-start YYYY-MM-DD|WEEKDAY --out FILE [--name NAME]
# Replaces the Status options, creates Priority, Estimate, Cycle, Target date,
# Due date and Health, then writes the board config (all IDs) to FILE.
source "$(dirname "$0")/common.sh"

url="" days="" start="" out="" name=""
while (($#)); do
  case $1 in
    --project-url) url=$2 ;; --cycle-days) days=$2 ;; --cycle-start) start=$2 ;;
    --out) out=$2 ;; --name) name=$2 ;;
    *) die "unknown arg: $1" ;;
  esac
  shift 2
done
[[ -n $url && -n $days && -n $start && -n $out ]] || die "usage: create-fields.sh --project-url URL --cycle-days N --cycle-start DATE|WEEKDAY --out FILE [--name NAME]"
[[ $days =~ ^[0-9]+$ && $days -ge 1 ]] || die "--cycle-days must be a positive integer"

# A weekday means "the most recent such day, today included", so a cycle is active right away.
if [[ ! $start =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  start=$(jq -rn --arg d "$start" '
    (["sunday","monday","tuesday","wednesday","thursday","friday","saturday"]
      | map(.[0:3]) | index($d | ascii_downcase | .[0:3])) as $want
    | if $want == null then error("bad weekday: \($d)") else . end
    | (now | gmtime) as $t | (($t[6] - $want + 7) % 7) as $back
    | now - $back * 86400 | strftime("%Y-%m-%d")') || die "--cycle-start must be YYYY-MM-DD or a weekday name"
fi

info=$("$SKILL_DIR/scripts/preflight.sh" "$url")
parse_project_url "$url"
project_id=$(jq -r .projectId <<<"$info")
status_id=$(jq -r .statusFieldId <<<"$info")
title=$(jq -r .title <<<"$info")

if [[ -n $name ]]; then
  log "renaming project to \"$name\""
  gql 'mutation($p: ID!, $t: String!) { updateProjectV2(input: {projectId: $p, title: $t}) { projectV2 { title } } }' \
    "$(jq -n --arg p "$project_id" --arg t "$name" '{p: $p, t: $t}')" >/dev/null
  title=$name
fi

log "replacing Status options"
current=$(project_query 'field(name: "Status") { ... on ProjectV2SingleSelectField { options { id name } } }')
opts=$(jq -c --argjson cur "$current" '.status | map(. as $o | . + ({id: ($cur.field.options[] | select(.name == $o.name) | .id)} // {}))' "$DEFAULTS")
gql 'mutation($f: ID!, $o: [ProjectV2SingleSelectFieldOptionInput!]!) {
    updateProjectV2Field(input: {fieldId: $f, singleSelectOptions: $o}) { projectV2Field { ... on ProjectV2SingleSelectField { id } } } }' \
  "$(jq -n --arg f "$status_id" --argjson o "$opts" '{f: $f, o: $o}')" >/dev/null

create_field() { # NAME TYPE [EXTRA_INPUT_JSON]
  log "creating field $1 ($2)"
  gql 'mutation($in: CreateProjectV2FieldInput!) { createProjectV2Field(input: $in) { projectV2Field { ... on ProjectV2FieldCommon { id } } } }' \
    "$(jq -n --arg p "$project_id" --arg n "$1" --arg t "$2" --argjson x "${3:-null}" '{in: ({projectId: $p, name: $n, dataType: $t} + $x)}')" >/dev/null
}

fname() { jq -r ".fieldNames.$1" "$DEFAULTS"; }
create_field "$(fname priority)" SINGLE_SELECT "$(jq -c '{singleSelectOptions: .priority}' "$DEFAULTS")"
create_field "$(fname estimate)" NUMBER
iters=$(jq -cn --arg s "$start" --argjson d "$days" '
  [range(0; 4) as $i | {title: "Cycle \($i + 1)", duration: $d,
    startDate: (($s | strptime("%Y-%m-%d") | mktime) + $i * $d * 86400 | strftime("%Y-%m-%d"))}]')
create_field "$(fname cycle)" ITERATION "$(jq -cn --arg s "$start" --argjson d "$days" --argjson it "$iters" \
  '{iterationConfiguration: {startDate: $s, duration: $d, iterations: $it}}')"
create_field "$(fname targetDate)" DATE
create_field "$(fname dueDate)" DATE
create_field "$(fname health)" SINGLE_SELECT "$(jq -c '{singleSelectOptions: .health}' "$DEFAULTS")"

log "writing config to $out"
fields=$(project_query 'fields(first: 50) { nodes { ... on ProjectV2FieldCommon { id name dataType }
  ... on ProjectV2SingleSelectField { options { id name } } } }')
mkdir -p "$(dirname "$out")"
jq -n --argjson info "$info" --argjson f "$fields" --slurpfile d "$DEFAULTS" \
  --arg title "$title" --arg start "$start" --argjson days "$days" '
  $d[0] as $d
  | def field($k): ($f.fields.nodes[] | select(.name == $d.fieldNames[$k])) as $x
      | {id: $x.id, name: $x.name} + (if $x.options then {options: ($x.options | map({(.name): .id}) | add)} else {} end);
  {
    ownerType: $info.ownerType, owner: $info.owner, projectNumber: $info.projectNumber,
    projectId: $info.projectId, projectUrl: $info.projectUrl, title: $title,
    slug: ($title | ascii_downcase | gsub("[^a-z0-9]+"; "-") | gsub("^-+|-+$"; "")),
    primaryRepo: null, repos: [],
    cycle: {durationDays: $days, startDate: $start},
    fields: ($d.fieldNames | keys_unsorted | map({(.): field(.)}) | add),
    estimateScale: $d.estimateScale, issueTypes: $d.issueTypes, labels: $d.labels, settings: $d.settings
  }' >"$out"
jq -r '"fields ready: " + (.fields | map(.name) | join(", "))' "$out"
