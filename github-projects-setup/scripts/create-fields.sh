#!/usr/bin/env bash
# create-fields.sh --project-url URL --pace timeboxed|self-paced --preset full|solo|hackathon --out FILE
#   [--name NAME] [--cycle-days N --cycle-start YYYY-MM-DD|WEEKDAY] [--wip N] [--due-date on|off]
#   [--end-date YYYY-MM-DD]
# Writes the board config (pace, preset, catalog), then lets board.sh add the Status
# options and the fields that this pace and preset use, and record their IDs in FILE.
source "$(dirname "$0")/common.sh"

url="" pace="" preset="" out="" name="" days="" start="" wip="" due="" end=""
while (($#)); do
  case $1 in
    --project-url) url=$2 ;; --pace) pace=$2 ;; --preset) preset=$2 ;; --out) out=$2 ;; --name) name=$2 ;;
    --cycle-days) days=$2 ;; --cycle-start) start=$2 ;; --wip) wip=$2 ;; --due-date) due=$2 ;; --end-date) end=$2 ;;
    *) die "unknown arg: $1" ;;
  esac
  shift 2
done
[[ -n $url && -n $pace && -n $preset && -n $out ]] ||
  die "usage: create-fields.sh --project-url URL --pace timeboxed|self-paced --preset full|solo|hackathon --out FILE [options]"
[[ $pace == timeboxed || $pace == self-paced ]] || die "--pace must be timeboxed or self-paced"
[[ $preset == full || $preset == solo || $preset == hackathon ]] || die "--preset must be full, solo, or hackathon"
if [[ $pace == timeboxed ]]; then
  [[ -n $days && -n $start ]] || die "Timeboxed needs --cycle-days and --cycle-start"
  [[ $days =~ ^[0-9]+$ && $days -ge 1 ]] || die "--cycle-days must be a positive integer"
else
  [[ $wip =~ ^[0-9]+$ && $wip -ge 1 ]] || die "Self-paced needs --wip N (the max number of In Progress items)"
fi
[[ -z $due || $due == on || $due == off ]] || die "--due-date must be on or off"
[[ -z $end || $preset == hackathon ]] || die "--end-date is for the hackathon preset"
[[ -z $end || $end =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "--end-date must be YYYY-MM-DD"

info=$("$SKILL_DIR/scripts/preflight.sh" "$url")
project_id=$(jq -r .projectId <<<"$info")
title=$(jq -r .title <<<"$info")

if [[ -n $name ]]; then
  log "renaming project to \"$name\""
  gql 'mutation($p: ID!, $t: String!) { updateProjectV2(input: {projectId: $p, title: $t}) { projectV2 { title } } }' \
    "$(jq -n --arg p "$project_id" --arg t "$name" '{p: $p, t: $t}')" >/dev/null
  title=$name
fi

log "writing config to $out ($preset, $pace)"
mkdir -p "$(dirname "$out")"
jq -n --argjson info "$info" --slurpfile d "$DEFAULTS" --arg title "$title" --arg pace "$pace" --arg preset "$preset" \
  --arg days "$days" --arg start "$start" --arg wip "$wip" --arg due "$due" --arg end "$end" '
  {
    ownerType: $info.ownerType, owner: $info.owner, projectNumber: $info.projectNumber,
    projectId: $info.projectId, projectUrl: $info.projectUrl, title: $title,
    slug: ($title | ascii_downcase | gsub("[^a-z0-9]+"; "-") | gsub("^-+|-+$"; "")),
    pace: $pace, preset: $preset,
    wipLimit: (if $wip == "" then null else ($wip | tonumber) end),
    endDate: (if $end == "" then null else $end end),
    flags: {dueDate: ($due == "on")},
    primaryRepo: null, repos: [],
    cycle: (if $pace == "timeboxed" then {durationDays: ($days | tonumber), startDate: $start} else null end),
    fields: {status: {id: $info.statusFieldId, name: "Status"}},
    catalog: $d[0]
  }' >"$out"

bash "$BOARD" --config "$out" schema-sync --fields --fresh
jq -r '"fields ready: " + ([.fields[].name] | join(", ")) + "\nstatuses: " + (.fields.status.options | keys_unsorted | join(", "))' "$out"
