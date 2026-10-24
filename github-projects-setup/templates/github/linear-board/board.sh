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
  profile                           Pace, preset, WIP limit, and what is enabled on this board
  items [--repo R] [--status S]     Board items as JSON (archived items excluded)
  show <ref>                        One item as JSON
  triage                            Inbox (Triage, or unprioritized inbox items) plus items labeled stale
  next [--limit 5]                  Now (in progress), the top unblocked open items by priority, then age,
                                    and the blocked items with what they wait on
  cycles                            Cycle iterations (completed, current, upcoming)
  projects                          Parent "project" issues with lead, progress (and target, health)
  overdue                           Overdue items and SLA breaches (Timeboxed)
  plan [--cycle @next]              Capacity check: cycle scope vs recent velocity
  review [--cycle @last]            Cycle review: completed, rolled over, scope change

Write
  set <ref> key=value...            Keys: status priority estimate cycle due target health
  accept <ref> [Backlog|Todo]       Accept an inbox item (default Backlog, or Todo), clears stale
  decline <ref> [reason]            Close as not planned (+ Canceled when the board has it)
  duplicate <ref> <of-ref>          Close as duplicate of <of-ref> (+ Canceled when the board has it)
  create --title T [--repo R] [--body B] [--type TYPE]
         [--priority P] [--estimate N] [--cycle @current|@next|TITLE] [--due YYYY-MM-DD]
         [--parent REF] [--status S] [--assignee LOGIN] [--blocked-by REF[,REF...]]
  block <ref> <blocker-ref>...      Mark ref as blocked by each blocker (GitHub issue dependencies)
  unblock <ref> <blocker-ref>...    Remove those blocked-by links
  project-create --title T [--repo R] [--body B] [--lead LOGIN] [--target YYYY-MM-DD]
         [--health "On track"|"At risk"|"Off track"]
  project-add <parent-ref> <child-ref>   Make child a sub-issue of a project
  status-update [--status ON_TRACK|AT_RISK|OFF_TRACK|COMPLETE|INACTIVE] --body B
         [--start YYYY-MM-DD] [--target YYYY-MM-DD]
  labels-sync                       Create or update the board's labels in every linked repo
  readme                            Refresh the end-date countdown in the project README

Automation (used by GitHub Actions)
  git-sync                          Branch / PR / issue events -> Status
  rollover                          Move unfinished items out of ended cycles, keep cycles ahead
  stale [--days 60] [--repo R]      Label untouched Backlog items stale
  sla [--hours 24] [--repo R]       Label Urgent items with no recent activity sla-breach
  flag-overdue [--repo R]           Label items past their due date overdue (and clear it)
  digest [--dry-run]                Post a weekly project status update

Setup and upgrades (add-only: nothing is ever deleted)
  resolve                           Recompute the enabled statuses, labels, views, workflows
  schema-sync [--fields] [--views] [--fresh]
                                    Add missing Status options, fields, and views
  sync-files [--dir D] [--primary]  Install enabled workflows and issue forms; render the local skill
  render FILE                       Render a template (<!-- when: ... --> blocks, __PLACEHOLDERS__)
  upgrade [--preset solo|full] [--pace timeboxed] [--cycle-days N --cycle-start S]
          [--wip N|keep|none]       Hackathon -> Solo -> Full, Self-paced -> Timeboxed. Self-paced ->
                                    Timeboxed with a WIP limit asks (or needs --wip without a TTY)

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

# Write the config in place. It goes through cat, so a symlinked config (the local
# skill's config.json) stays a symlink.
write_config() { # JQ_ARGS... (the filter and its --arg options, applied to $CONFIG)
  local t
  t=$(mktemp)
  jq "$@" "$CONFIG" >"$t" && cat "$t" >"$CONFIG"
  rm -f "$t"
}

# What this board has. Boards set up before pace and presets existed are Timeboxed + Full.
pace() { cfg '.pace // "timeboxed"'; }
preset() { cfg '.preset // "full"'; }
inbox_status() { cfg '.inboxStatus // "Triage"'; }
wip_limit() { cfg '.wipLimit // empty'; }
has_field() { [[ -n $(cfg ".fields.$1.id // empty") ]]; }
has_status() { jq -e --arg s "$1" '(.fields.status.options // {}) | has($s)' "$CONFIG" >/dev/null; }
has_label() { jq -e --arg l "$1" '[.labels[].name] | index($l)' "$CONFIG" >/dev/null; }
behavior() { [[ $(cfg ".behaviors.$1 != false") == true ]]; }
off() { die "$1 is not enabled on this board ($(preset), $(pace)). See: board.sh upgrade"; }
need_field() { has_field "$1" || off "the $(jq -r --arg k "$1" '[.catalog.fields[]? | select(.key == $k) | .name][0] // $k' "$CONFIG") field"; }

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
      blockedBy(first: 20) { nodes { number state repository { nameWithOwner } } }
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
  def fv($k): if $f[$k].id then [.fieldValues.nodes[] | select(.field.id? == $f[$k].id)][0] else null end;
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
    blockedBy: [.content.blockedBy.nodes[]? | select(.state == "OPEN") | "\(.repository.nameWithOwner)#\(.number)"],
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
  need_field "$key"
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

