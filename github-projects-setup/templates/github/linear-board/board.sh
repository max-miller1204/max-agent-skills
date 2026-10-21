#!/usr/bin/env bash
# board.sh: Linear-style operations on a GitHub Projects (v2) board.
#
# Shared by the GitHub Actions workflows (.github/workflows/linear-*.yml) and
# the project-local board skill. All IDs come from the config file, so nothing
# is rediscovered at runtime.
#
# Config lookup: --config FILE, then $LINEAR_BOARD_CONFIG, then
# <repo root>/.github/linear-board.json.
#
# Requires: gh (authenticated with project + repo scopes), jq.
set -euo pipefail

die() { echo "board: $*" >&2; exit 1; }
log() { echo "board: $*" >&2; }

usage() {
  cat <<'EOF'
Usage: board.sh [--config FILE] <command> [args]

Read
  items [--repo R] [--status S]     Board items as JSON (archived items excluded)
  show <ref>                        One item as JSON
  triage                            Triage inbox plus items labeled stale
  cycles                            Cycle iterations (completed, current, upcoming)
  projects                          Parent "project" issues with lead, target, health, progress
  overdue                           Overdue items and SLA breaches
  plan [--cycle @next]              Capacity check: cycle scope vs recent velocity
  review [--cycle @last]            Cycle review: completed, rolled over, scope change

Write
  set <ref> key=value...            Keys: status priority estimate cycle due target health
  accept <ref> [Backlog|Todo]       Accept a triage item (default Backlog), clears stale
  decline <ref> [reason]            Canceled + close as not planned
  duplicate <ref> <of-ref>          Canceled + close as duplicate of <of-ref>
  create --title T [--repo R] [--body B] [--type bug|feature|improvement|chore]
         [--priority P] [--estimate N] [--cycle @current|@next|TITLE] [--due YYYY-MM-DD]
         [--parent REF] [--status S] [--assignee LOGIN]
  project-create --title T [--repo R] [--body B] [--lead LOGIN] [--target YYYY-MM-DD]
         [--health "On track"|"At risk"|"Off track"]
  project-add <parent-ref> <child-ref>   Make child a sub-issue of a project
  status-update --status ON_TRACK|AT_RISK|OFF_TRACK|COMPLETE|INACTIVE --body B
         [--start YYYY-MM-DD] [--target YYYY-MM-DD]
  labels-sync                       Create or update the standard labels in every linked repo

Automation (used by GitHub Actions)
  git-sync                          Branch / PR / issue events -> Status
  rollover                          Move unfinished items out of ended cycles, keep cycles ahead
  stale [--days 60] [--repo R]      Label untouched Backlog items stale
  sla [--hours 24] [--repo R]       Label Urgent items with no recent activity sla-breach
  flag-overdue [--repo R]           Label items past their due date overdue (and clear it)
  digest [--dry-run]                Post a weekly project status update

Refs: owner/repo#12, repo#12, #12 or 12 (defaults to $GITHUB_REPOSITORY or the primary repo).
EOF
}

# ---------------------------------------------------------------- config

CONFIG=${LINEAR_BOARD_CONFIG:-}
if [[ ${1:-} == --config ]]; then CONFIG=$2; shift 2; fi
if [[ -z $CONFIG ]]; then
  root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
  CONFIG=$root/.github/linear-board.json
fi
[[ -f $CONFIG ]] || die "config not found: $CONFIG"
command -v jq >/dev/null || die "jq is required"
command -v gh >/dev/null || die "gh is required"
if [[ -n ${GITHUB_ACTIONS:-} && -z ${GH_TOKEN:-} ]]; then
  die "GH_TOKEN is empty. Set the PROJECT_TOKEN secret: gh secret set PROJECT_TOKEN -R ${GITHUB_REPOSITORY:-<repo>}"
fi

cfg() { jq -r "$1" "$CONFIG"; }
PROJECT_ID=$(cfg .projectId)
OWNER=$(cfg .owner)
PRIMARY_REPO=$(cfg .primaryRepo)
TODAY=$(date -u +%Y-%m-%d)

field_id() { cfg ".fields.$1.id"; }
field_name() { cfg ".fields.$1.name"; }

# option_id FIELD_KEY NAME: case-insensitive lookup of a single-select option ID.
option_id() {
  local id
  id=$(jq -r --arg f "$1" --arg o "$2" \
    '.fields[$f].options | to_entries[] | select((.key|ascii_downcase) == ($o|ascii_downcase)) | .value' "$CONFIG")
  [[ -n $id ]] || die "unknown $1 option: $2 (valid: $(jq -r --arg f "$1" '.fields[$f].options | keys_unsorted | join(", ")' "$CONFIG"))"
  echo "$id"
}

# Canonical option name (case as configured).
option_name() {
  jq -r --arg f "$1" --arg o "$2" \
    '.fields[$f].options | keys_unsorted[] | select(ascii_downcase == ($o|ascii_downcase))' "$CONFIG"
}

# ---------------------------------------------------------------- graphql

# gql QUERY [VARIABLES_JSON]
gql() {
  local vars=${2:-}
  [[ -n $vars ]] || vars='{}'
  jq -n --arg q "$1" --argjson v "$vars" '{query: $q, variables: $v}' | gh api graphql --input -
}

ITEM_FIELDS='
  id isArchived type
  content {
    __typename
    ... on DraftIssue { title createdAt updatedAt }
    ... on Issue {
      id number title state stateReason url createdAt updatedAt closedAt
      repository { nameWithOwner }
      labels(first: 30) { nodes { name } }
      assignees(first: 10) { nodes { login } }
      parent { number repository { nameWithOwner } }
      subIssuesSummary { total completed percentCompleted }
    }
    ... on PullRequest {
      id number title state url createdAt updatedAt closedAt
      repository { nameWithOwner }
      labels(first: 30) { nodes { name } }
      assignees(first: 10) { nodes { login } }
    }
  }
  fieldValues(first: 30) {
    nodes {
      __typename
      ... on ProjectV2ItemFieldSingleSelectValue { name updatedAt creator { login } field { ... on ProjectV2FieldCommon { id } } }
      ... on ProjectV2ItemFieldNumberValue { number field { ... on ProjectV2FieldCommon { id } } }
      ... on ProjectV2ItemFieldDateValue { date field { ... on ProjectV2FieldCommon { id } } }
      ... on ProjectV2ItemFieldIterationValue { iterationId title startDate duration updatedAt field { ... on ProjectV2FieldCommon { id } } }
    }
  }'

