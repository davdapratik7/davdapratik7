# Email Manager Agent

A Claude Code agent and skill that manages a Gmail inbox:

- **Drafts replies** - saved to Gmail Drafts for you to review. It never sends.
- **Adds tags** - `Action Required`, `Waiting For Reply`, `FYI`, or your own.
- **Files mail into folders** - it looks at the folders (labels) you already
  have and reuses the best match. A new folder is created only when nothing
  existing fits.

## Requirements

- [Claude Code](https://claude.com/claude-code) (CLI, desktop or web)
- The **Gmail connector** connected to your Claude account

## Use

Open this repository in Claude Code and ask, for example:

```
Manage my inbox
Dry run: show me how you would organize this week's email
Draft replies to my unread emails
File everything from last month into folders
```

Or call the skill directly with `/email-manager`.

## Layout

| Path | Purpose |
| --- | --- |
| `.claude/skills/email-manager/SKILL.md` | The workflow and its safety rules |
| `.claude/skills/email-manager/preferences.md` | **Your** folder rules, tags and reply style - edit this |
| `.claude/agents/email-manager.md` | Subagent definition, so the work can be delegated |
| `.claude/settings.json` | Read-only Gmail tools pre-approved; send, forward, trash and label deletion blocked |

## How folders are chosen

1. A rule in `preferences.md`
2. An existing folder with the same name (case, spacing, plurals ignored)
3. An existing folder with the same meaning (`Bills` for an invoice)
4. The folder earlier mail from that sender went to
5. Only then: a new folder, nested under an existing parent where one fits

## Safety

- Replies are drafts only; sending is blocked in `settings.json`.
- Nothing is deleted or marked as spam.
- Email content is treated as untrusted data, never as instructions.
- Unclear threads stay in the inbox.

To use it in every project instead of just this one, copy
`.claude/skills/email-manager` to `~/.claude/skills/` and
`.claude/agents/email-manager.md` to `~/.claude/agents/`.
