#!/usr/bin/env bash
# setup.sh OWNER/REPO --dir CHECKOUT [--delete-unused-defaults]
# Creates the labels in the repo, installs the issue forms, and renders the
# project-local skill into CHECKOUT. Safe to re-run: labels are upserted and
# files are overwritten. Lists GitHub's default labels that no issue uses; with
# --delete-unused-defaults, deletes them.
set -euo pipefail

SKILL_DIR=$(cd "$(dirname "$0")/.." && pwd)

die() { echo "error: $*" >&2; exit 1; }
log() { echo "==> $*" >&2; }

for bin in gh jq; do command -v "$bin" >/dev/null || die "$bin is required"; done

repo="" dir="" delete_defaults=false
while (($#)); do
  case $1 in
    --dir) dir=$2; shift 2 ;;
    --delete-unused-defaults) delete_defaults=true; shift ;;
    -*) die "unknown flag: $1" ;;
    *) repo=$1; shift ;;
  esac
done
[[ $repo == */* && -n $dir ]] || die "usage: setup.sh OWNER/REPO --dir CHECKOUT [--delete-unused-defaults]"
[[ -d $dir/.git ]] || die "not a git checkout: $dir"
gh repo view "$repo" --json name >/dev/null || die "cannot read $repo with gh"

# name|color|description
labels=(
  "triage|5319e7|New, not yet accepted"
  "bug|d73a4a|Something is broken"
  "feature|a2eeef|New capability"
  "chore|cfd3d7|Maintenance, no user-facing change"
  "urgent|b60205|Priority: drop everything"
  "high|d93f0b|Priority: next up"
  "medium|fbca04|Priority: soon"
  "low|c5def5|Priority: someday"
  "project|0e8a16|Parent issue that groups sub-issues into a project"
)
for l in "${labels[@]}"; do
  IFS='|' read -r name color desc <<<"$l"
  gh label create "$name" -R "$repo" --color "$color" --description "$desc" --force >/dev/null
done
log "labels ready: $(printf '%s\n' "${labels[@]}" | cut -d'|' -f1 | paste -sd, - | sed 's/,/, /g')"

# GitHub's defaults that this setup does not use ("bug" is kept).
defaults=(accessibility documentation duplicate enhancement "good first issue" "help wanted" invalid question wontfix)
existing=$(gh label list -R "$repo" --limit 500 --json name --jq '.[].name')
unused=()
for name in "${defaults[@]}"; do
  grep -qxF "$name" <<<"$existing" || continue
  n=$(gh issue list -R "$repo" --state all --label "$name" --limit 1 --json number --jq length)
  if [[ $n -gt 0 ]]; then log "keeping default label in use: $name"; else unused+=("$name"); fi
done
if ((${#unused[@]})); then
  if [[ $delete_defaults == true ]]; then
    for name in "${unused[@]}"; do gh label delete "$name" -R "$repo" --yes >/dev/null; done
    log "deleted unused default labels: $(printf '%s, ' "${unused[@]}" | sed 's/, $//')"
  else
    log "unused default labels (re-run with --delete-unused-defaults to delete): $(printf '%s, ' "${unused[@]}" | sed 's/, $//')"
  fi
fi

mkdir -p "$dir/.github/ISSUE_TEMPLATE"
cp "$SKILL_DIR"/templates/ISSUE_TEMPLATE/*.yml "$dir/.github/ISSUE_TEMPLATE/"
log "issue forms installed: .github/ISSUE_TEMPLATE/{bug,feature,config}.yml"

skill="${repo#*/}-issues"
skill=$(tr '[:upper:]' '[:lower:]' <<<"$skill")
mkdir -p "$dir/.agents/skills/$skill" "$dir/.claude/skills"
sed -e "s|__SKILL_NAME__|$skill|g" -e "s|__REPO__|$repo|g" \
  "$SKILL_DIR/templates/local-skill/SKILL.md.tmpl" >"$dir/.agents/skills/$skill/SKILL.md"
ln -sfn "../../.agents/skills/$skill" "$dir/.claude/skills/$skill"
log "local skill: .agents/skills/$skill/SKILL.md (linked at .claude/skills/$skill)"
