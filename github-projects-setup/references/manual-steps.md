# Manual steps (the API cannot do these)

GitHub's API cannot turn on or edit the built-in project workflows. It also cannot set view grouping, sorting, field sums, or roadmap date fields. Do these steps in the browser. Each step takes less than a minute.

## 1. Built-in workflows

Open __PROJECT_URL__/workflows

| Workflow | Setting |
|---|---|
| Item added to project | Turn on. When: `issue, pull request`. Set Status: **Triage**. The default is Todo. Until you change it, the `git-sync` Action replaces a Todo that this workflow set with Triage about 10 seconds later. |
| Item closed | Turn on. Set Status: **Done**. This workflow also sets Done on issues closed as not planned or duplicate. The `git-sync` Action and the board CLI change those to **Canceled** afterwards. |
| Pull request merged | Turn on. Set Status: **Done**. |
| Pull request linked to issue | Turn off. The `board: git-sync` Action moves linked issues to In Review. |
| Auto-close issue | Keep on. When Status changes to Done, GitHub closes the issue. |
| Auto-add to project | Turn on once for each linked repo: __REPOS__. Filter: `is:issue,pr is:open`. Set the repository in the workflow. Free plans allow only 1 auto-add workflow. If you hit that limit, the `git-sync` Action adds new issues from every linked repo and sets them to Triage. |
| Auto-archive items | Turn on. Filter: `is:issue,pr is:closed updated:<@today-__ARCHIVE_DAYS__d`. Canceled items are closed (as not planned or duplicate), so this filter archives them too. |

## 2. View settings

Open each view. Use the view menu (the arrow next to the tab name) or the toolbar. Then click **Save**.

| View | Set |
|---|---|
| Triage (__PROJECT_URL__/views/1) | Sort by: Created, descending. |
| Backlog | Group by: Priority. Sort by: Priority. |
| Active cycle | Column by: Status (this is the default). Swimlanes (optional): Priority. |
| Upcoming cycle | Column by: Status. |
| My issues | Sort by: Priority. |
| All issues | Group by: Status. Sort by: Priority. |
| Projects | Sort by: Target date. Fields shown: Assignees (the lead), Target date, Health, Sub-issues progress. |
| Roadmap | Dates: start = (none) or Created, target = **Target date**. Zoom: Quarter. |
| Cycles | Group by: Cycle. In the Estimate column menu, choose **Sum**. |
| Overdue | Sort by: Due date, ascending. |
| SLA breach | Sort by: Updated, ascending. |

Delete any view tab that you do not need: open the view menu, then **Delete view**.

## 3. Per-repo Actions secret

Each linked repo needs a `PROJECT_TOKEN` secret. The default `GITHUB_TOKEN` cannot write to a user-owned or org-owned project.

1. Create a token:
   - Classic PAT: https://github.com/settings/tokens/new with scopes `project` and `repo`.
   - Or fine-grained PAT: https://github.com/settings/personal-access-tokens/new. Give it access to the linked repos (Issues: read and write, Pull requests: read, Contents: read). For a user-owned board, use a classic token. Fine-grained tokens get project access only for organization-owned projects ("Organization permissions > Projects: read and write").
2. Set the token in each repo. The command prompts for the value, so the token does not appear in your shell history:

```
__SECRET_COMMANDS__
```