# jq program that flattens a raw item node. Expects $f (config .fields) as an argument.
NORMALIZE='
  def fv($k): [.fieldValues.nodes[] | select(.field.id? == $f[$k].id)][0];
  {
    itemId: .id,
    archived: .isArchived,
    type: .type,
    contentId: .content.id,
    repo: .content.repository.nameWithOwner,
    number: .content.number,
    ref: (if .content.repository then "\(.content.repository.nameWithOwner)#\(.content.number)" else null end),
    title: .content.title,
    state: .content.state,
    stateReason: .content.stateReason,
    url: .content.url,
    createdAt: .content.createdAt,
    updatedAt: .content.updatedAt,
    closedAt: .content.closedAt,
    labels: [.content.labels.nodes[]?.name],
    assignees: [.content.assignees.nodes[]?.login],
    parent: (if .content.parent then "\(.content.parent.repository.nameWithOwner)#\(.content.parent.number)" else null end),
    subIssues: .content.subIssuesSummary,
    status: fv("status").name,
    statusSetAt: fv("status").updatedAt,
    statusSetBy: fv("status").creator.login,
    priority: fv("priority").name,
    estimate: fv("estimate").number,
    cycle: (fv("cycle") | if . then {id: .iterationId, title, startDate, duration, setAt: .updatedAt} else null end),
    dueDate: fv("dueDate").date,
    targetDate: fv("targetDate").date,
    health: fv("health").name
  }'

# All non-archived items, normalized, as one JSON array.
items_json() {
  local cursor=null page fields
  fields=$(jq -c .fields "$CONFIG")
  {
    while :; do
      page=$(gql "query(\$id: ID!, \$after: String) { node(id: \$id) { ... on ProjectV2 {
          items(first: 100, after: \$after) { pageInfo { hasNextPage endCursor } nodes { $ITEM_FIELDS } } } } }" \
        "$(jq -n --arg id "$PROJECT_ID" --argjson after "$cursor" '{id: $id, after: $after}')")
      jq -c --argjson f "$fields" ".data.node.items.nodes[] | select(.isArchived | not) | $NORMALIZE" <<<"$page"
      [[ $(jq -r .data.node.items.pageInfo.hasNextPage <<<"$page") == true ]] || break
      cursor=$(jq '.data.node.items.pageInfo.endCursor' <<<"$page")
    done
  } | jq -s .
}

# ---------------------------------------------------------------- refs and items

default_repo() { echo "${BOARD_REPO:-${GITHUB_REPOSITORY:-$PRIMARY_REPO}}"; }

# parse_ref REF -> sets REF_REPO, REF_NUM
parse_ref() {
  local r=$1
  if [[ $r == *#* ]]; then REF_REPO=${r%%#*}; REF_NUM=${r##*#}; else REF_REPO=""; REF_NUM=$r; fi
  [[ -n $REF_REPO ]] || REF_REPO=$(default_repo)
  [[ $REF_REPO == */* ]] || REF_REPO=$OWNER/$REF_REPO
  [[ $REF_NUM =~ ^[0-9]+$ ]] || die "bad issue ref: $r"
}

# content_json REPO NUM -> {id, typename, state, items: [{id, isArchived, projectId}]}
content_json() {
  gql 'query($o: String!, $n: String!, $num: Int!) { repository(owner: $o, name: $n) {
      issueOrPullRequest(number: $num) {
        __typename
        ... on Issue { id state projectItems(first: 20, includeArchived: true) { nodes { id isArchived project { id } } } }
        ... on PullRequest { id state projectItems(first: 20, includeArchived: true) { nodes { id isArchived project { id } } } }
      } } }' \
    "$(jq -n --arg o "${1%%/*}" --arg n "${1##*/}" --argjson num "$2" '{o: $o, n: $n, num: $num}')" |
    jq '.data.repository.issueOrPullRequest | {id, typename: .__typename, state, items: [.projectItems.nodes[] | {id, isArchived, projectId: .project.id}]}'
}

# ensure_item REF -> prints the project item ID, adding (or unarchiving) the issue if needed.
# It runs in a command substitution, so callers that need REF_REPO/REF_NUM call parse_ref themselves.
ensure_item() {
  parse_ref "$1"
  local c item archived
  c=$(content_json "$REF_REPO" "$REF_NUM")
  [[ $(jq -r .id <<<"$c") != null ]] || die "not found: $REF_REPO#$REF_NUM"
  item=$(jq -r --arg p "$PROJECT_ID" '[.items[] | select(.projectId == $p)][0].id // empty' <<<"$c")
  archived=$(jq -r --arg p "$PROJECT_ID" '[.items[] | select(.projectId == $p)][0].isArchived // false' <<<"$c")
  if [[ -z $item ]]; then
    item=$(gql 'mutation($p: ID!, $c: ID!) { addProjectV2ItemById(input: {projectId: $p, contentId: $c}) { item { id } } }' \
      "$(jq -n --arg p "$PROJECT_ID" --arg c "$(jq -r .id <<<"$c")" '{p: $p, c: $c}')" | jq -r .data.addProjectV2ItemById.item.id)
  elif [[ $archived == true ]]; then
    gql 'mutation($p: ID!, $i: ID!) { unarchiveProjectV2Item(input: {projectId: $p, itemId: $i}) { item { id } } }' \
      "$(jq -n --arg p "$PROJECT_ID" --arg i "$item" '{p: $p, i: $i}')" >/dev/null
  fi
  echo "$item"
}

# item_json ITEM_ID -> normalized item
item_json() {
  gql "query(\$id: ID!) { node(id: \$id) { ... on ProjectV2Item { $ITEM_FIELDS } } }" "$(jq -n --arg id "$1" '{id: $id}')" |
    jq --argjson f "$(jq -c .fields "$CONFIG")" ".data.node | $NORMALIZE"
}

# set_value ITEM FIELD_KEY VALUE_JSON  (VALUE_JSON is a ProjectV2FieldValue, e.g. {"date":"2026-01-01"})
set_value() {
  gql 'mutation($p: ID!, $i: ID!, $f: ID!, $v: ProjectV2FieldValue!) {
      updateProjectV2ItemFieldValue(input: {projectId: $p, itemId: $i, fieldId: $f, value: $v}) { projectV2Item { id } } }' \
    "$(jq -n --arg p "$PROJECT_ID" --arg i "$1" --arg f "$(field_id "$2")" --argjson v "$3" '{p: $p, i: $i, f: $f, v: $v}')" >/dev/null
}

clear_value() {
  gql 'mutation($p: ID!, $i: ID!, $f: ID!) { clearProjectV2ItemFieldValue(input: {projectId: $p, itemId: $i, fieldId: $f}) { projectV2Item { id } } }' \
    "$(jq -n --arg p "$PROJECT_ID" --arg i "$1" --arg f "$(field_id "$2")" '{p: $p, i: $i, f: $f}')" >/dev/null
}

set_select() {
  local opt
  opt=$(option_id "$2" "$3") || exit 1
  set_value "$1" "$2" "$(jq -n --arg o "$opt" '{singleSelectOptionId: $o}')"
}

check_date() { [[ $1 =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "dates must be YYYY-MM-DD: $1"; }

# set_field ITEM KEY VALUE (VALUE "none" or "" clears the field)
set_field() {
  local item=$1 key=$2 val=$3
  case $key in
    status) key=status ;;
    priority) key=priority ;;
    estimate) key=estimate ;;
    cycle | iteration) key=cycle ;;
    due | dueDate | due-date) key=dueDate ;;
    target | targetDate | target-date) key=targetDate ;;
    health) key=health ;;
    *) die "unknown field: $key" ;;
  esac
  if [[ -z $val || $val == none ]]; then clear_value "$item" "$key"; return; fi
  case $key in
    status | priority | health) set_select "$item" "$key" "$val" ;;
    estimate)
      jq -e --argjson v "$val" '.estimateScale | index($v)' "$CONFIG" >/dev/null 2>&1 ||
        die "estimate must be one of $(cfg '.estimateScale | map(tostring) | join(", ")')"
      set_value "$item" estimate "$(jq -n --argjson v "$val" '{number: $v}')" ;;
    cycle) set_value "$item" cycle "$(jq -n --arg v "$(resolve_cycle "$val" | jq -r .id)" '{iterationId: $v}')" ;;
    dueDate | targetDate) check_date "$val"; set_value "$item" "$key" "$(jq -n --arg v "$val" '{date: $v}')" ;;
  esac
}

