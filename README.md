# ifPlayer

A test runner for Inform 7 games. Drives `glulxe` over stdin/stdout,
captures every turn, and renders the run as a single-page HTML report
you can drill into.

**Live demo:** <https://johnesco.github.io/ifplayer/>

- [Cloak of Darkness walkthrough](https://johnesco.github.io/ifplayer/demo/cloak.html) — 5 turns, score 2/2
- [Zork I — full walkthrough](https://johnesco.github.io/ifplayer/demo/zork1.html) — 437 turns, score 350/350

## Why

Test files read like gameplay; reports read like a transcript. No
DSL, no XML, no judgment calls about what's "really" being asserted —
just commands and the substrings/regexes you expect in each turn's
response. Setup is shared via separate `.before` files so changing one
test never silently changes 30 others.

## Install

```bash
git clone https://github.com/johnesco/ifplayer
cd ifplayer
pip install -e .
```

`glulxe.exe` (or the Unix `glulxe` binary) is auto-discovered if it sits
in `<repo>/i7/tools/interpreters/`, otherwise it must be on `PATH`.

## Test format

```
test: cloak-walkthrough
game: ../cloak-inform7.ulx
seed: 42
---

> west
? Cloakroom
? hooks

> hang cloak on hook
? velvet cloak
? /score has (just )?gone up/

> read message
? *** The End ***
```

Markers:

| Symbol | Meaning |
|---|---|
| `>` | input command |
| `?` text | substring assertion (must be present) |
| `?` /regex/ | regex assertion |
| `?!` text | negative assertion (must NOT be present) |
| `@` Name | room label (display annotation) |
| `@?` Name | room assertion |
| `@?` Name `#N` | identity assertion (Nth distinct room of this name) |
| `$` N | score label |
| `$?` N | score assertion |
| `T:N` | turn count label |

Indented prose between `>` and `?` lines is the recorded game output —
soft-diff only, drift surfaces as a word-level diff in the report but
never fails the test.

## Shared setup with `.before`

```
test: zork1-troll-fight
game: ../../i7/zork1/zork1.ulx
seed: 26
before: zork1-cellar-nav.before
---

> n
? Troll Room
> kill troll with sword
...
```

A `.before` file is just `>` commands and `#` comments — no
assertions allowed. Setup runs silently, doesn't count toward the
test's turn or assertion totals, and surfaces in the report as a
collapsible "Setup" bar with the post-setup state ("ends in Cellar
· T:27 · score 24").

## CLI

```
ifplayer play <game.ulx>                  REPL against the game
ifplayer run <test.test>                  run one test, render to terminal
ifplayer run <test.test> --html-report R  also write HTML to R
ifplayer test <tests...>                  run many, summary across all
ifplayer new <name> --game G              stub a new test file
ifplayer update <test.test>               rewrite with observed output
```

Verbosity: `-q` quiet · default · `-v` verbose · `-vv` debug · `-vvv` trace.
Failed turns auto-expand to verbose level inline.

## Report

The HTML report has:

- **Header** — game(s) under test, seed, totals
- **Test cards** — collapsed by default; failing tests auto-open
- **Setup bar** — collapsed; shows where setup left the player
- **Banner** — game's opening text, before any commands
- **Turn rows** — `T:N > command @ Room score N (delta) WIN ✓ N/N` per turn
- **Per-turn body** — game response styled as transcript
- **Checks** — each assertion line with a `show` toggle that highlights
  the satisfying match(es) inside the response above
- **Drift diff** — word-level red/green/grey when recorded ≠ observed

No JavaScript framework — just native HTML5 `<details>`/`<summary>` and
~20 lines of inline JS for the assertion-highlight toggles.

## Limitations

- **Z-Machine maze rooms** are intentionally identical in both name and
  description, so stdout-based fingerprinting collapses them. Use
  `~force-new` after `@? Maze` in a `.test` to manually disambiguate
  visits, or pass the game a DEBUG build for true introspection.
- **Score max is unknown** until the game prints its end-of-game line
  ("you scored 350 out of 350, in 437 turns"); during play, score
  shows current only.
- **Inform 7 only** — drives the Glulx interpreter exclusively. No
  Z-Machine, no Twine, no Ink. By design.

## License

MIT
