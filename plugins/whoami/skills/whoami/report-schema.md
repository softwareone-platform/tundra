# Report document

The report is one JSON document, written in the person's language, which `scripts/render_report.py` turns into an HTML file and a Markdown summary. The renderer escapes every value, so write plain text everywhere: no HTML, no Markdown markup.

**Plain language in every field a reader sees.** Describe an instance by what happened ("two of the four backfill scripts stop early when a whole batch is already linked"), never by an identifier. Commit SHAs, file paths, line numbers, and class names go only in an instance's `ref`, which the HTML shows inside the collapsed evidence. The renderer rejects anything that looks like a commit SHA in a field shown before the reader expands something: the title, the scope line, the summary and its axes, the diagrams, a pattern's name, description, trade-offs and constraint, and the implications. The evidence, the appendix, and the scope table may cite commits.

```json
{
  "language": "zh-TW",
  "title": "whoami: acme-orders",
  "scope_line": "one line: the repositories, how much was read, and where AI involvement shows",
  "timeline": {
    "recent_from": "2026-03-02",
    "repositories": [
      { "name": "acme-orders", "from": "2024-11-04", "to": "2026-09-20", "ai_from": "2026-02-10" }
    ],
    "prompts": { "from": "2026-08-25", "to": "2026-09-24" }
  },
  "labels": { "summary": "結論", "strengths": "優點", "...": "..." },

  "summary": {
    "text": "several sentences, paragraphs separated by a blank line",
    "axes": [
      {
        "name": "the axis in a few words",
        "description": "what the axis is about and why it matters, two or three sentences",
        "strong": "the kind of question the person reliably gets right",
        "weak": "the kind of question they reliably miss",
        "patterns": ["pattern ids that support it"]
      }
    ]
  },
  "diagrams": [
    {
      "caption": "what it shows",
      "lanes": [
        {
          "title": "optional, when the lanes are compared side by side",
          "steps": [
            { "text": "the step in a few words", "detail": ["optional lines under it"], "tone": "neutral", "note": "optional label on the arrow to the next step" }
          ]
        }
      ]
    }
  ],

  "strengths": [ PATTERN ],
  "gaps": [ PATTERN ],
  "styles": [ PATTERN, plus "gives" and "costs" ],

  "implications": [
    { "area": "where it shows up", "meaning": "what it means for the person's work", "patterns": ["ids"] }
  ],

  "scope": { "label shown to the reader": "value, or a list of values" },
  "dissolved": [ { "name": "the candidate", "evidence": "what refuted it" } ],
  "events": [ { "text": "a single event noticed along the way", "ref": "where" } ]
}
```

A `PATTERN` is:

```json
{
  "id": "short-kebab-id",
  "name": "the pattern in a few words",
  "description": "what it looks like and why it matters, two to four sentences",
  "sources": ["code", "prompts", "instructions"],
  "confidence": { "level": "verified" }  or  { "level": "depends", "on": "the constraint nobody could check" },
  "instances": [ { "text": "what happened, in plain language", "ref": "repository sha path:line" } ],
  "exceptions": [ { "text": "...", "ref": "..." } ],
  "calibration": "how the rest of the repository compares, as counts",
  "checked": "what was checked to test it"
}
```

- The **timeline** is drawn under the scope line, above the conclusion, so the reader sees what the conclusion rests on before reading it. Every date is written `YYYY-MM-DD` and is one you measured, never an estimate. A repository's `from` and `to` are the dates of the oldest and newest of its changes you read. `recent_from` is the date of the oldest of the newest changes you read closely, and the changes before it are drawn as the thinner sample; leave it out when you read every change. `ai_from` is the date of the earliest evidence that AI took part, left out when there is none. `prompts` spans the first and last prompt you read, left out when you read none. The timeline is optional, and the Markdown summary leaves it to the scope line.
- A **diagram** is structure, never text drawn as boxes: a text-drawn box misaligns as soon as it holds CJK characters. Each lane is a flow read top to bottom, one step per box, with an arrow between consecutive steps. Put a comparison in two or three lanes of one diagram, which sit side by side, rather than in separate diagrams. A step's `tone` is `neutral`, `strong` for what the person reliably gets right, or `weak` for what they reliably miss, and it takes the same colours as the axes. Every diagram needs a `caption`, which the HTML and the Markdown summary both show as the diagram's title above it, every lane of a diagram with more than one lane needs a `title`, and a diagram holds at most three lanes: the Markdown summary lists each lane as a numbered list, and only a caption or a title keeps one list from running into the next. `detail` is a list of text. A `note` on a lane's last step has no arrow to label and is not shown.
- `strengths`, `gaps`, and `styles` are separate lists. A **style** is a way of working that is neither a strength nor a gap: a trade-off, stated with what it `gives` and what it `costs`.
- A pattern that narrowed is stated in its narrowed form, with `verified` confidence.
- A conditional pattern has `depends` confidence, and `on` names the constraint. Its table shows only the `conditional` label, with the constraint in a tooltip and in the evidence, and the Markdown summary spells it out under the table.
- An axis's `patterns` may mix groups. Each side of the axis lists the ones from its own group, strengths on the side it gets right and gaps on the side it misses, and styles go below both.
- A dissolved candidate goes in `dissolved`, never in a pattern list.
- Every pattern needs at least two `instances`. A strength needs `exceptions`, which may be an empty list only when you looked and found none.
- `labels` translates the headings into the report's language. Give every label or none: the renderer rejects a `labels` object with any key missing, because a missing label would fall back to English in the middle of a report written in another language. The keys are `summary`, `strong`, `weak`, `strengths`, `gaps`, `styles`, `implications`, `scope`, `appendix`, `dissolved`, `events`, `pattern`, `description`, `source`, `confidence`, `evidence`, `gives`, `costs`, `area`, `meaning`, `related`, `verified`, `depends`, `conditional`, the short confidence label for a conditional pattern, `instances`, `exceptions`, `calibration`, `checked`, `report_file`, `code`, `prompts`, `instructions`, `timeline_thin`, `timeline_close`, `timeline_ai`, and `timeline_prompts`, the timeline's legend and prompts row, `separator`, the text between two sources, such as "、" in Chinese, `list_separator`, the text between two items in the summary, such as "；" in Chinese, because an item can itself hold a comma, and `colon`, the text between a label and its value, such as "：" in Chinese.

The renderer checks the document before writing anything and exits 2 with a list of what is wrong. Fix the document and run it again.
