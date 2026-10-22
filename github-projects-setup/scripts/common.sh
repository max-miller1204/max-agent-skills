# shellcheck shell=bash
# Sourced by the setup scripts.
set -euo pipefail

SKILL_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DEFAULTS=$SKILL_DIR/scripts/defaults.json
# The shared CLI (installed into each repo) owns the schema code, so setup and upgrades match.
BOARD=$SKILL_DIR/templates/github/linear-board/board.sh

die() { echo "error: $*" >&2; exit 1; }
log() { echo "==> $*" >&2; }

for bin in gh jq; do command -v "$bin" >/dev/null || die "$bin is required"; done

# gql QUERY [VARIABLES_JSON]
gql() {
  local vars=${2:-}
  [[ -n $vars ]] || vars='{}'
  jq -n --arg q "$1" --argjson v "$vars" '{query: $q, variables: $v}' | gh api graphql --input -
}

# parse_project_url URL -> sets OWNER_TYPE (user|organization), OWNER, NUMBER
parse_project_url() {
  [[ $1 =~ ^https://github\.com/(users|orgs)/([^/]+)/projects/([0-9]+) ]] ||
    die "expected a project URL like https://github.com/users/NAME/projects/3, got: $1"
  OWNER_TYPE=$([[ ${BASH_REMATCH[1]} == users ]] && echo user || echo organization)
  OWNER=${BASH_REMATCH[2]}
  NUMBER=${BASH_REMATCH[3]}
}

# project_query FRAGMENT -> runs FRAGMENT inside the owner's projectV2(number)
project_query() {
  gql "query(\$o: String!, \$n: Int!) { $OWNER_TYPE(login: \$o) { projectV2(number: \$n) { $1 } } }" \
    "$(jq -n --arg o "$OWNER" --argjson n "$NUMBER" '{o: $o, n: $n}')" |
    jq ".data.$OWNER_TYPE.projectV2"
}

# Config helpers (the generated .github/linear-board.json).
load_config() {
  CONFIG=$1
  [[ -f $CONFIG ]] || die "config not found: $CONFIG"
  OWNER_TYPE=$(jq -r .ownerType "$CONFIG")
  OWNER=$(jq -r .owner "$CONFIG")
  NUMBER=$(jq -r .projectNumber "$CONFIG")
  PROJECT_ID=$(jq -r .projectId "$CONFIG")
  PROJECT_URL=$(jq -r .projectUrl "$CONFIG")
}
