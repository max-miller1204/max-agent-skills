# Manual steps (the API cannot do these)

GitHub's API cannot turn on or edit the built-in project workflows. It also cannot set view grouping, sorting, field sums, or roadmap date fields. Do these steps in the browser. Each step takes less than a minute.

## 1. Built-in workflows

Open __PROJECT_URL__/workflows

| Workflow | Setting |
|---|---|
<!-- when: !hackathon -->
| Item added to project | Turn on. When: `issue`. Set Status: **__INBOX__**. The default is Todo. Until you change it, the `git-sync` Action replaces a Todo that this workflow set with __INBOX__ about 10 seconds later. |
<!-- end -->
<!-- when: hackathon -->
| Item added to project | Turn on. When: `issue`. Set Status: **Todo** (this is the default). |
<!-- end -->
<!-- when: canceled -->
| Item closed | Turn on. Set Status: **Done**. This workflow also sets Done on issues closed as not planned or duplicate. The `git-sync` Action and the board CLI change those to **Canceled** afterwards. |
<!-- end -->
<!-- when: !canceled -->
| Item closed | Turn on. Set Status: **Done**. This board has no Canceled status, so issues closed as not planned also end as Done (they stay closed as not planned). |
<!-- end -->
| Pull request merged | Turn on. Set Status: **Done**. It only affects pull requests added to the board by hand. |
<!-- when: review -->
| Pull request linked to issue | Turn off. The `board: git-sync` Action moves linked issues to In Review. |
<!-- end -->
<!-- when: !review -->
| Pull request linked to issue | Turn off. The `board: git-sync` Action moves linked issues to In Progress. |
<!-- end -->
| Auto-close issue | Keep on. When Status changes to Done, GitHub closes the issue. |
| Auto-add to project | Turn on once for each linked repo: __REPOS__. Filter: `is:issue is:open`. Leave out `pr`: a PR would get its own card next to its issue, land in __INBOX__ with no priority, and show up in triage. Set the repository in the workflow. Free plans allow only 1 auto-add workflow. If you hit that limit, the `git-sync` Action adds new issues from every linked repo and sets them to __INBOX__. |
| Auto-archive items | Turn on. Filter: `is:issue,pr is:closed updated:<@today-__ARCHIVE_DAYS__d`. Closed items (Done, and Canceled when the board has it) are archived after __ARCHIVE_DAYS__ days. |

## 2. View settings

Open each view. Use the view menu (the arrow next to the tab name) or the toolbar. Then click **Save**.

| View | Set |
|---|---|
__VIEW_SETTINGS__

Delete any view tab that you do not need: open the view menu, then **Delete view**.
<!-- when: endDate -->

## Countdown

The project README shows "Ends __END_DATE__: N days left". There is no scheduled Action, so the `git-sync` Action refreshes it on each run (each branch, PR, or issue event). Run `bash .github/linear-board/board.sh readme` to refresh it by hand.
<!-- end -->

## 3. Per-repo Actions secret

Each linked repo needs a `PROJECT_TOKEN` secret. The default `GITHUB_TOKEN` cannot write to a user-owned or org-owned project.

1. Create a token:
   - Classic PAT: https://github.com/settings/tokens/new with scopes `project` and `repo`.
   - Or fine-grained PAT: https://github.com/settings/personal-access-tokens/new. Give it access to the linked repos (Issues: read and write, Pull requests: read, Contents: read). For a user-owned board, use a classic token. Fine-grained tokens get project access only for organization-owned projects ("Organization permissions > Projects: read and write").
2. Set the token in each repo. The command prompts for the value, so the token does not appear in your shell history:

```
__SECRET_COMMANDS__
```
