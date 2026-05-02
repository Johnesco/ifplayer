# ifPlayer — project notes

**ifPlayer is an Inform 7 test runner.** Drives `glulxe -q` over stdin/
stdout via a Windows-safe threaded reader. Emits a single-page HTML
report with collapsible nested sections, per-assertion match
highlighting, word-level drift diff, and room-identity tracking.

This file is for future Claude sessions picking up the project. It
captures decisions worth not relitigating, conventions to follow, and
the surface area of the codebase.

---

## Scope

- **I7-only.** Drives `glulxe -q` exclusively. No Z-Machine, Twine,
  Ink, ChoiceScript, or browser-based interpreters.
- **One target binary at a time.** Auto-discovers `glulxe.exe` from
  `<repo>/i7/tools/interpreters/`, falls back to `PATH`.
- **Cross-engine, browser, IFDB acquisition all out of scope.**
  Explicitly decided. If a future need arises, it goes in a separate
  tool, not in ifPlayer.

## Architecture

Flat module layout — no adapter abstraction (single-engine = single
driver):

```
ifplayer/
├── cli.py              click commands: play | run | test | new | update
├── interpreter.py      glulxe -q subprocess + threaded byte-by-byte reader
├── i7.py               status parsing, win/lose/score regexes,
│                       room fingerprint (first-sentence based)
├── test_format.py      .test parser/emitter; .before parser
├── runner.py           per-turn TurnRecord stream; assertion evaluation
├── display.py          rich-based terminal renderer (L0–L4 verbosity)
├── repl.py             interactive --play mode
├── report.py           single-page HTML report generator
└── diff.py             word-level diff for soft-drift display
```

Tests live under `examples/` and `examples/zork1/`.

## File format invariants — do not change without strong reason

**`.test` (one test per file).** Header (key:value) + `---` + body.
Body is `>` commands interleaved with `?` assertions, `@` room labels,
`$` score labels, and indented recorded output.

| Symbol | Meaning |
|---|---|
| `>` | input command |
| `? text` | substring assertion (hard fail if missing) |
| `? /regex/` | regex assertion (trailing slash required) |
| `?! text` | negative assertion |
| `@ Name` | room label (display annotation) |
| `@? Name` | room assertion |
| `@? Name #N` | identity assertion (Nth distinct room of this name) |
| `~force-new` | maze escape on a room line |
| `$ N/M` | score label |
| `$? N/M` | score assertion |
| `T:N` | turn count label |
| `# label` (immediately above an assertion) | becomes that assertion's display name |
| indented text | recorded game output (soft-diff, never fails) |

**`.before` files have only `>` commands and `#` comments.** Parser
rejects every other line type. Setup is silent — output is captured
for the report but not asserted on. `.test` files reference setup with
`before: path.before` in the header (repeatable, paths relative to
the .test file). `.before` files cannot include other `.before` files.

## Reports

The HTML report (`report.py`) is the primary surface. Conventions
worth preserving:

- **Test cards** are collapsed by default; failed tests auto-open
- **Setup section** sits between the banner and the body, collapsed,
  with an "ends in @ Room · T:N · score N" summary in its header
- **Turn rows** have a tinted warm-tan background; trailing columns
  (score / delta / outcome / asserts) are fixed-width
- **Room column** is right-aligned and hugs the score column
- **Room fingerprint** is a `title=` mouseover tooltip with a dotted-
  underline cue
- **Assertions** show their `# comment` label as the primary text
  (green for pass, red for fail); pattern shown muted alongside
- **`show` toggle** on each assertion highlights matches inline in
  the response above; radio behaviour per turn

## Working on this project

A few patterns from the original collaboration session to carry
forward:

1. **Format/UX first, automation later.** When tackling a body of
   legacy tests, hand-convert several first to surface what an
   automated converter would actually need to handle. Don't build
   the converter blind.

2. **Don't bend the parser to consume legacy formats.** If we want
   `?? vital` or bare `/regex`, decide that as a format extension
   first, not as a compatibility patch.

3. **One test per file, always.** If you see a multi-test pattern
   creeping in, push back.

4. **Setup files are commands-only by design.** If you find yourself
   wanting an assertion in a `.before`, that assertion belongs in
   the test that uses the setup, not in the setup.

5. **Comment-as-label.** Every assertion deserves a human-readable
   name. Add `# Some descriptive sentence` above each `?` line so
   the report reads as documentation.

6. **Verify in preview after report-generator changes.** The local
   preview server runs from `.claude/launch.json`. Mirror the
   relevant file into `_preview/` to view it.

## Demo

Live at <https://johnesco.github.io/ifplayer/>. Pages serves
`main:/docs`. To refresh:

```bash
python -m ifplayer.cli run examples/cloak-walkthrough.test \
  --html-report docs/demo/cloak.html
python -m ifplayer.cli run examples/zork1/full-walkthrough.test \
  --html-report docs/demo/zork1.html
python -m ifplayer.cli test examples/zork1/*.test \
  --html-report docs/demo/zork-suite.html
git add docs/ && git commit -m "regenerate demo" && git push
```

Pages rebuilds in ~30–60s.

## Roadmap (deferred but planned)

- **Vital `?:abort` assertions** — halt on first failure to avoid
  cascading garbage in the rest of a test
- **DEBUG-mode probe** — when game is compiled with `Use DEBUG.`,
  send `showme yourself` after each turn and parse the room's
  internal object name. Solves the maze-disambiguation limitation
  perfectly when DEBUG is on.
- **Tag system** — `[combat]`, `[smoke]` headers + CLI filter
  (`ifplayer test --tag combat`)
- **Auto-extracting common preludes into `.before` files** — pattern
  detection across a test suite
- **`/save-test` slash-command in REPL** — author a stub `.test`
  from a play session
