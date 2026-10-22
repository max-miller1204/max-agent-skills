#!/usr/bin/env bash
# install-files.sh --config FILE --dir CHECKOUT [--primary]
# Copies the board config, board.sh and the template library (.github/linear-board/templates)
# into a local checkout of a linked repo. Then board.sh sync-files installs the workflows and
# issue forms that this pace and preset use. With --primary, it also installs the
# primary-only workflows and renders the project-local board skill. Does not commit.
source "$(dirname "$0")/common.sh"

config="" dir="" primary=()
while (($#)); do
  case $1 in
    --config) config=$2; shift 2 ;;
    --dir) dir=$2; shift 2 ;;
    --primary) primary=(--primary); shift ;;
    *) die "unknown arg: $1" ;;
  esac
done
[[ -n $config && -d $dir ]] || die "usage: install-files.sh --config FILE --dir CHECKOUT [--primary]"
load_config "$config"
T=$SKILL_DIR/templates
L=$dir/.github/linear-board

mkdir -p "$L/templates/workflows" "$L/templates/ISSUE_TEMPLATE"
cp "$config" "$dir/.github/linear-board.json"
cp "$BOARD" "$L/board.sh"
chmod +x "$L/board.sh"
# The template library lets the local skill add workflows and forms later (board.sh upgrade).
cp "$T"/github/workflows/*.yml "$L/templates/workflows/"
cp "$T"/github/ISSUE_TEMPLATE/*.yml "$L/templates/ISSUE_TEMPLATE/"
cp "$T/local-skill/SKILL.md.tmpl" "$L/templates/local-skill.md.tmpl"
cp "$SKILL_DIR/references/manual-steps.md" "$L/templates/manual-steps.md"

bash "$L/board.sh" --config "$dir/.github/linear-board.json" sync-files --dir "$dir" ${primary[@]+"${primary[@]}"}
echo "installed into $dir: .github/linear-board.json, .github/linear-board/ (board.sh, templates), $(jq -r '[.workflows[] | select(.primary | not) | .file] | join(", ")' "$config")$( ((${#primary[@]})) && jq -r '[.workflows[] | select(.primary) | .file] | if length > 0 then ", " + join(", ") else "" end' "$config")"
