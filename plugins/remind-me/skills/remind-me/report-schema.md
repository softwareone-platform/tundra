# The remind-me report document

One JSON object, written by the model and read by `scripts/render.py` together with the collector's digest. It holds only what needs judgement. Times, token usage, pull request states, folders and the commands that reopen a session all come from the digest, so a figure on the page is never the model's.

```json
{
  "day": "2026-10-05",
  "language": "en",
  "headline": "One sentence, in words, on what matters most about the day.",
  "labels": { "sessions": "sessions", "...": "..." },
  "sessions": {
    "<session id from the digest>": {
      "topics": ["what it worked on, in a few words"],
      "done": ["what it finished, one line each"],
      "open": [
        { "kind": "decision", "text": "what the person has to choose, and between what", "since": "16:36" }
      ]
    }
  }
}
```

- `day` is the digest's day.
- `headline` carries no counts. The figures under it come from the digest, and a number the model writes is the one figure on the page nothing checks.
- `language` is a BCP 47 tag such as `en` or `zh-TW`, and it sets the page's `lang`.
- `sessions` has one entry for every session in the digest, keyed by its id, and none for a session the digest does not have.
- `topics` has at least one entry. `done` and `open` may be empty.
- `kind` is `decision`, `action` or `question`. `since` is optional, `HH:MM` in local time.

## Labels

Every label is required, in the report's language.

| Key | English |
|---|---|
| `sessions` | sessions |
| `repositories` | repositories |
| `prompts` | prompts |
| `pull_requests` | new pull requests completed |
| `tokens` | input tokens (uncached) |
| `tokens_cached` | input tokens (cached) |
| `output` | output tokens |
| `open` | open |
| `done` | done |
| `topics` | topics |
| `actions` | pick up |
| `running` | running |
| `all_open` | everything left open |
| `timeline_hint` | Select a repository's name for all its sessions, or a bar for one session. A coloured bar still has something open. |
| `resume` | resume session |
| `copy_resume` | copy resume command |
| `new_session` | new session |
| `copy_terminal` | copy terminal command |
| `copy_path` | copy path |
| `show_all` | Show all |
| `copied` | copied |
| `theme` | theme |
| `theme_light` | Light |
| `theme_dark` | Dark |
| `uncommitted` | uncommitted |
| `unpushed` | unpushed |
| `nothing_open` | Nothing left open. |
| `as_of` | state as of |
| `language_hint` | To read this in another language, name it in the request, for example "in German". |
| `resume_last` | resume the last session |
| `here` | you are here |
| `active` | active |
| `session_list` | sessions |
| `decision` | decide |
| `action` | do |
| `question` | find out |

