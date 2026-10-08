# The remind-me report document

One JSON object, written by the model and read by `scripts/render.py` together with the collector's digest. It holds only what needs judgement. The page's figures, the timeline's times, token usage, folders and the commands that reopen a session all come from the digest. The model writes the headline, the topics, the lines of what happened and what is open, and an emoji per repository.

```json
{
  "day": "2026-10-05",
  "language": "en",
  "headline": "One sentence, in words, on what matters most about the day.",
  "labels": { "sessions": "sessions", "...": "..." },
  "marks": { "<repository path exactly as the digest writes it>": "🧾" },
  "sessions": {
    "<session id from the digest>": {
      "topics": ["what it worked on, in a few words"],
      "decided": ["a choice the person made or accepted, with what was chosen"],
      "done": ["what the work produced, one line each"],
      "found": ["something established: a cause, a fact confirmed, a measurement"],
      "open": [
        { "kind": "decision", "text": "what the person has to choose, and between what", "detail": "why it matters, and what the session recommended" }
      ]
    }
  }
}
```

- `day` is the digest's day.
- `headline` carries no counts. The figures under it come from the digest, and a number the model writes there, as in any item's text, is one nothing checks; above the figures it would read as one of them.
- `language` is a BCP 47 tag such as `en` or `zh-TW`, and it sets the page's `lang`.
- `sessions` has one entry for every session in the digest, keyed by its id, and none for a session the digest does not have.
- `topics` has at least one entry. `decided`, `done`, `found` and `open` may be empty or left out; each line is a non-blank string.
- `kind` is `decision`, `action` or `question`. `text` is the item in one line: what to decide, do or find out, and for a decision what it chooses between, since the session summary prints `text` alone. `detail`, optional, holds the rest: why, the consequences, what the session recommended. Split by meaning, not by punctuation.
- `marks`, optional, maps a repository's `path` from the digest to one common emoji chosen from the meaning of its name, a different one for each repository and never a flag. A mark that cannot be used, a duplicate included, gets the repository's initial instead; it never fails the render.
- Any other field, at the top level, in a session or in an open item, fails the render with its name, so a misspelt one is caught rather than dropped.

## Labels

Every label is required, in the report's language. A label that holds a number or a name keeps its placeholders, such as `{n}`, exactly as written, in whatever place the language puts them; the renderer fills them in.

| Key | English |
|---|---|
| `sessions` | sessions |
| `repositories` | repositories |
| `repository_list` | REPOSITORIES |
| `prompts` | prompts |
| `pull_requests` | new pull requests completed |
| `tokens` | input tokens (not from cache) |
| `tokens_cached` | input tokens (cached) |
| `output` | output tokens |
| `token_mix` | token mix |
| `all_open` | everything left open |
| `level_day` | DAY |
| `level_repository` | REPOSITORY |
| `level_session` | SESSION |
| `session_position` | {n} of {total} |
| `timeline` | Timeline |
| `session_count` | {n} sessions in {repository} |
| `duration` | {hours} h {minutes} min |
| `duration_minutes` | {minutes} min |
| `still_open` | Still open |
| `what_happened` | What happened |
| `nothing_open` | Nothing left open. |
| `nothing_happened` | Nothing recorded as decided, done or found. |
| `decided` | decided |
| `done` | done |
| `found` | found |
| `decision` | decide |
| `action` | do |
| `question` | find out |
| `show_all` | Show all |
| `running` | running |
| `running_count` | {n} running |
| `cannot_resume` | Running, so it cannot be resumed from here |
| `here` | you are here |
| `state_unread` | state not read |
| `uncommitted` | uncommitted |
| `unpushed` | unpushed |
| `as_of` | state as of |
| `resume` | resume session |
| `copy_resume` | copy resume command |
| `resume_last` | resume the last session |
| `new_session` | new session |
| `copy_terminal` | copy terminal command |
| `copy_path` | copy path |
| `copied` | copied |
| `theme` | theme |
| `theme_light` | Light |
| `theme_dark` | Dark |
| `cache_read` | cache read |
| `cache_write` | cache write |
| `uncached_input` | uncached input |
| `language_hint` | To read this in another language, name it in the request, for example "in German". |
