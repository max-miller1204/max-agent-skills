#!/usr/bin/env bash
# install-files.sh --config FILE --dir CHECKOUT [--primary]
# Copies the board config, board.sh, workflows and issue templates into a local
# checkout of a linked repo. With --primary, also installs the primary-only
# workflows and generates the project-local board skill. Does not commit.
source "$(dirname "$0")/common.sh"

config="" dir="" primary=false
while (($#)); do
  case $1 in
    --config) config=$2; shift 2 ;;
    --dir) dir=$2; shift 2 ;;
    --primary) primary=true; shift ;;
    *) die "unknown arg: $1" ;;
  esac
done
[[ -n $config && -d $dir ]] || die "usage: install-files.sh --config FILE --dir CHECKOUT [--primary]"
load_config "$config"
T=$SKILL_DIR/templates
G=$dir/.github

mkdir -p "$G/linear-board" "$G/workflows" "$G/ISSUE_TEMPLATE"
cp "$config" "$G/linear-board.json"
cp "$T/github/linear-board/board.sh" "$G/linear-board/board.sh"
chmod +x "$G/linear-board/board.sh"

workflows=(linear-git-sync linear-stale-flag linear-urgent-sla)
[[ $primary == true ]] && workflows+=(linear-cycle-rollover linear-weekly-digest)
for w in "${workflows[@]}"; do cp "$T/github/workflows/$w.yml" "$G/workflows/$w.yml"; done

for f in "$T"/github/ISSUE_TEMPLATE/*.yml; do
  sed "s|__PROJECT__|$OWNER/$NUMBER|" "$f" >"$G/ISSUE_TEMPLATE/$(basename "$f")"
done
echo "installed into $dir: .github/linear-board.json, .github/linear-board/board.sh, ${workflows[*]/%/.yml}, issue templates"

[[ $primary == true ]] || exit 0

slug=$(jq -r .slug "$config")-board
skill=$dir/.agents/skills/$slug
mkdir -p "$skill" "$dir/.claude/skills"
cp "$config" "$skill/config.json"
jq -r --rawfile tmpl "$T/local-skill/SKILL.md.tmpl" '
  . as $c
  | $tmpl
  | gsub("__SKILL_NAME__"; "\($c.slug)-board")
  | gsub("__TITLE__"; $c.title)
  | gsub("__PROJECT_URL__"; $c.projectUrl)
  | gsub("__PRIMARY_REPO__"; $c.primaryRepo)
  | gsub("__REPOS__"; ($c.repos | join(", ")))
  | gsub("__CYCLE_DAYS__"; ($c.cycle.durationDays | tostring))
  | gsub("__STALE_DAYS__"; ($c.settings.staleDays | tostring))
  | gsub("__SLA_HOURS__"; ($c.settings.slaHours | tostring))
  | gsub("__ESTIMATES__"; ($c.estimateScale | map(tostring) | join(", ")))' "$config" >"$skill/SKILL.md"
ln -sfn "../../.agents/skills/$slug" "$dir/.claude/skills/$slug"
echo "generated local skill: .agents/skills/$slug (linked from .claude/skills/$slug)"
