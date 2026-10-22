#!/usr/bin/env bash
# create-views.sh --config FILE
# Creates the views that this pace and preset use (layout, filter, visible fields). The
# default "View 1" becomes the first one. Grouping, sorting, field sums and roadmap dates
# are not exposed by the API; summary.sh prints those as manual steps. "Overdue / SLA" uses
# the overdue and sla-breach labels kept current by the urgent-sla Action, because project
# filters cannot OR across different fields (due date vs label).
source "$(dirname "$0")/common.sh"
[[ ${1:-} == --config && -n ${2:-} ]] || die "usage: create-views.sh --config FILE"
load_config "$2"

bash "$BOARD" --config "$CONFIG" schema-sync --views

project_query 'views(first: 50) { nodes { number name layout filter } }' |
  jq -r '.views.nodes | sort_by(.number) | .[] | "view \(.number): \(.name) [\(.layout | sub("_LAYOUT"; "") | ascii_downcase)] \(.filter // "")"'