add_label() { gh issue edit "$2" -R "$1" --add-label "$3" >/dev/null; }
remove_label() { gh issue edit "$2" -R "$1" --remove-label "$3" >/dev/null 2>&1 || true; }

# ---------------------------------------------------------------- cycles

iterations_json() {
  gql 'query($id: ID!) { node(id: $id) { ... on ProjectV2IterationField { configuration {
      duration startDay
      iterations { id title startDate duration }
      completedIterations { id title startDate duration } } } } }' \
    "$(jq -n --arg id "$(field_id cycle)" '{id: $id}')" |
    jq --arg today "$TODAY" '
      def enddate: (.startDate | strptime("%Y-%m-%d") | mktime) + .duration * 86400 | strftime("%Y-%m-%d");
      .data.node.configuration
      | .completedIterations |= (map(. + {endDate: enddate}) | sort_by(.startDate))
      | .iterations |= (map(. + {endDate: enddate}) | sort_by(.startDate))
      | .current = ([.iterations[] | select(.startDate <= $today and .endDate > $today)][0])
      | .upcoming = [.iterations[] | select(.startDate > $today)]
      | .completedIterations += [.iterations[] | select(.endDate <= $today)]
      | .iterations |= map(select(.endDate > $today))'
}

# resolve_cycle @current|@next|@last|TITLE -> iteration JSON
resolve_cycle() {
  local it
  it=$(iterations_json | jq --arg c "$1" '
    if $c == "@current" then .current
    elif $c == "@next" then .upcoming[0]
    elif $c == "@last" then .completedIterations[-1]
    else ([.completedIterations[], .iterations[]] | map(select((.title|ascii_downcase) == ($c|ascii_downcase))))[0]
    end')
  [[ $it != null ]] || die "no cycle matches $1"
  echo "$it"
}

# Keep at least $1 cycles scheduled after the current one.
ensure_future_cycles() {
  local want=${1:-3} it n
  it=$(iterations_json)
  n=$(jq '.upcoming | length' <<<"$it")
  ((n >= want)) && return 0
  log "adding $((want - n)) upcoming cycle(s)"
  local cfgin
  cfgin=$(jq --argjson want "$want" --argjson dur "$(cfg .cycle.durationDays)" '
    def num: (.title | capture("(?<n>[0-9]+)").n // "0" | tonumber);
    def plus($d): (strptime("%Y-%m-%d") | mktime) + $d * 86400 | strftime("%Y-%m-%d");
    ([.completedIterations[], .iterations[]] | unique_by(.id) | sort_by(.startDate)) as $all
    | ($all | map(num) | max // 0) as $maxn
    | ($all[-1]) as $last
    | (reduce range(0; $want - (.upcoming | length)) as $k (
        {list: [], start: ($last.startDate | plus($last.duration))};
        .list += [{startDate: .start, duration: $dur, title: "Cycle \($maxn + 1 + $k)"}]
        | .start = (.start | plus($dur)))) as $new
    | {startDate: $all[0].startDate, duration: $dur,
       iterations: (($all | map({id, startDate, duration, title})) + $new.list)}' <<<"$it")
  gql 'mutation($f: ID!, $c: ProjectV2IterationFieldConfigurationInput!) {
      updateProjectV2Field(input: {fieldId: $f, iterationConfiguration: $c}) { projectV2Field { ... on ProjectV2IterationField { id } } } }' \
    "$(jq -n --arg f "$(field_id cycle)" --argjson c "$cfgin" '{f: $f, c: $c}')" >/dev/null
}

# ---------------------------------------------------------------- read commands

ACTIVE='(.status != "Done" and .status != "Canceled" and .state != "CLOSED" and .state != "MERGED")'

fmt_items() { # JSON array on stdin -> aligned text lines
  jq -r '.[] | [(.ref // "draft"), (.status // "-"), (.priority // "-"), ((.estimate // "-")|tostring),
    (.cycle.title // "-"), (.dueDate // "-"), ((.labels | join(",")) | if . == "" then "-" else . end), .title] | @tsv' |
    column -t -s $'\t'
}

cmd_items() {
  local repo="" status=""
  while (($#)); do case $1 in --repo) repo=$2; shift 2 ;; --status) status=$2; shift 2 ;; *) die "items: unknown arg $1" ;; esac; done
  items_json | jq --arg r "$repo" --arg s "$status" 'map(select(($r == "" or .repo == $r) and ($s == "" or ((.status // "")|ascii_downcase) == ($s|ascii_downcase))))'
}

cmd_show() { item_json "$(ensure_item "$1")"; }

cmd_triage() {
  local all
  all=$(items_json)
  echo "## Triage"
  jq 'map(select(.status == "Triage" or (.status == null and .state == "OPEN"))) | sort_by(.createdAt)' <<<"$all" | fmt_items
  echo
  echo "## Stale (Backlog, untouched $(cfg .settings.staleDays)+ days)"
  jq "map(select((.labels | index(\"stale\")) and $ACTIVE))" <<<"$all" | fmt_items
}

cmd_cycles() {
  iterations_json | jq -r '
    (.completedIterations[] | "done     \(.title)  \(.startDate) -> \(.endDate)"),
    (.iterations[] | "\(if .startDate <= (now|strftime("%Y-%m-%d")) then "current " else "upcoming" end) \(.title)  \(.startDate) -> \(.endDate)")'
}

cmd_projects() {
  items_json | jq -r 'map(select(.labels | index("project"))) | sort_by(.targetDate // "9999") | .[] |
    [.ref, (.status // "-"), (.health // "-"), (.targetDate // "-"),
     ((.assignees | join(",")) | if . == "" then "no lead" else . end),
     (if .subIssues.total > 0 then "\(.subIssues.completed)/\(.subIssues.total) (\(.subIssues.percentCompleted)%)" else "0 sub-issues" end),
     .title] | @tsv' | column -t -s $'\t'
}

cmd_overdue() {
  local all
  all=$(items_json)
  echo "## Overdue (due before $TODAY)"
  jq --arg t "$TODAY" "map(select(.dueDate != null and .dueDate < \$t and $ACTIVE)) | sort_by(.dueDate)" <<<"$all" | fmt_items
  echo
  echo "## SLA breach (Urgent, no activity $(cfg .settings.slaHours)h+)"
  jq "map(select((.labels | index(\"sla-breach\")) and $ACTIVE))" <<<"$all" | fmt_items
}

# velocity_json ITEMS_JSON ITERATIONS_JSON -> {cycles: [{title, points}], average}
velocity_json() {
  jq -n --argjson items "$1" --argjson it "$2" '
    ($it.completedIterations[-3:]) as $last
    | [$last[] | . as $c | {title, points: ([$items[] | select(.cycle.id == $c.id and .status == "Done") | .estimate // 0] | add // 0)}] as $v
    | {cycles: $v, average: (if ($v | length) > 0 then (($v | map(.points) | add) / ($v | length)) else null end)}'
}

cmd_plan() {
  local which=@next
  while (($#)); do case $1 in --cycle) which=$2; shift 2 ;; *) die "plan: unknown arg $1" ;; esac; done
  local it items cyc vel
  it=$(iterations_json); items=$(items_json); cyc=$(resolve_cycle "$which")
  vel=$(velocity_json "$items" "$it")
  local order
  order=$(cfg '.fields.priority.options | keys_unsorted')
  jq -rn --argjson items "$items" --argjson c "$cyc" --argjson v "$vel" '
    [$items[] | select(.cycle.id == $c.id)] as $scope
    | ($scope | map(.estimate // 0) | add // 0) as $pts
    | "Cycle: \($c.title) (\($c.startDate) -> \($c.endDate))",
      "Scope: \($scope | length) items, \($pts) points, \($scope | map(select(.estimate == null)) | length) unestimated",
      "Velocity (last \($v.cycles | length) cycles): \($v.cycles | map("\(.title)=\(.points)") | join(", ")); average \($v.average // "n/a")",
      (if $v.average == null then "Capacity: no completed cycles yet, use judgment"
       elif $pts > $v.average then "Capacity: OVER by \($pts - $v.average) points"
       else "Capacity: \($v.average - $pts) points of room" end)'
  echo
  echo "## In cycle"
  jq --argjson c "$cyc" --argjson order "$order" '[.[] | select(.cycle.id == $c.id)] | sort_by(.priority as $p | ($order | index($p // "No priority")) // 99)' <<<"$items" | fmt_items
  echo
  echo "## Candidates (Todo/Backlog, no cycle, by priority)"
  jq --argjson order "$order" \
    "[.[] | select(.cycle == null and (.status == \"Todo\" or .status == \"Backlog\") and $ACTIVE and ((.labels | index(\"project\")) | not))]
     | sort_by(.priority as \$p | (\$order | index(\$p // \"No priority\")) // 99) | .[:15]" <<<"$items" | fmt_items
}

cmd_review() {
  local which=@last
  while (($#)); do case $1 in --cycle) which=$2; shift 2 ;; *) die "review: unknown arg $1" ;; esac; done
  local it items cyc
  it=$(iterations_json); items=$(items_json); cyc=$(resolve_cycle "$which")
  jq -rn --argjson items "$items" --argjson c "$cyc" --argjson it "$it" '
    ([$it.completedIterations[], $it.iterations[]] | unique_by(.id) | sort_by(.startDate)) as $all
    | ([$all[] | select(.startDate >= $c.endDate)][0]) as $nextc
    | [$items[] | select(.cycle.id == $c.id)] as $in
    | [$in[] | select(.status == "Done")] as $done
    | [$in[] | select(.status == "Canceled")] as $canceled
    | [$in[] | select(.status != "Done" and .status != "Canceled")] as $open
    | [$items[] | select($nextc != null and .cycle.id == $nextc.id and (.labels | index("rolled-over")))] as $rolled
    | [$in[] | select(.cycle.setAt != null and (.cycle.setAt[:10]) > $c.startDate)] as $added
    | def pts: map(.estimate // 0) | add // 0;
      def lines: if length == 0 then "  (none)" else .[] | "  \(.ref) [\(.priority // "-"), \(.estimate // "-")pt] \(.title)" end;
      "Cycle review: \($c.title) (\($c.startDate) -> \($c.endDate))",
      "Completed: \($done | length) items, \($done | pts) points",
      "Completion: \(if ($in | length) > 0 then (($done | length) * 100 / ($in | length) | floor) else 0 end)% of items still in the cycle",
      "", "## Completed", ($done | lines),
      "", "## Canceled", ($canceled | lines),
      "", "## Still open in this cycle", ($open | lines),
      "", "## Rolled over into \($nextc.title // "next cycle")", ($rolled | lines),
      "", "## Scope added after the cycle started", ($added | lines)'
}

# ---------------------------------------------------------------- write commands

cmd_set() {
  local ref=$1; shift
  (($#)) || die "set: give key=value pairs"
  local item
  item=$(ensure_item "$ref")
  for kv in "$@"; do
    [[ $kv == *=* ]] || die "set: expected key=value, got $kv"
    set_field "$item" "${kv%%=*}" "${kv#*=}"
  done
  item_json "$item" | jq -c '{ref, status, priority, estimate, cycle: .cycle.title, dueDate, targetDate, health}'
}

cmd_accept() {
  local ref=$1 to=${2:-Backlog} item
  parse_ref "$ref"
  item=$(ensure_item "$ref")
  set_select "$item" status "$to"
  remove_label "$REF_REPO" "$REF_NUM" stale
  echo "accepted $REF_REPO#$REF_NUM -> $(option_name status "$to")"
}

close_issue() { # close_issue CONTENT_ID REASON [DUPLICATE_OF_ID]
  gql 'mutation($i: ID!, $r: IssueClosedStateReason!, $d: ID) { closeIssue(input: {issueId: $i, stateReason: $r, duplicateIssueId: $d}) { issue { state } } }' \
    "$(jq -n --arg i "$1" --arg r "$2" --arg d "${3:-}" '{i: $i, r: $r, d: (if $d == "" then null else $d end)}')" >/dev/null
}

# cancel_after_close ITEM: the built-in "Item closed" workflow sets Done shortly after an
# issue closes. Wait for it (up to ~20s), then set Canceled so Canceled sticks.
cancel_after_close() {
  local i
  for i in $(seq 10); do
    [[ $(item_json "$1" | jq -r '.status // ""') == Done ]] && break
    sleep 2
  done
  set_select "$1" status Canceled
}

cmd_decline() {
  local ref=$1 reason=${2:-} item cid
  parse_ref "$ref"
  item=$(ensure_item "$ref")
  [[ -z $reason ]] || gh issue comment "$REF_NUM" -R "$REF_REPO" --body "Declined: $reason" >/dev/null
  cid=$(content_json "$REF_REPO" "$REF_NUM" | jq -r .id)
  close_issue "$cid" NOT_PLANNED
  cancel_after_close "$item"
  echo "declined $REF_REPO#$REF_NUM (Canceled, closed as not planned)"
}

cmd_duplicate() {
  local ref=$1 of=$2 item cid oid orepo onum
  parse_ref "$of"; orepo=$REF_REPO; onum=$REF_NUM
  oid=$(content_json "$orepo" "$onum" | jq -r .id)
  parse_ref "$ref"
  item=$(ensure_item "$ref")
  cid=$(content_json "$REF_REPO" "$REF_NUM" | jq -r .id)
  close_issue "$cid" DUPLICATE "$oid"
  cancel_after_close "$item"
  echo "$REF_REPO#$REF_NUM closed as duplicate of $orepo#$onum (Canceled)"
}

add_sub_issue() { # PARENT_REF CHILD_REF
  local pid cid
  parse_ref "$1"; pid=$(content_json "$REF_REPO" "$REF_NUM" | jq -r .id)
  parse_ref "$2"; cid=$(content_json "$REF_REPO" "$REF_NUM" | jq -r .id)
  gql 'mutation($p: ID!, $c: ID!) { addSubIssue(input: {issueId: $p, subIssueId: $c}) { issue { id } } }' \
    "$(jq -n --arg p "$pid" --arg c "$cid" '{p: $p, c: $c}')" >/dev/null
}

cmd_create() {
  local repo title="" body="" type="" priority="" estimate="" cycle="" due="" parent="" status="" assignee=""
  repo=$(default_repo)
  while (($#)); do
    case $1 in
      --repo) repo=$2 ;; --title) title=$2 ;; --body) body=$2 ;; --type) type=$2 ;;
      --priority) priority=$2 ;; --estimate) estimate=$2 ;; --cycle) cycle=$2 ;; --due) due=$2 ;;
      --parent) parent=$2 ;; --status) status=$2 ;; --assignee) assignee=$2 ;;
      *) die "create: unknown arg $1" ;;
    esac
    shift 2
  done
  [[ -n $title ]] || die "create: --title is required"
  [[ $repo == */* ]] || repo=$OWNER/$repo
  [[ -z $type ]] || jq -e --arg t "$type" '.issueTypes | index($t)' "$CONFIG" >/dev/null ||
    die "create: --type must be one of $(cfg '.issueTypes | join(", ")')"
  [[ -n $status ]] || if [[ -n $cycle ]]; then status=Todo; else status=Backlog; fi
  local args=(-R "$repo" --title "$title" --body "$body")
  [[ -z $type ]] || args+=(--label "$type")
  [[ -z $assignee ]] || args+=(--assignee "$assignee")
  local url num item
  url=$(gh issue create "${args[@]}")
  num=${url##*/}
  item=$(ensure_item "$repo#$num")
  set_field "$item" status "$status"
  [[ -z $priority ]] || set_field "$item" priority "$priority"
  [[ -z $estimate ]] || set_field "$item" estimate "$estimate"
  [[ -z $cycle ]] || set_field "$item" cycle "$cycle"
  [[ -z $due ]] || set_field "$item" dueDate "$due"
  [[ -z $parent ]] || add_sub_issue "$parent" "$repo#$num"
  echo "$url"
  item_json "$item" | jq -c '{ref, status, priority, estimate, cycle: .cycle.title, dueDate, parent}'
}

cmd_project_create() {
  local repo title="" body="" lead="" target="" health=""
  repo=$(default_repo)
  while (($#)); do
    case $1 in
      --repo) repo=$2 ;; --title) title=$2 ;; --body) body=$2 ;; --lead) lead=$2 ;;
      --target) target=$2 ;; --health) health=$2 ;;
      *) die "project-create: unknown arg $1" ;;
    esac
    shift 2
  done
  [[ -n $title ]] || die "project-create: --title is required"
  [[ $repo == */* ]] || repo=$OWNER/$repo
  local args=(-R "$repo" --title "$title" --body "$body" --label project)
  [[ -z $lead ]] || args+=(--assignee "$lead")
  local url num item
  url=$(gh issue create "${args[@]}")
  num=${url##*/}
  item=$(ensure_item "$repo#$num")
  set_field "$item" status Backlog
  [[ -z $target ]] || set_field "$item" targetDate "$target"
  [[ -z $health ]] || set_field "$item" health "$health"
  echo "$url"
}

cmd_project_add() { add_sub_issue "$1" "$2"; ensure_item "$2" >/dev/null; echo "$2 is now a sub-issue of $1"; }

cmd_status_update() {
  local status="" body="" start="" target=""
  while (($#)); do
    case $1 in --status) status=$2 ;; --body) body=$2 ;; --start) start=$2 ;; --target) target=$2 ;; *) die "status-update: unknown arg $1" ;; esac
    shift 2
  done
  [[ -n $status && -n $body ]] || die "status-update: --status and --body are required"
  gql 'mutation($p: ID!, $s: ProjectV2StatusUpdateStatus, $b: String, $sd: Date, $td: Date) {
      createProjectV2StatusUpdate(input: {projectId: $p, status: $s, body: $b, startDate: $sd, targetDate: $td}) { statusUpdate { id } } }' \
    "$(jq -n --arg p "$PROJECT_ID" --arg s "$status" --arg b "$body" --arg sd "$start" --arg td "$target" \
      '{p: $p, s: $s, b: $b, sd: (if $sd == "" then null else $sd end), td: (if $td == "" then null else $td end)}')" |
    jq -r '"status update posted: \(.data.createProjectV2StatusUpdate.statusUpdate.id)"'
}

cmd_labels_sync() {
  local repo
  for repo in $(cfg '.repos[]'); do
    jq -c '.labels[]' "$CONFIG" | while read -r l; do
      gh label create "$(jq -r .name <<<"$l")" -R "$repo" --color "$(jq -r .color <<<"$l")" \
        --description "$(jq -r .description <<<"$l")" --force >/dev/null
    done
    echo "labels synced: $repo"
  done
}

# ---------------------------------------------------------------- automation

STATUS_ORDER='["Triage","Backlog","Todo","In Progress","In Review","Done","Canceled"]'

# advance ITEM TARGET_STATUS: only moves forward through the workflow; Done and Canceled never regress.
advance() {
  local item=$1 target=$2 cur
  cur=$(item_json "$item" | jq -r '.status // ""')
  if jq -en --arg c "$cur" --arg t "$target" --argjson o "$STATUS_ORDER" \
    '($o | index($c)) as $ci | ($o | index($t)) as $ti | $ci == null or ($ci < $ti and $c != "Done" and $c != "Canceled")' >/dev/null; then
    set_select "$item" status "$target"
    log "$REF_REPO#$REF_NUM: ${cur:-<none>} -> $target"
  else
    log "$REF_REPO#$REF_NUM: stays $cur (not moving back to $target)"
  fi
}

branch_issue_number() { # 12-fix-login, feature/12-fix-login, 12_fix -> 12
  local b=${1##*/}
  [[ $b =~ ^([0-9]+)([-_]|$) ]] && echo "${BASH_REMATCH[1]}"
}

in_board_repo() { jq -e --arg r "$1" '.repos | index($r)' "$CONFIG" >/dev/null; }

cmd_git_sync() {
  local ev=${GITHUB_EVENT_NAME:?} path=${GITHUB_EVENT_PATH:?} repo=${GITHUB_REPOSITORY:?}
  case $ev in
    create)
      [[ $(jq -r .ref_type "$path") == branch ]] || return 0
      local branch sender num item
      branch=$(jq -r .ref "$path"); sender=$(jq -r .sender.login "$path")
      num=$(branch_issue_number "$branch") || { log "branch $branch has no issue number"; return 0; }
      [[ $(content_json "$repo" "$num" | jq -r .typename) == Issue ]] || { log "#$num is not an issue"; return 0; }
      parse_ref "$repo#$num"
      item=$(ensure_item "$repo#$num")
      advance "$item" "In Progress"
      gh issue edit "$num" -R "$repo" --add-assignee "$sender" >/dev/null && log "assigned $sender"
      ;;
    pull_request)
      local action draft merged branch prnum refs r target
      action=$(jq -r .action "$path"); draft=$(jq -r .pull_request.draft "$path")
      merged=$(jq -r .pull_request.merged "$path"); branch=$(jq -r .pull_request.head.ref "$path")
      prnum=$(jq -r .pull_request.number "$path")
      refs=$(gql 'query($o: String!, $n: String!, $num: Int!) { repository(owner: $o, name: $n) { pullRequest(number: $num) {
          closingIssuesReferences(first: 20) { nodes { number repository { nameWithOwner } } } } } }' \
        "$(jq -n --arg o "${repo%%/*}" --arg n "${repo##*/}" --argjson num "$prnum" '{o: $o, n: $n, num: $num}')" |
        jq -r '.data.repository.pullRequest.closingIssuesReferences.nodes[] | "\(.repository.nameWithOwner)#\(.number)"')
      if num=$(branch_issue_number "$branch"); then refs=$(printf '%s\n%s#%s\n' "$refs" "$repo" "$num"); fi
      refs=$(sort -u <<<"$refs" | sed '/^$/d')
      [[ -n $refs ]] || { log "PR #$prnum references no issues"; return 0; }
      case $action in
        closed) [[ $merged == true ]] || { log "PR closed without merge"; return 0; }; target=Done ;;
        *) if [[ $draft == true ]]; then target="In Progress"; else target="In Review"; fi ;;
      esac
      for r in $refs; do
        in_board_repo "${r%%#*}" || continue
        [[ $(content_json "${r%%#*}" "${r##*#}" | jq -r .typename) == Issue ]] || continue
        local item
        parse_ref "$r"
        item=$(ensure_item "$r")
        if [[ $target == Done ]]; then
          set_select "$item" status Done; log "$r -> Done"
          local c
          c=$(content_json "$REF_REPO" "$REF_NUM")
          [[ $(jq -r .state <<<"$c") == CLOSED ]] || close_issue "$(jq -r .id <<<"$c")" COMPLETED
        else
          advance "$item" "$target"
        fi
      done
      ;;
    issues)
      local action num item
      action=$(jq -r .action "$path")
      num=$(jq -r .issue.number "$path")
      if [[ $action == closed ]]; then
        # Closed as not planned or duplicate (CLI or GitHub UI) -> Canceled, after "Item closed" sets Done.
        case $(jq -r .issue.state_reason "$path") in
          not_planned | duplicate)
            item=$(ensure_item "$repo#$num")
            sleep 10
            set_select "$item" status Canceled; log "$repo#$num -> Canceled" ;;
          *) log "$repo#$num closed as completed" ;;
        esac
        return 0
      fi
      [[ $action == opened ]] || return 0
      item=$(ensure_item "$repo#$num")
      sleep 10 # let whoever created the issue (board skill, issue form) set Status first
      # Empty, or a default from the built-in "Item added to project" workflow (Todo unless
      # reconfigured), means nobody chose a Status: send it to Triage.
      local cur
      cur=$(item_json "$item" | jq -c '{status, by: .statusSetBy}')
      if jq -e '.status == null or .by == "github-project-automation"' <<<"$cur" >/dev/null; then
        [[ $(jq -r .status <<<"$cur") == Triage ]] || set_select "$item" status Triage
        log "$repo#$num -> Triage"
      else
        log "$repo#$num already has Status $(jq -r .status <<<"$cur") (set by $(jq -r .by <<<"$cur"))"
      fi
      ;;
    *) log "git-sync: ignoring event $ev" ;;
  esac
}