# WIP limit (set at setup for Self-paced boards). Over the limit only warns, it never blocks.
wip_count() { # [ITEMS_JSON]
  if (($#)); then jq "$WIP_FILTER" <<<"$1"; else items_json | jq "$WIP_FILTER"; fi
}
WIP_FILTER='[.[] | select(.status == "In Progress" and ((.labels | index("project")) | not))] | length'

# wip_warning [ITEMS_JSON]: prints a warning sentence when In Progress is over the limit.
wip_warning() {
  local limit n
  limit=$(wip_limit)
  [[ -n $limit ]] || return 0
  n=$(wip_count "$@")
  if ((n > limit)); then
    echo "WIP limit exceeded: $n items are In Progress (limit $limit). Finish or pause something before starting more."
  fi
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
  local all inbox
  all=$(items_json); inbox=$(inbox_status)
  if [[ $inbox == Triage ]]; then
    echo "## Triage"
    jq 'map(select(.status == "Triage" or (.status == null and .state == "OPEN"))) | sort_by(.createdAt)' <<<"$all" | fmt_items
  else
    # No Triage status: new issues land in the inbox status; "untriaged" means no Priority yet.
    echo "## Inbox ($inbox, no priority yet)"
    jq --arg s "$inbox" "map(select((.status == \$s or .status == null) and .priority == null and $ACTIVE
      and ((.labels | index(\"project\")) | not))) | sort_by(.createdAt)" <<<"$all" | fmt_items
  fi
  if has_label stale; then
    echo
    echo "## Stale (Backlog, untouched $(cfg .settings.staleDays)+ days)"
    jq "map(select((.labels | index(\"stale\")) and $ACTIVE))" <<<"$all" | fmt_items
  fi
}

# Priority rank for sort_by: Urgent first, no priority last.
PRIORITY_RANK='(.priority as $p | ($order | index($p // "No priority")) // 99)'

cmd_next() {
  local limit=5
  while (($#)); do case $1 in --limit) limit=$2; shift 2 ;; *) die "next: unknown arg $1" ;; esac; done
  local items order w
  items=$(items_json)
  order=$(cfg '.fields.priority.options | keys_unsorted')
  echo "## Now (In Progress, In Review)"
  jq '[.[] | select(.status == "In Progress" or .status == "In Review")] | sort_by(.statusSetAt)' <<<"$items" | fmt_items
  if [[ -n $(wip_limit) ]]; then
    echo "WIP: $(wip_count "$items") of $(wip_limit) In Progress"
    w=$(wip_warning "$items")
    [[ -z $w ]] || echo "warning: $w"
  fi
  echo
  echo "## Next (by priority, then age; blocked items skipped)"
  local open blocked
  open=$(jq --argjson order "$order" \
    "[.[] | select((.status == \"Todo\" or .status == \"Backlog\") and $ACTIVE and ((.labels | index(\"project\")) | not))]
     | sort_by($PRIORITY_RANK, .createdAt)" <<<"$items")
  jq --argjson n "$limit" '[.[] | select(.blockedBy | length == 0)] | .[:$n]' <<<"$open" | fmt_items
  blocked=$(jq --argjson n "$limit" '[.[] | select(.blockedBy | length > 0)] | .[:$n]' <<<"$open")
  [[ $(jq length <<<"$blocked") -gt 0 ]] || return 0
  echo
  echo "## Blocked (waiting on open issues)"
  jq -r '.[] | .repo as $r | [.ref, (.priority // "-"), .title,
      "waits on " + (.blockedBy | map(if startswith($r + "#") then "#" + split("#")[1] else . end) | join(", "))] | @tsv' \
    <<<"$blocked" | column -t -s $'\t'
}

cmd_profile() {
  jq -r '
    "Pace: \(.pace // "timeboxed")",
    "Preset: \(.preset // "full")",
    "WIP limit: \(.wipLimit // "none")",
    (if .endDate then "End date: \(.endDate)" else empty end),
    "Inbox status: \(.inboxStatus // "Triage")",
    "Statuses: \(.fields.status.options | keys_unsorted | join(", "))",
    "Fields: \([.fields[] | .name] | join(", "))",
    "Labels: \([.labels[].name] | join(", "))",
    "Issue types: \(.issueTypes | join(", "))",
    (if .views then "Views: \(.views | join(", "))" else empty end),
    (if .workflows then "Workflows: \([.workflows[] | .file + (if .primary then " (primary)" else "" end)] | join(", "))" else empty end),
    "Assign on branch: \(.behaviors.assignOnBranch != false)"' "$CONFIG"
}

cmd_cycles() {
  need_field cycle
  iterations_json | jq -r '
    (.completedIterations[] | "done     \(.title)  \(.startDate) -> \(.endDate)"),
    (.iterations[] | "\(if .startDate <= (now|strftime("%Y-%m-%d")) then "current " else "upcoming" end) \(.title)  \(.startDate) -> \(.endDate)")'
}

cmd_projects() {
  has_label project || off "Projects"
  # Self-paced boards have no Health or Target date: progress is the sub-issue rollup only.
  items_json | jq -r --argjson h "$(has_field health && echo true || echo false)" \
    --argjson t "$(has_field targetDate && echo true || echo false)" '
    map(select(.labels | index("project"))) | sort_by(.targetDate // "9999", .createdAt) | .[] |
    [.ref, (.status // "-")] + (if $h then [.health // "-"] else [] end) + (if $t then [.targetDate // "-"] else [] end) +
    [((.assignees | join(",")) | if . == "" then "no lead" else . end),
     (if .subIssues.total > 0 then "\(.subIssues.completed)/\(.subIssues.total) (\(.subIssues.percentCompleted)%)" else "0 sub-issues" end),
     .title] | @tsv' | column -t -s $'\t'
}

cmd_overdue() {
  [[ $(pace) == timeboxed ]] || off "Overdue and SLA tracking"
  local all
  all=$(items_json)
  if has_field dueDate; then
    echo "## Overdue (due before $TODAY)"
    jq --arg t "$TODAY" "map(select(.dueDate != null and .dueDate < \$t and $ACTIVE)) | sort_by(.dueDate)" <<<"$all" | fmt_items
    echo
  fi
  if has_label sla-breach; then
    echo "## SLA breach (Urgent, no activity $(cfg .settings.slaHours)h+)"
    jq "map(select((.labels | index(\"sla-breach\")) and $ACTIVE))" <<<"$all" | fmt_items
  fi
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
  need_field cycle
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
  need_field cycle
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
  local started=false w
  for kv in "$@"; do
    [[ $kv == *=* ]] || die "set: expected key=value, got $kv"
    set_field "$item" "${kv%%=*}" "${kv#*=}"
    [[ ${kv%%=*} != status || $(tr '[:upper:]' '[:lower:]' <<<"${kv#*=}") != "in progress" ]] || started=true
  done
  item_json "$item" | jq -c '{ref, status, priority, estimate, cycle: .cycle.title, dueDate, targetDate, health}'
  if [[ $started == true ]]; then w=$(wip_warning); [[ -z $w ]] || log "warning: $w"; fi
}

# Status for accepted or new unscheduled work: Backlog, or Todo on boards without Backlog.
backlog_status() { if has_status Backlog; then echo Backlog; else echo Todo; fi; }

cmd_accept() {
  local ref=$1 to=${2:-$(backlog_status)} item
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
  if has_status Canceled; then
    cancel_after_close "$item"
    echo "declined $REF_REPO#$REF_NUM (Canceled, closed as not planned)"
  else
    echo "declined $REF_REPO#$REF_NUM (closed as not planned)"
  fi
}

cmd_duplicate() {
  local ref=$1 of=$2 item cid oid orepo onum
  parse_ref "$of"; orepo=$REF_REPO; onum=$REF_NUM
  oid=$(content_json "$orepo" "$onum" | jq -r .id)
  parse_ref "$ref"
  item=$(ensure_item "$ref")
  cid=$(content_json "$REF_REPO" "$REF_NUM" | jq -r .id)
  close_issue "$cid" DUPLICATE "$oid"
  has_status Canceled && cancel_after_close "$item"
  echo "$REF_REPO#$REF_NUM closed as duplicate of $orepo#$onum$(has_status Canceled && echo " (Canceled)")"
}

add_sub_issue() { # PARENT_REF CHILD_REF
  local pid cid
  parse_ref "$1"; pid=$(content_json "$REF_REPO" "$REF_NUM" | jq -r .id)
  parse_ref "$2"; cid=$(content_json "$REF_REPO" "$REF_NUM" | jq -r .id)
  gql 'mutation($p: ID!, $c: ID!) { addSubIssue(input: {issueId: $p, subIssueId: $c}) { issue { id } } }' \
    "$(jq -n --arg p "$pid" --arg c "$cid" '{p: $p, c: $c}')" >/dev/null
}

# issue_node_id REF -> node ID, or nothing when the issue does not exist
issue_node_id() {
  parse_ref "$1"
  { content_json "$REF_REPO" "$REF_NUM" | jq -r '.id // empty'; } 2>/dev/null || true
}

# blocked_by add|remove REF BLOCKER_REF: GitHub's native "blocked by" issue dependency.
# Adding a link that already exists is not an error.
blocked_by() {
  local op=$1 iid bid out m
  iid=$(issue_node_id "$2"); [[ -n $iid ]] || die "not found: $2"
  bid=$(issue_node_id "$3"); [[ -n $bid ]] || die "not found: $3"
  if [[ $op == add ]]; then m=addBlockedBy; else m=removeBlockedBy; fi
  if ! out=$(gql "mutation(\$i: ID!, \$b: ID!) { $m(input: {issueId: \$i, blockingIssueId: \$b}) { issue { id } } }" \
    "$(jq -n --arg i "$iid" --arg b "$bid" '{i: $i, b: $b}')" 2>&1); then
    [[ $op == add && $out == *"already been taken"* ]] || die "$m $2 <- $3: $out"
  fi
}

cmd_block() { # REF BLOCKER_REF...
  (($# >= 2)) || die "block: usage: block <ref> <blocker-ref>..."
  local ref=$1 b; shift
  for b in "$@"; do blocked_by add "$ref" "$b"; echo "$ref is blocked by $b"; done
}

cmd_unblock() { # REF BLOCKER_REF...
  (($# >= 2)) || die "unblock: usage: unblock <ref> <blocker-ref>..."
  local ref=$1 b; shift
  for b in "$@"; do blocked_by remove "$ref" "$b"; echo "$ref is no longer blocked by $b"; done
}

cmd_create() {
  local repo title="" body="" type="" priority="" estimate="" cycle="" due="" parent="" status="" assignee="" blockers=""
  repo=$(default_repo)
  while (($#)); do
    case $1 in
      --repo) repo=$2 ;; --title) title=$2 ;; --body) body=$2 ;; --type) type=$2 ;;
      --priority) priority=$2 ;; --estimate) estimate=$2 ;; --cycle) cycle=$2 ;; --due) due=$2 ;;
      --parent) parent=$2 ;; --status) status=$2 ;; --assignee) assignee=$2 ;; --blocked-by) blockers=${2//,/ } ;;
      *) die "create: unknown arg $1" ;;
    esac
    shift 2
  done
  [[ -n $title ]] || die "create: --title is required"
  [[ $repo == */* ]] || repo=$OWNER/$repo
  [[ -z $type ]] || jq -e --arg t "$type" '.issueTypes | index($t)' "$CONFIG" >/dev/null ||
    die "create: --type must be one of $(cfg '.issueTypes | join(", ")')"
  # Check every field before the issue exists, so a field this board lacks does not leave a half-made issue.
  [[ -z $priority ]] || need_field priority
  [[ -z $estimate ]] || need_field estimate
  [[ -z $cycle ]] || need_field cycle
  [[ -z $due ]] || need_field dueDate
  [[ -z $parent ]] || has_label project || off "Projects"
  # Bare blocker refs (#12) mean the new issue's repo. Check they exist before the issue does.
  local BOARD_REPO=$repo b
  for b in $blockers; do [[ -n $(issue_node_id "$b") ]] || die "create: blocker not found: $b"; done
  [[ -n $status ]] || if [[ -n $cycle ]]; then status=Todo; else status=$(backlog_status); fi
  [[ -z $status ]] || option_id status "$status" >/dev/null
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
  for b in $blockers; do blocked_by add "$repo#$num" "$b"; done
  echo "$url"
  item_json "$item" | jq -c '{ref, status, priority, estimate, cycle: .cycle.title, dueDate, parent, blockedBy}'
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
  has_label project || off "Projects"
  [[ -z $target ]] || need_field targetDate
  [[ -z $health ]] || need_field health
  [[ $repo == */* ]] || repo=$OWNER/$repo
  local args=(-R "$repo" --title "$title" --body "$body" --label project)
  [[ -z $lead ]] || args+=(--assignee "$lead")
  local url num item
  url=$(gh issue create "${args[@]}")
  num=${url##*/}
  item=$(ensure_item "$repo#$num")
  set_field "$item" status "$(backlog_status)"
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
  [[ -n $body ]] || die "status-update: --body is required"
  gql 'mutation($p: ID!, $s: ProjectV2StatusUpdateStatus, $b: String, $sd: Date, $td: Date) {
      createProjectV2StatusUpdate(input: {projectId: $p, status: $s, body: $b, startDate: $sd, targetDate: $td}) { statusUpdate { id } } }' \
    "$(jq -n --arg p "$PROJECT_ID" --arg s "$status" --arg b "$body" --arg sd "$start" --arg td "$target" \
      '{p: $p, s: (if $s == "" then null else $s end), b: $b, sd: (if $sd == "" then null else $sd end), td: (if $td == "" then null else $td end)}')" |
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
# Sets ADVANCED=true when it moved the item.
advance() {
  local item=$1 target=$2 cur
  ADVANCED=false
  cur=$(item_json "$item" | jq -r '.status // ""')
  if jq -en --arg c "$cur" --arg t "$target" --argjson o "$STATUS_ORDER" \
    '($o | index($c)) as $ci | ($o | index($t)) as $ti | $ci == null or ($ci < $ti and $c != "Done" and $c != "Canceled")' >/dev/null; then
    set_select "$item" status "$target"
    ADVANCED=true
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

# After an issue moves to In Progress: comment on it when the board is over its WIP limit.
wip_comment() { # REPO NUM
  local w
  w=$(wip_warning)
  [[ -n $w ]] || return 0
  gh issue comment "$2" -R "$1" --body "Heads up: $w" >/dev/null
  log "$1#$2: $w"
}

cmd_git_sync() {
  git_sync_event
  # Hackathon boards with an end date: refresh the README countdown on every run (there is no schedule).
  if [[ -n $(cfg '.endDate // empty') ]]; then cmd_readme || log "README countdown not updated"; fi
}

git_sync_event() {
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
      [[ $ADVANCED == false ]] || wip_comment "$repo" "$num"
      if behavior assignOnBranch; then
        gh issue edit "$num" -R "$repo" --add-assignee "$sender" >/dev/null && log "assigned $sender"
      fi
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
        *) if [[ $draft == true ]] || ! has_status "In Review"; then target="In Progress"; else target="In Review"; fi ;;
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
          [[ $ADVANCED == false || $target != "In Progress" ]] || wip_comment "$REF_REPO" "$REF_NUM"
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
            has_status Canceled || { log "$repo#$num closed as not planned (board has no Canceled)"; return 0; }
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
      # reconfigured), means nobody chose a Status: send it to the inbox (Triage, or Backlog
      # on Solo boards, or Todo on Hackathon boards).
      local cur inbox
      inbox=$(inbox_status)
      cur=$(item_json "$item" | jq -c '{status, by: .statusSetBy}')
      if jq -e '.status == null or .by == "github-project-automation"' <<<"$cur" >/dev/null; then
        [[ $(jq -r .status <<<"$cur") == "$inbox" ]] || set_select "$item" status "$inbox"
        log "$repo#$num -> $inbox"
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
  if ! has_field cycle; then log "this board has no cycles ($(pace)), nothing to roll over"; return 0; fi
  ensure_future_cycles "$(cfg '.settings.upcomingCycles // 3')"
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
  if ! has_label stale; then log "stale flagging is off for this board ($(preset))"; return 0; fi
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
  if ! has_label sla-breach; then log "the Urgent SLA is off for this board ($(preset), $(pace))"; return 0; fi
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
  if ! has_label overdue || ! has_field dueDate; then log "overdue flagging is off for this board ($(preset), $(pace))"; return 0; fi
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
  items=$(items_json)
  since=$(jq -rn 'now - 7 * 86400 | strftime("%Y-%m-%d")')
  if [[ $(pace) == self-paced ]]; then
    report=$(done_log "$items" "$since")
  else
    it=$(iterations_json)
    report=$(timeboxed_digest "$items" "$it" "$since")
  fi
  if [[ $dry == true ]]; then
    jq -r '"status: \(.status // "(none)")\n\n\(.body)"' <<<"$report"
  else
    cmd_status_update --status "$(jq -r '.status // ""' <<<"$report")" --body "$(jq -r .body <<<"$report")" --start "$since" --target "$TODAY"
  fi
}

DIGEST_DEFS='
  def line: "- [\(.ref // "draft")](\(.url // "")) \(.title)\(if .priority then " (\(.priority))" else "" end)";
  def section($h; $xs): "### \($h) (\($xs | length))\n" + (if ($xs | length) == 0 then "None" else ($xs[:25] | map(line) | join("\n")) end);
  def done_since($since): select(.status == "Done" and (((.closedAt // .statusSetAt) // "")[:10]) >= $since);'

# Self-paced: a plain "what got done" log. No status, no deadlines, no at-risk language.
done_log() { # ITEMS_JSON SINCE
  jq -n --argjson items "$1" --arg since "$2" --arg today "$TODAY" "$DIGEST_DEFS"'
    [$items[] | done_since($since)] as $done
    | [$items[] | select(.status == "In Progress" or .status == "In Review")] as $now
    | [$items[] | select((.labels | index("project")) and .status != "Done" and .status != "Canceled" and .state == "OPEN")] as $projects
    | {
        status: null,
        body: ([
          "What got done, \($since) to \($today).",
          section("Done"; $done),
          section("Now"; $now),
          (if ($projects | length) == 0 then empty else
            "### Projects\n" + ($projects | map("- [\(.ref)](\(.url)) \(.title): \(.subIssues.completed // 0)/\(.subIssues.total // 0) sub-issues done") | join("\n")) end)
        ] | join("\n\n"))
      }'
}

timeboxed_digest() { # ITEMS_JSON ITERATIONS_JSON SINCE
  local items=$1 it=$2 since=$3 report
  report=$(jq -n --argjson items "$items" --argjson it "$it" --arg since "$since" --arg today "$TODAY" "
    def active: $ACTIVE;
    $DIGEST_DEFS
    [\$items[] | done_since(\$since)] as \$done
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
  echo "$report"
}

# ---------------------------------------------------------------- setup and upgrades
#
# The config's "catalog" (a copy of the setup skill's defaults.json) lists every status,
# field, label, view, workflow, and issue form, each with the presets, paces, and flags it
# needs. "resolve" turns catalog + pace + preset into the lists that everything else reads.
# Setup and upgrades share this code, and both only add: nothing is deleted or renamed.

PRESETS='["hackathon","solo","full"]'
PACES='["self-paced","timeboxed"]'

RESOLVE='
  def on($c): (.presets == null or (.presets | index($c.preset)) != null)
    and (.paces == null or (.paces | index($c.pace)) != null)
    and ((.flags // []) | all(. as $f | $c.flags[$f] == true));
  .pace //= "timeboxed" | .preset //= "full" | .catalog as $k
  | . as $c | .flags = ($k.flagDefaults | with_entries(.key as $n | .value = (($c.flags[$n] // false) or (.value | on($c)))))
  | . as $c
  | .statuses = [$k.statuses[] | select(on($c)) | .name]
  | .inboxStatus = (.statuses as $s | [$k.inboxOrder[] | select(. as $x | $s | index($x))][0])
  | .labels = [$k.labels[] | select(on($c)) | {name, color, description}]
  | .issueTypes = [$k.labels[] | select(.type and on($c)) | .name]
  | .fieldKeys = [$k.fields[] | select(on($c)) | .key]
  | .views = [$k.views[] | select(on($c)) | .name]
  | .workflows = [$k.workflows[] | select(on($c)) | {file, primary: (.primary // false)}]
  | .issueTemplates = [$k.issueTemplates[] | select(on($c)) | .file]
  | .behaviors = ($k.behaviors | map_values(on($c)))
  | .estimateScale = $k.estimateScale | .settings = $k.settings'

cmd_resolve() {
  jq -e .catalog "$CONFIG" >/dev/null || die "config has no catalog (set up before pace and presets existed)"
  write_config "$RESOLVE"
}

project_fields_json() {
  gql 'query($id: ID!) { node(id: $id) { ... on ProjectV2 {
      fields(first: 50) { nodes { ... on ProjectV2FieldCommon { id name dataType }
        ... on ProjectV2SingleSelectField { options { id name color description } } } }
      views(first: 50) { nodes { id name number } } } } }' "$(jq -n --arg id "$PROJECT_ID" '{id: $id}')" | jq .data.node
}

# A weekday cycle start means the most recent such day, today included, so a cycle is active right away.
resolve_start() { # YYYY-MM-DD|WEEKDAY
  if [[ $1 =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then echo "$1"; return; fi
  jq -rn --arg d "$1" '
    (["sunday","monday","tuesday","wednesday","thursday","friday","saturday"]
      | map(.[0:3]) | index($d | ascii_downcase | .[0:3])) as $want
    | if $want == null then error("bad weekday: \($d)") else . end
    | (now | gmtime) as $t | (($t[6] - $want + 7) % 7) as $back
    | now - $back * 86400 | strftime("%Y-%m-%d")' || die "cycle start must be YYYY-MM-DD or a weekday name"
}

create_project_field() { # NAME TYPE [EXTRA_INPUT_JSON]
  log "creating field $1 ($2)"
  gql 'mutation($in: CreateProjectV2FieldInput!) { createProjectV2Field(input: $in) { projectV2Field { ... on ProjectV2FieldCommon { id } } } }' \
    "$(jq -n --arg p "$PROJECT_ID" --arg n "$1" --arg t "$2" --argjson x "${3:-null}" '{in: ({projectId: $p, name: $n, dataType: $t} + $x)}')" >/dev/null
}

# Status options in catalog order. Existing options keep their ID (so items keep their
# value) and, unless --fresh, their color and description. Unknown options stay at the end.
sync_status() { # FRESH(true|false)
  local p cur opts
  p=$(project_fields_json)
  cur=$(jq -c '[.fields.nodes[] | select(.name == "Status")][0]' <<<"$p")
  opts=$(jq -c --argjson cur "$cur" --argjson fresh "$1" '
    .pace as $pace | .statuses as $want
    | [.catalog.statuses[] | select(.name as $n | $want | index($n)) | . as $s
       | ([$cur.options[] | select(.name == $s.name)][0]) as $have
       | {name, color, description: (if $pace == "self-paced" then .descriptionSelfPaced // .description else .description end)}
       | if $have == null then . elif $fresh then . + {id: $have.id} else {id: $have.id, name: $have.name, color: $have.color, description: $have.description} end]
    | . + [$cur.options[] | select(.name as $n | $want | index($n) | not) | {id, name, color, description}]' "$CONFIG")
  if [[ $1 == true ]] || jq -e --argjson cur "$cur" 'length != ($cur.options | length)' <<<"$opts" >/dev/null; then
    log "Status options: $(jq -r 'map(.name) | join(", ")' <<<"$opts")"
    gql 'mutation($f: ID!, $o: [ProjectV2SingleSelectFieldOptionInput!]!) {
        updateProjectV2Field(input: {fieldId: $f, singleSelectOptions: $o}) { projectV2Field { ... on ProjectV2SingleSelectField { id } } } }' \
      "$(jq -n --arg f "$(jq -r .id <<<"$cur")" --argjson o "$opts" '{f: $f, o: $o}')" >/dev/null
  fi
}

sync_fields() { # FRESH
  sync_status "$1"
  local p key name type
  p=$(project_fields_json)
  while IFS=$'\t' read -r key name type; do
    jq -e --arg n "$name" '.fields.nodes[] | select(.name == $n)' <<<"$p" >/dev/null && continue
    case $type in
      SINGLE_SELECT) create_project_field "$name" "$type" "$(jq -c --arg k "$key" \
        '.catalog as $c | {singleSelectOptions: $c[$c.fields[] | select(.key == $k) | .options]}' "$CONFIG")" ;;
      ITERATION)
        [[ -n $(cfg '.cycle.durationDays // empty') ]] || die "a Cycle field needs a cycle length and start (--cycle-days, --cycle-start)"
        write_config --arg s "$(resolve_start "$(cfg .cycle.startDate)")" '.cycle.startDate = $s'
        create_project_field "$name" "$type" "$(jq -c '.cycle as $c | {iterationConfiguration: {startDate: $c.startDate, duration: $c.durationDays,
          iterations: [range(0; 4) as $i | {title: "Cycle \($i + 1)", duration: $c.durationDays,
            startDate: (($c.startDate | strptime("%Y-%m-%d") | mktime) + $i * $c.durationDays * 86400 | strftime("%Y-%m-%d"))}]}}' "$CONFIG")" ;;
      *) create_project_field "$name" "$type" ;;
    esac
  done < <(jq -r '.fieldKeys as $on | .catalog.fields[] | select(.builtin | not) | select(.key as $k | $on | index($k)) | [.key, .name, .dataType] | @tsv' "$CONFIG")
  # Record every catalog field that is on the board (including ones from an earlier preset).
  p=$(project_fields_json)
  write_config --argjson p "$p" '
    .fields = (reduce .catalog.fields[] as $cf ({};
        ([$p.fields.nodes[] | select(.name == $cf.name)][0]) as $x
        | if $x == null then . else . + {($cf.key): ({id: $x.id, name: $x.name}
            + (if $x.options then {options: ($x.options | map({(.name): .id}) | add)} else {} end))} end))'
}

sync_views() {
  local p first name layout filter conf vid
  p=$(project_fields_json)
  first=$(jq -r '.views.nodes | sort_by(.number) | .[0] | select(.name == "View 1") | .id // empty' <<<"$p")
  # Unit separator, not tab: read collapses repeated whitespace separators, and filters can be empty.
  while IFS=$'\x1f' read -r name layout filter conf; do
    if jq -e --arg n "$name" '.views.nodes[] | select(.name == $n)' <<<"$p" >/dev/null; then
      log "view \"$name\" exists, skipping"; continue
    fi
    if [[ -n $first ]]; then
      vid=$first; first=""
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
  done < <(jq -r --argjson p "$p" '
    (.fields.status.options | keys) as $st
    | .views as $on | .catalog.views[] | select(.name as $n | $on | index($n))
    | ([.excludeStatuses[]? | select(. as $s | $st | index($s))]) as $ex
    | ([.filter, (if ($ex | length) > 0 then "-status:" + ($ex | join(",")) else empty end)] | map(select(. != "")) | join(" ")) as $filter
    | ([.fields[]? as $n | $p.fields.nodes[] | select(.name == $n) | .id]) as $ids
    | [.name, .layout, $filter, (if ($ids | length) > 0 then ({visibleFieldIds: $ids} | tojson) else "null" end)] | join("\u001f")' "$CONFIG")
}

cmd_schema_sync() {
  local fields=false views=false fresh=false
  while (($#)); do case $1 in --fields) fields=true ;; --views) views=true ;; --fresh) fresh=true ;; *) die "schema-sync: unknown arg $1" ;; esac; shift; done
  [[ $fields == true || $views == true ]] || { fields=true; views=true; }
  cmd_resolve
  [[ $fields == false ]] || sync_fields "$fresh"
  [[ $views == false ]] || sync_views
}

# Facts for <!-- when: ... --> blocks: pace, preset, field keys, and feature words.
facts() {
  jq -r '[.pace // "timeboxed", .preset // "full", (.fields | keys[]),
    (if .fields.status.options.Triage then "triage" else empty end),
    (if .fields.status.options.Backlog then "backlog" else empty end),
    (if .fields.status.options["In Review"] then "review" else empty end),
    (if .fields.status.options.Canceled then "canceled" else empty end),
    (.labels[].name | if . == "project" then "projects" elif . == "sla-breach" then "sla" else . end),
    (if .wipLimit then "wip" else empty end), (if .endDate then "endDate" else empty end),
    (if .behaviors.assignOnBranch != false then "assign" else empty end),
    (.workflows[]?.file | sub("^linear-"; "") | sub("\\.yml$"; ""))] | join(" ")' "$CONFIG"
}

# render FILE: keeps the lines inside <!-- when: A B,C !D --> ... <!-- end --> only when every
# word is a fact (A and (B or C) and not D), keeps inline <!-- X -->text<!-- /X --> only when
# X is a fact (X may be !Y or Y,Z), then fills in the __PLACEHOLDERS__.
cmd_render() {
  [[ -f ${1:-} ]] || die "render: file not found: ${1:-}"
  local f
  f=$(facts)
  awk -v facts=" $f " '
    function ok(cond,  n, i, t, x, neg, m, alts, j, any) {
      n = split(cond, t, " ")
      for (i = 1; i <= n; i++) {
        x = t[i]; neg = 0
        if (substr(x, 1, 1) == "!") { neg = 1; x = substr(x, 2) }
        m = split(x, alts, ","); any = 0
        for (j = 1; j <= m; j++) if (index(facts, " " alts[j] " ")) any = 1
        if (any == neg) return 0
      }
      return 1
    }
    BEGIN { show = 1; d = 0 }
    /^[ \t]*<!-- when: .* -->[ \t]*$/ { c = $0; sub(/^[ \t]*<!-- when: /, "", c); sub(/ -->[ \t]*$/, "", c); stack[++d] = show; show = show && ok(c); next }
    /^[ \t]*<!-- end -->[ \t]*$/ { show = stack[d--]; next }
    show { print }' "$1" |
    jq -Rrs --slurpfile c "$CONFIG" --arg facts "$f" '
      ($facts | split(" ")) as $facts
      | def fact($x): if ($x | startswith("!")) then (fact($x[1:]) | not) else any($x | split(",")[]; . as $a | $facts | index($a)) end;
      $c[0] as $c
      | ($c.catalog.views // [] | map(select(.name as $n | ($c.views // []) | index($n)))) as $views
      | {
          SKILL_NAME: "\($c.slug)-board", TITLE: $c.title, PROJECT_URL: $c.projectUrl,
          PRIMARY_REPO: ($c.primaryRepo // ""), REPOS: ($c.repos | join(", ")),
          PACE: ($c.pace // "timeboxed"), PRESET: ($c.preset // "full"),
          INBOX: ($c.inboxStatus // "Triage"), STATUSES: ($c.fields.status.options | keys_unsorted | join(", ")),
          TYPES: ($c.issueTypes | join("|")), TYPES_LIST: ($c.issueTypes | join(", ")),
          LABELS: ([$c.labels[].name] | join(", ")),
          CYCLE_DAYS: ($c.cycle.durationDays // "" | tostring), STALE_DAYS: ($c.settings.staleDays | tostring),
          SLA_HOURS: ($c.settings.slaHours | tostring), ARCHIVE_DAYS: ($c.settings.archiveDays | tostring),
          ESTIMATES: ($c.estimateScale | map(tostring) | join(", ")),
          WIP: ($c.wipLimit // "" | tostring), END_DATE: ($c.endDate // ""),
          VIEW_SETTINGS: ($views | map("| \(.name) | \(.manual) |") | join("\n")),
          SECRET_COMMANDS: ($c.repos | map("gh secret set PROJECT_TOKEN -R \(.)") | join("\n"))
        } as $v
      | reduce ($v | to_entries[]) as $e (.; gsub("__\($e.key)__"; $e.value))
      | gsub("<!-- (?<c>!?[A-Za-z][A-Za-z,-]*) -->(?<t>.*?)<!-- /\\k<c> -->"; if fact(.c) then .t else "" end)'
}

# sync-files: copies the enabled workflows and issue forms from .github/linear-board/templates
# into .github, without touching files that exist. With --primary, also renders the local skill.
cmd_sync_files() {
  local dir="" primary=false
  while (($#)); do case $1 in --dir) dir=$2; shift 2 ;; --primary) primary=true; shift ;; *) die "sync-files: unknown arg $1" ;; esac; done
  [[ -n $dir ]] || dir=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
  local T=$dir/.github/linear-board/templates G=$dir/.github f added=()
  [[ -d $T ]] || die "templates not found: $T"
  mkdir -p "$G/workflows" "$G/ISSUE_TEMPLATE"
  for f in $(jq -r --argjson p "$primary" '.workflows[] | select($p or (.primary | not)) | .file' "$CONFIG"); do
    [[ -e $G/workflows/$f ]] && continue
    cp "$T/workflows/$f" "$G/workflows/$f"; added+=("workflows/$f")
  done
  for f in $(cfg '.issueTemplates[]'); do
    [[ -e $G/ISSUE_TEMPLATE/$f ]] && continue
    sed "s|__PROJECT__|$OWNER/$(cfg .projectNumber)|" "$T/ISSUE_TEMPLATE/$f" >"$G/ISSUE_TEMPLATE/$f"; added+=("ISSUE_TEMPLATE/$f")
  done
  echo "added: ${added[*]:-(nothing new)}"
  [[ $primary == true ]] || return 0
  local slug skill
  slug=$(cfg .slug)-board
  skill=$dir/.agents/skills/$slug
  mkdir -p "$skill" "$dir/.claude/skills"
  cmd_render "$T/local-skill.md.tmpl" >"$skill/SKILL.md"
  # One config: the skill's config.json links to the repo's .github/linear-board.json.
  [[ -L $skill/config.json ]] || { rm -f "$skill/config.json"; ln -s ../../../.github/linear-board.json "$skill/config.json"; }
  ln -sfn "../../.agents/skills/$slug" "$dir/.claude/skills/$slug"
  echo "local skill: .agents/skills/$slug (linked from .claude/skills/$slug)"
}

# upgrade: Hackathon -> Solo -> Full and Self-paced -> Timeboxed. Adds, never deletes.
cmd_upgrade() {
  local to_preset to_pace days="" start="" wip="" dir=""
  to_preset=$(preset); to_pace=$(pace)
  while (($#)); do
    case $1 in
      --preset) to_preset=$2 ;; --pace) to_pace=$2 ;; --cycle-days) days=$2 ;; --cycle-start) start=$2 ;;
      --wip) wip=$2 ;; --dir) dir=$2 ;; *) die "upgrade: unknown arg $1" ;;
    esac
    shift 2
  done
  [[ -n $dir ]] || dir=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
  jq -en --arg a "$(preset)" --arg b "$to_preset" --argjson o "$PRESETS" '($o | index($b)) != null and ($o | index($b)) >= ($o | index($a))' >/dev/null ||
    die "upgrade: preset can only go hackathon -> solo -> full (now $(preset), asked $to_preset)"
  jq -en --arg a "$(pace)" --arg b "$to_pace" --argjson o "$PACES" '($o | index($b)) != null and ($o | index($b)) >= ($o | index($a))' >/dev/null ||
    die "upgrade: pace can only go self-paced -> timeboxed (now $(pace), asked $to_pace)"
  [[ -z $wip || $wip == keep || $wip == none || ($wip =~ ^[0-9]+$ && $wip -ge 1) ]] ||
    die "upgrade: --wip must be a positive number, keep, or none"
  local new_cycle=false
  if [[ $to_pace == timeboxed && -z $(cfg '.cycle.durationDays // empty') ]]; then
    [[ -n $days && -n $start ]] || die "upgrade: Timeboxed needs --cycle-days N and --cycle-start YYYY-MM-DD|WEEKDAY"
    [[ $days =~ ^[0-9]+$ && $days -ge 1 ]] || die "upgrade: --cycle-days must be a positive integer"
    start=$(resolve_start "$start"); new_cycle=true
  fi
  [[ $wip != none || $to_pace != self-paced ]] || die "upgrade: Self-paced needs a WIP limit, so --wip none is only for Timeboxed"
  # Self-paced -> Timeboxed with a WIP limit: the user decides whether the limit stays.
  if [[ -z $wip && $(pace) == self-paced && $to_pace == timeboxed && -n $(wip_limit) ]]; then
    local how
    how="Pass --wip keep (keep $(wip_limit)), --wip N (a new limit), or --wip none (remove it)."
    [[ -t 0 ]] || die "upgrade: this board has a WIP limit of $(wip_limit). Timeboxed boards do not need one. $how"
    read -rp "This board has a WIP limit of $(wip_limit). Keep it, change it, or remove it? [keep/N/none]: " wip
    [[ $wip == keep || $wip == none || ($wip =~ ^[0-9]+$ && $wip -ge 1) ]] || die "upgrade: unknown answer '$wip'. $how"
  fi
  [[ $new_cycle == false ]] ||
    write_config --argjson d "$days" --arg s "$start" '.cycle = {durationDays: $d, startDate: $s}'
  local before
  before=$(jq -c '{views: (.views // []), workflows: [.workflows[]?.file], inbox: (.inboxStatus // "Triage"), wip: .wipLimit}' "$CONFIG")
  write_config --arg p "$to_preset" --arg c "$to_pace" --arg w "$wip" \
    '.preset = $p | .pace = $c
     | if $w == "none" then del(.wipLimit) elif $w == "" or $w == "keep" then . else .wipLimit = ($w | tonumber) end'
  "$0" --config "$CONFIG" schema-sync --fields --views
  "$0" --config "$CONFIG" labels-sync
  "$0" --config "$CONFIG" sync-files --dir "$dir" --primary
  jq -r --argjson b "$before" '
    "", "Upgraded to \(.preset), \(.pace). Nothing was deleted.",
    (if .wipLimit != $b.wip then "WIP limit: \(.wipLimit // "removed") (was \($b.wip // "none"))." else empty end),
    "", "Manual steps:",
    (if .inboxStatus != $b.inbox then "- Built-in workflow \"Item added to project\" (\(.projectUrl)/workflows): set Status to \(.inboxStatus) (was \($b.inbox))." else empty end),
    (.views - $b.views | if length > 0 then "- New views: \(join(", ")). Set grouping and sorting as in the view settings below." else empty end),
    (([.workflows[].file] - $b.workflows) | if length > 0 then "- New workflows: \(join(", ")). Commit and push them to the default branch (scheduled workflows run only from there)." else empty end),
    (.repos - [.primaryRepo] | if length > 0 then "- Other linked repos (\(join(", "))): copy .github/linear-board.json into each repo, then run there: bash .github/linear-board/board.sh sync-files" else empty end),
    "", "View settings (the API cannot set these):", "| View | Set |", "|---|---|"' "$CONFIG"
  local t
  t=$(mktemp)
  echo __VIEW_SETTINGS__ >"$t"
  cmd_render "$t"
  rm -f "$t"
}

# Hackathon end date as a countdown block in the project README (between markers, so the
# rest of the README is kept).
cmd_readme() {
  local end cur block new
  end=$(cfg '.endDate // empty')
  [[ -n $end ]] || { log "no end date configured"; return 0; }
  block=$(jq -rn --arg e "$end" --arg t "$TODAY" '
    ((($e | strptime("%Y-%m-%d") | mktime) - ($t | strptime("%Y-%m-%d") | mktime)) / 86400 | floor) as $d
    | "<!-- board:countdown -->\n**"
      + (if $d > 1 then "Ends \($e): \($d) days left" elif $d == 1 then "Ends \($e): 1 day left"
         elif $d == 0 then "Ends today (\($e))" else "Ended on \($e)" end)
      + "**\n<!-- /board:countdown -->"')
  cur=$(gql 'query($id: ID!) { node(id: $id) { ... on ProjectV2 { readme } } }' "$(jq -n --arg id "$PROJECT_ID" '{id: $id}')" | jq -r '.data.node.readme // ""')
  new=$(jq -rn --arg c "$cur" --arg b "$block" '
    if ($c | test("<!-- board:countdown -->[\\s\\S]*<!-- /board:countdown -->"))
    then $c | sub("<!-- board:countdown -->[\\s\\S]*<!-- /board:countdown -->"; $b)
    elif $c == "" then $b else $b + "\n\n" + $c end')
  if [[ $new == "$cur" ]]; then echo "README countdown is current"; return 0; fi
  gql 'mutation($p: ID!, $r: String!) { updateProjectV2(input: {projectId: $p, readme: $r}) { projectV2 { id } } }' \
    "$(jq -n --arg p "$PROJECT_ID" --arg r "$new" '{p: $p, r: $r}')" >/dev/null
  echo "README countdown: $(sed -n 2p <<<"$block" | tr -d '*')"
}

# ---------------------------------------------------------------- dispatch

cmd=${1:-help}
(($#)) && shift
case $cmd in
  items) cmd_items "$@" ;;
  show) cmd_show "$@" ;;
  profile) cmd_profile ;;
  triage) cmd_triage ;;
  next) cmd_next "$@" ;;
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
  block) cmd_block "$@" ;;
  unblock) cmd_unblock "$@" ;;
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
  readme) cmd_readme ;;
  resolve) cmd_resolve ;;
  schema-sync) cmd_schema_sync "$@" ;;
  sync-files) cmd_sync_files "$@" ;;
  render) cmd_render "$@" ;;
  upgrade) cmd_upgrade "$@" ;;
  help | -h | --help) usage ;;
  *) usage >&2; exit 2 ;;
esac