cmd_rollover() {
  if [[ -n ${GITHUB_REPOSITORY:-} && $GITHUB_REPOSITORY != "$PRIMARY_REPO" ]]; then
    log "rollover runs in $PRIMARY_REPO only"; return 0
  fi
  ensure_future_cycles 3
  local it target items movers
  it=$(iterations_json)
  target=$(jq -c '.current // .upcoming[0]' <<<"$it")
  items=$(items_json)
  movers=$(jq -c --argjson it "$it" "[.[] | select(.cycle != null and $ACTIVE and (.cycle.id as \$c | \$it.completedIterations | map(.id) | index(\$c)))]" <<<"$items")
  log "moving $(jq length <<<"$movers") unfinished item(s) into $(jq -r .title <<<"$target")"
  jq -c '.[]' <<<"$movers" | while read -r m; do
    set_value "$(jq -r .itemId <<<"$m")" cycle "$(jq -n --arg v "$(jq -r .id <<<"$target")" '{iterationId: $v}')"
    if [[ $(jq -r .ref <<<"$m") != null ]]; then
      add_label "$(jq -r .repo <<<"$m")" "$(jq -r .number <<<"$m")" rolled-over
      gh issue comment "$(jq -r .number <<<"$m")" -R "$(jq -r .repo <<<"$m")" \
        --body "Rolled over from $(jq -r .cycle.title <<<"$m") to $(jq -r .title <<<"$target") (unfinished when the cycle ended)." >/dev/null
    fi
    log "$(jq -r '.ref // .title' <<<"$m"): $(jq -r .cycle.title <<<"$m") -> $(jq -r .title <<<"$target")"
  done
}

cmd_stale() {
  local days repo=${GITHUB_REPOSITORY:-}
  days=$(cfg .settings.staleDays)
  while (($#)); do case $1 in --days) days=$2; shift 2 ;; --repo) repo=$2; shift 2 ;; *) die "stale: unknown arg $1" ;; esac; done
  local cutoff
  cutoff=$(jq -rn --argjson d "$days" 'now - $d * 86400 | strftime("%Y-%m-%dT%H:%M:%SZ")')
  items_json | jq -c --arg r "$repo" --arg cut "$cutoff" \
    '.[] | select(.ref != null and ($r == "" or .repo == $r) and .status == "Backlog" and .state == "OPEN"
      and .updatedAt <= $cut and ((.labels | index("stale")) | not) and ((.labels | index("project")) | not))' |
    while read -r m; do
      add_label "$(jq -r .repo <<<"$m")" "$(jq -r .number <<<"$m")" stale
      log "stale: $(jq -r .ref <<<"$m") (updated $(jq -r .updatedAt <<<"$m"))"
    done
}

cmd_sla() {
  local hours repo=${GITHUB_REPOSITORY:-}
  hours=$(cfg .settings.slaHours)
  while (($#)); do case $1 in --hours) hours=$2; shift 2 ;; --repo) repo=$2; shift 2 ;; *) die "sla: unknown arg $1" ;; esac; done
  local cutoff all
  cutoff=$(jq -rn --argjson h "$hours" 'now - $h * 3600 | strftime("%Y-%m-%dT%H:%M:%SZ")')
  all=$(items_json | jq --arg r "$repo" '[.[] | select(.ref != null and .type == "ISSUE" and ($r == "" or .repo == $r))]')
  # Breaches: Urgent, still active, quiet since the cutoff, not yet flagged.
  jq -c --arg cut "$cutoff" ".[] | select(.priority == \"Urgent\" and $ACTIVE and .updatedAt <= \$cut and ((.labels | index(\"sla-breach\")) | not))" <<<"$all" |
    while read -r m; do
      local who
      who=$(jq -r '.assignees | map("@" + .) | join(" ") | if . == "" then "Unassigned." else . end' <<<"$m")
      add_label "$(jq -r .repo <<<"$m")" "$(jq -r .number <<<"$m")" sla-breach
      gh issue comment "$(jq -r .number <<<"$m")" -R "$(jq -r .repo <<<"$m")" \
        --body "SLA breach: this Urgent issue has had no activity for ${hours}h or more. $who" >/dev/null
      log "sla-breach: $(jq -r .ref <<<"$m")"
    done
  # Clear the flag once the item is no longer Urgent or no longer active.
  jq -c ".[] | select((.labels | index(\"sla-breach\")) and (.priority != \"Urgent\" or ($ACTIVE | not)))" <<<"$all" |
    while read -r m; do
      remove_label "$(jq -r .repo <<<"$m")" "$(jq -r .number <<<"$m")" sla-breach
      log "cleared sla-breach: $(jq -r .ref <<<"$m")"
    done
}

# Due date passed and still active -> "overdue" label (so one view can show overdue + SLA
# items with label:overdue,sla-breach). The label is removed once that stops being true.
cmd_flag_overdue() {
  local repo=${GITHUB_REPOSITORY:-}
  while (($#)); do case $1 in --repo) repo=$2; shift 2 ;; *) die "flag-overdue: unknown arg $1" ;; esac; done
  local all
  all=$(items_json | jq --arg r "$repo" '[.[] | select(.ref != null and ($r == "" or .repo == $r))]')
  jq -c --arg t "$TODAY" ".[] | select(.dueDate != null and .dueDate < \$t and $ACTIVE and ((.labels | index(\"overdue\")) | not))" <<<"$all" |
    while read -r m; do
      add_label "$(jq -r .repo <<<"$m")" "$(jq -r .number <<<"$m")" overdue
      log "overdue: $(jq -r .ref <<<"$m") (due $(jq -r .dueDate <<<"$m"))"
    done
  jq -c --arg t "$TODAY" ".[] | select((.labels | index(\"overdue\")) and ((.dueDate != null and .dueDate < \$t and $ACTIVE) | not))" <<<"$all" |
    while read -r m; do
      remove_label "$(jq -r .repo <<<"$m")" "$(jq -r .number <<<"$m")" overdue
      log "cleared overdue: $(jq -r .ref <<<"$m")"
    done
}

cmd_digest() {
  local dry=false
  [[ ${1:-} == --dry-run ]] && dry=true
  if [[ $dry == false && -n ${GITHUB_REPOSITORY:-} && $GITHUB_REPOSITORY != "$PRIMARY_REPO" ]]; then
    log "digest runs in $PRIMARY_REPO only"; return 0
  fi
  local items it since report
  items=$(items_json); it=$(iterations_json)
  since=$(jq -rn 'now - 7 * 86400 | strftime("%Y-%m-%d")')
  report=$(jq -n --argjson items "$items" --argjson it "$it" --arg since "$since" --arg today "$TODAY" "
    def active: $ACTIVE;
    def line: \"- [\(.ref // \"draft\")](\(.url // \"\")) \(.title)\(if .priority then \" (\(.priority))\" else \"\" end)\";
    def section(\$h; \$xs): \"### \(\$h) (\(\$xs | length))\n\" + (if (\$xs | length) == 0 then \"None\" else (\$xs[:25] | map(line) | join(\"\n\")) end);
    [\$items[] | select(.status == \"Done\" and (((.closedAt // .statusSetAt) // \"\")[:10]) >= \$since)] as \$done
    | [\$items[] | select(.status == \"In Progress\" or .status == \"In Review\")] as \$wip
    | [\$items[] | select(\$it.current != null and .cycle.id == \$it.current.id and (.labels | index(\"rolled-over\")) and active)] as \$rolled
    | [\$items[] | select((.labels | index(\"project\")) and active and (.health == \"At risk\" or .health == \"Off track\" or (.targetDate != null and .targetDate < \$today)))] as \$risk
    | [\$items[] | select(.dueDate != null and .dueDate < \$today and active)] as \$overdue
    | {
        status: (if any(\$risk[]; .health == \"Off track\") then \"OFF_TRACK\" elif (\$risk | length) > 0 or (\$overdue | length) > 0 then \"AT_RISK\" else \"ON_TRACK\" end),
        body: ([
          \"Weekly digest, \(\$since) to \(\$today)\" + (if \$it.current then \". Current cycle: \(\$it.current.title) (ends \(\$it.current.endDate)).\" else \".\" end),
          section(\"Completed\"; \$done),
          section(\"In progress\"; \$wip),
          section(\"Rolled over into this cycle\"; \$rolled),
          (\"### At-risk projects (\(\$risk | length))\n\" + (if (\$risk | length) == 0 then \"None\" else (\$risk | map(\"- [\(.ref)](\(.url)) \(.title): \(.health // \"no health\"), target \(.targetDate // \"none\")\") | join(\"\n\")) end)),
          section(\"Overdue\"; \$overdue)
        ] | join(\"\n\n\"))
      }")
  if [[ $dry == true ]]; then
    jq -r '"status: \(.status)\n\n\(.body)"' <<<"$report"
  else
    cmd_status_update --status "$(jq -r .status <<<"$report")" --body "$(jq -r .body <<<"$report")" --start "$since" --target "$TODAY"
  fi
}

# ---------------------------------------------------------------- dispatch

cmd=${1:-help}
(($#)) && shift
case $cmd in
  items) cmd_items "$@" ;;
  show) cmd_show "$@" ;;
  triage) cmd_triage ;;
  cycles) cmd_cycles ;;
  projects) cmd_projects ;;
  overdue) cmd_overdue ;;
  plan) cmd_plan "$@" ;;
  review) cmd_review "$@" ;;
  set) cmd_set "$@" ;;
  accept) cmd_accept "$@" ;;
  decline) cmd_decline "$@" ;;
  duplicate) cmd_duplicate "$@" ;;
  create) cmd_create "$@" ;;
  project-create) cmd_project_create "$@" ;;
  project-add) cmd_project_add "$@" ;;
  status-update) cmd_status_update "$@" ;;
  labels-sync) cmd_labels_sync ;;
  git-sync) cmd_git_sync ;;
  rollover) cmd_rollover ;;
  stale) cmd_stale "$@" ;;
  sla) cmd_sla "$@" ;;
  flag-overdue) cmd_flag_overdue "$@" ;;
  digest) cmd_digest "$@" ;;
  help | -h | --help) usage ;;
  *) usage >&2; exit 2 ;;
esac
