"""HTML report generator — single page, three nested levels.

Layout:
  1. Run header — generated-at, suite-wide totals
  2. "Games under test" — every unique game (relative + absolute path, seed)
  3. "Tests" — one collapsible <details> per test
       └─ test summary row (always visible)
       └─ test body (visible when test is expanded)
            └─ transcript: list of <details> per turn
                 └─ turn row (always visible)
                 └─ turn body (visible when turn is expanded)
                       └─ output, assertions, drift, parser notes, metadata

Defaults: tests are collapsed; failed tests auto-open. Within an open
test, turns are collapsed; failed turns auto-open. So failures surface
without clicking, but the page stays scannable when everything passes.
No JavaScript — pure HTML5 <details>/<summary>.
"""

from __future__ import annotations

import datetime as _dt
import html
from pathlib import Path
from typing import Optional

from . import diff as diff_mod
from . import runner


# ─── CSS ─────────────────────────────────────────────────────────────


_CSS = """
:root {
  --bg: #fafafa;
  --fg: #1a1a1a;
  --muted: #6b6b6b;
  --pass: #1a7f37;
  --pass-bg: #dcffe4;
  --fail: #b81a3e;
  --fail-bg: #ffd9e0;
  --warn: #b87800;
  --code-bg: #f1efe9;
  --line: #d8d4cb;
  --accent: #4a3a25;
  --hover: #f3efe8;
  /* Game text — what the interpreter prints. Monospace, slightly warm. */
  --game-font: ui-monospace, "SF Mono", "Cascadia Code", "Fira Code", Consolas, monospace;
  /* Test/UI text — labels, counts, status. Proportional. */
  --ui-font: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", system-ui, sans-serif;
  /* Mono used inside UI for things that quote literal game/assertion text. */
  --mono: ui-monospace, "SF Mono", Consolas, monospace;
}
* { box-sizing: border-box; }
html, body { margin: 0; }
body {
  background: var(--bg);
  color: var(--fg);
  font: 15px/1.5 var(--ui-font);
  padding: 24px 32px 64px;
  max-width: 1100px;
}
h1 { font-size: 22px; margin: 0 0 4px; font-weight: 600; }
h2 {
  font-size: 13px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.5px;
  color: var(--muted);
  margin: 32px 0 10px; padding-bottom: 6px;
  border-bottom: 1px solid var(--line);
}
h3 { font-size: 16px; margin: 0 0 4px; font-weight: 600; }
h4 {
  font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.5px;
  color: var(--muted);
  margin: 14px 0 6px;
}
h4:first-child { margin-top: 0; }
.subtitle { color: var(--muted); font-size: 13px; margin-bottom: 8px; }

/* ─── summary card ─────────────────────────────────────────────── */
.summary {
  display: flex; gap: 24px; flex-wrap: wrap;
  padding: 12px 16px;
  background: white;
  border: 1px solid var(--line);
  border-radius: 6px;
  font-variant-numeric: tabular-nums;
}
.summary .item { display: flex; align-items: baseline; gap: 6px; }
.summary .num { font-size: 22px; font-weight: 600; }
.summary .label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
.summary .pass-num { color: var(--pass); }
.summary .fail-num { color: var(--fail); }

/* ─── games card ───────────────────────────────────────────────── */
.games {
  background: white;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 4px 16px;
}
.game-row {
  display: grid;
  grid-template-columns: max-content 1fr;
  column-gap: 16px;
  align-items: baseline;
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
}
.game-row:last-child { border-bottom: none; }
.game-name { font-weight: 600; font-family: var(--mono); }
.game-paths { font-family: var(--mono); font-size: 12.5px; color: var(--muted); }
.game-paths .relpath { color: var(--accent); }
.game-paths .abspath { display: block; margin-top: 2px; }
.game-paths .seed { color: var(--warn); margin-left: 8px; }

.glyph { display: inline-block; width: 16px; text-align: center; font-weight: 700; }
.glyph.pass { color: var(--pass); }
.glyph.fail { color: var(--fail); }
.glyph.error { color: var(--fail); }
.outcome-walkthrough { color: var(--pass); font-weight: 600; }
.outcome-scenario { color: var(--muted); }
.outcome-error { color: var(--fail); font-weight: 600; }

/* ─── test list (collapsible per-test cards) ─────────────────── */
.tests { background: transparent; border: none; }
details.test {
  background: white;
  border: 1px solid var(--line);
  border-radius: 8px;
  margin-bottom: 18px;
  overflow: hidden;
  box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}

/* Heavy, inverted-color title bar so it's unmistakable when scrolling
   between multiple open tests where one ends and the next begins. */
summary.test-summary {
  cursor: pointer;
  padding: 18px 22px;
  display: grid;
  grid-template-columns: 18px 28px minmax(160px, 1fr) auto;
  gap: 16px;
  align-items: center;
  user-select: none;
  list-style: none;
  background: #2b231a;
  color: #fafafa;
  border-left: 6px solid var(--pass);
}
details.test[data-status="fail"] summary.test-summary {
  background: #4a1626;
  border-left-color: var(--fail);
}
summary.test-summary::-webkit-details-marker { display: none; }
summary.test-summary::before {
  content: "▶";
  color: rgba(255,255,255,0.55);
  font-size: 12px;
  transition: transform 0.15s;
}
details.test[open] > summary.test-summary::before { transform: rotate(90deg); }
summary.test-summary:hover { filter: brightness(1.18); }
summary.test-summary .glyph { font-size: 22px; }
summary.test-summary .glyph.pass { color: #6fcf97; }
summary.test-summary .glyph.fail { color: #ff7a91; }
.test-name {
  font-weight: 700;
  font-size: 22px;
  letter-spacing: 0.2px;
}
.test-summary-meta {
  color: rgba(255,255,255,0.78);
  font-size: 13.5px;
  font-variant-numeric: tabular-nums;
}
.test-summary-meta .outcome-walkthrough { color: #6fcf97; font-weight: 600; }
.test-summary-meta .outcome-scenario { color: rgba(255,255,255,0.72); }
.test-summary-meta .outcome-error { color: #ff7a91; font-weight: 600; }

.test-body {
  padding: 0;
  background: white;
}

/* ─── setup section (.before turns) ──────────────────────────── */
details.setup {
  background: #f7f3ea;
  border-bottom: 1px solid var(--line);
}
summary.setup-summary {
  cursor: pointer;
  list-style: none;
  padding: 10px 22px;
  font-size: 12.5px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.6px;
  font-weight: 600;
  user-select: none;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.setup-end-state {
  margin-left: auto;
  text-transform: none;
  letter-spacing: 0;
  font-weight: 400;
  color: var(--muted);
  font-family: var(--ui-font);
  font-size: 12.5px;
}
.setup-end-room {
  color: #6b3a8a;
  font-family: var(--mono);
}
summary.setup-summary::-webkit-details-marker { display: none; }
summary.setup-summary::before {
  content: "▶";
  font-size: 9px;
  color: var(--muted);
  transition: transform 0.15s;
}
details.setup[open] > summary.setup-summary::before { transform: rotate(90deg); }
summary.setup-summary:hover { background: var(--hover); }
.setup-count { color: var(--accent); }
.setup-files { color: var(--muted); font-weight: 400; text-transform: none; letter-spacing: 0; font-family: var(--mono); }
ul.setup-list {
  list-style: none;
  margin: 0;
  padding: 0;
  background: #fdfbf6;
  border-top: 1px dashed var(--line);
}
li.setup-step {
  display: grid;
  grid-template-columns: 28px 52px 1fr;
  gap: 10px;
  padding: 4px 22px 4px 28px;
  font-family: var(--ui-font);
  font-size: 13px;
  color: var(--muted);
  border-bottom: 1px solid #ebe6d9;
}
li.setup-step:last-child { border-bottom: none; }
li.setup-step .setup-glyph { color: var(--muted); font-family: var(--mono); }
li.setup-step .setup-tcount { color: var(--muted); font-variant-numeric: tabular-nums; }
li.setup-step .setup-cmd { color: var(--accent); font-family: var(--mono); }
li.setup-step .setup-room {
  color: #6b3a8a;
  font-family: var(--mono);
  font-size: 12px;
  margin-left: 8px;
  opacity: 0.7;
}

/* ─── transcript (per-turn collapsible rows) ─────────────────── */
.transcript {
  list-style: none;
  margin: 0;
  padding: 0;
  background: white;
  border: none;
  border-radius: 0;
}
li.turn {
  border-bottom: 1px solid var(--line);
}
li.turn:last-child { border-bottom: none; }
details.turn-detail[data-status="fail"] {
  background: var(--fail-bg);
}
/* Fixed pixel columns so room / score / turn counter / asserts align
   vertically down the whole transcript instead of sliding with content.
   The row is UI text (sans), but the > command and @ room are quoted
   literal strings so they stay in mono. */
summary.turn-row {
  cursor: pointer;
  display: grid;
  /* Trailing columns are fixed-width so the layout stays stable whether
     a turn has assertions or not — no horizontal shift between rows. */
  grid-template-columns:
    14px              /* disclosure ▶ */
    52px              /* T:N         */
    minmax(140px, auto) /* > command */
    1fr               /* Room name (right-aligned, hugs score column) */
    78px              /* score N     */
    40px              /* +N delta    */
    50px              /* WIN/LOSE    */
    100px;            /* asserts     */
  gap: 12px;
  padding: 10px 16px;
  align-items: center;
  font-family: var(--ui-font);
  font-size: 14px;
  /* Subtle warm tint distinguishes each turn header from the white
     turn-body (and the bg-coloured extras section) when scrolling. */
  background: #f1ebdb;
  border-top: 1px solid #ddd5c0;
  user-select: none;
  list-style: none;
}
li.turn:first-child summary.turn-row { border-top: none; }
details.turn-detail[data-status="fail"] summary.turn-row {
  background: var(--fail-bg);
  border-top-color: #f0aab8;
}
summary.turn-row::-webkit-details-marker { display: none; }
summary.turn-row::before {
  content: "▶";
  color: var(--muted);
  font-size: 9px;
  transition: transform 0.1s;
}
details.turn-detail[open] > summary.turn-row::before { transform: rotate(90deg); }
summary.turn-row:hover { background: var(--hover); }
details.turn-detail[data-status="fail"] summary.turn-row:hover { background: #ffc6d3; }
.turn-room, .turn-cmd { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.turn-cmd {
  font-weight: 600;
  color: var(--accent);
  font-family: var(--mono);  /* literal player input */
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.turn-room {
  color: #6b3a8a;
  font-family: var(--mono);  /* literal room name from game */
  text-align: right;          /* hug the score column on the right */
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.room-fp {
  border-bottom: 1px dotted currentColor;
  cursor: help;
}
.turn-score { color: var(--warn); font-variant-numeric: tabular-nums; }
.turn-tcount { color: var(--muted); font-variant-numeric: tabular-nums; }
.turn-delta { color: var(--pass); font-weight: 600; font-variant-numeric: tabular-nums; }
.turn-delta.neg { color: var(--fail); }
.turn-outcome { font-weight: 700; }
.turn-outcome.win { color: var(--pass); }
.turn-outcome.lose { color: var(--fail); }
.turn-outcome.end { color: var(--muted); }
.turn-asserts {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  text-align: right;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.turn-asserts .pass { color: var(--pass); font-weight: 600; }
.turn-asserts .fail { color: var(--fail); font-weight: 600; }
.turn-asserts .pass + .fail { margin-left: 8px; }

/* ─── transcript-style response ──────────────────────────────── */
.turn-body { background: white; }
pre.turn-response, pre.opening-text {
  margin: 0;
  padding: 16px 24px 16px 38px;
  background: white;
  font-family: var(--game-font);
  font-size: 14.5px;
  line-height: 1.65;
  color: #2a2620;
  white-space: pre-wrap;
  word-wrap: break-word;
  border-top: 1px dashed var(--line);
}
pre.turn-response .room-title,
pre.opening-text .room-title {
  font-weight: 700;
  color: var(--accent);
  display: inline-block;
}
li.opening { list-style: none; }
li.opening pre.opening-text { border-top: none; padding-left: 24px; }

/* ─── per-turn extras (assertions, drift, metadata) ──────────── */
details.turn-extras {
  border-top: 1px dashed var(--line);
}
summary.turn-extras-summary {
  cursor: pointer;
  padding: 8px 16px 8px 38px;
  font-size: 13px;
  color: var(--muted);
  user-select: none;
  list-style: none;
  font-family: var(--ui-font);
}
summary.turn-extras-summary::-webkit-details-marker { display: none; }
summary.turn-extras-summary::before {
  content: "▸ ";
  font-size: 10px;
}
details.turn-extras[open] > summary.turn-extras-summary::before { content: "▾ "; }
summary.turn-extras-summary:hover { background: var(--hover); }
.turn-extras-body {
  padding: 10px 24px 14px 38px;
  background: var(--bg);
}
.turn-extras-body h4:first-child { margin-top: 0; }

ul.assertions { margin: 0; padding-left: 4px; list-style: none; }
ul.assertions li {
  padding: 5px 0;
  font-family: var(--mono);  /* asserts quote literal substrings, keep mono */
  font-size: 13px;
  position: relative;
  padding-left: 20px;
}
ul.assertions li.pass::before {
  content: "✓";
  position: absolute;
  left: 0;
  color: var(--pass);
  font-weight: 700;
}
ul.assertions li.fail::before {
  content: "✗";
  position: absolute;
  left: 0;
  color: var(--fail);
  font-weight: 700;
}
ul.assertions li .detail {
  display: block;
  color: var(--fail);
  margin-top: 2px;
  font-size: 12px;
}

/* Inline match highlight: clicking "show" on an assertion highlights
   the satisfying span(s) in the game-output block above. Click the same
   button again to clear; click a different button in the same turn to
   swap (radio-style). Highlight spans start invisible and only show
   when activated, so plain reading of the transcript is uncluttered. */
button.match-toggle {
  margin-left: 8px;
  padding: 1px 8px;
  font-family: var(--ui-font);
  font-size: 11px;
  background: white;
  color: var(--muted);
  border: 1px solid var(--line);
  border-radius: 3px;
  cursor: pointer;
  vertical-align: 1px;
}
button.match-toggle:hover { color: var(--accent); border-color: var(--accent); }
button.match-toggle.active {
  background: #ffe88a;
  color: #5a4400;
  border-color: #d9b840;
  font-weight: 700;
}
li.fail button.match-toggle.active {
  background: #ffd9e0;
  color: var(--fail);
  border-color: var(--fail);
}
.hl {
  /* baseline: no visual treatment — only highlight when activated */
}
.hl.hl-active {
  background: #ffe88a;
  color: #5a4400;
  padding: 1px 2px;
  border-radius: 2px;
  font-weight: 700;
  box-shadow: 0 0 0 1px #d9b840;
}
li.fail.matches-active ~ pre.turn-response .hl.hl-active,
.turn-detail[data-status="fail"] .hl.hl-active.hl-fail {
  background: #ffd9e0;
  color: var(--fail);
  box-shadow: 0 0 0 1px var(--fail);
}

/* ─── drift diff ──────────────────────────────────────────────── */
.drift-diff {
  background: white;
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 12px 14px;
  font-family: var(--game-font);  /* it's quoting game text */
  font-size: 13px;
  line-height: 1.7;
}
.drift-diff .equal { color: var(--muted); }
.drift-diff .del {
  background: var(--fail-bg);
  color: var(--fail);
  text-decoration: line-through;
  padding: 1px 3px;
  border-radius: 2px;
}
.drift-diff .ins {
  background: var(--pass-bg);
  color: var(--pass);
  padding: 1px 3px;
  border-radius: 2px;
}
.drift-legend {
  font-size: 11px; color: var(--muted);
  margin: 4px 0 6px;
  display: flex; gap: 12px; flex-wrap: wrap;
}
.drift-legend .swatch { display: inline-block; padding: 0 4px; border-radius: 2px; margin-right: 4px; }
.drift-legend .swatch.del { background: var(--fail-bg); color: var(--fail); text-decoration: line-through; }
.drift-legend .swatch.ins { background: var(--pass-bg); color: var(--pass); }

.error {
  background: var(--fail-bg);
  border: 1px solid var(--fail);
  border-radius: 4px;
  padding: 8px 12px;
  font-family: var(--mono);
  font-size: 12.5px;
  color: var(--fail);
}
.metadata {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px 16px;
  font-size: 12.5px;
  font-family: var(--mono);
}
.metadata dt { color: var(--muted); }
.metadata dd { margin: 0; }
"""


# ─── Public entry point ──────────────────────────────────────────────


def emit_html(
    results: list[runner.TestResult],
    *,
    title: str = "ifPlayer Report",
) -> str:
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    walkthroughs = sum(1 for r in results if r.outcome == "walkthrough")
    total_turns = sum(r.turn_count for r in results)
    total_asserts_pass = sum(r.assertion_pass for r in results)
    total_asserts = sum(r.assertion_total for r in results)
    duration_ms = sum(r.duration_ms for r in results)
    timestamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    body: list[str] = [
        f'<h1>{html.escape(title)}</h1>',
        f'<div class="subtitle">'
        f'generated {html.escape(timestamp)} · '
        f'{len(results)} test{"s" if len(results) != 1 else ""} · '
        f'{duration_ms:.0f}ms</div>',
        '<div class="summary">',
        _summary_item("pass-num", passed, "passed"),
        _summary_item("fail-num", failed, "failed"),
        _summary_item("", walkthroughs, "walkthroughs"),
        _summary_item("", total_turns, "turns"),
        _summary_item("", f"{total_asserts_pass}/{total_asserts}", "assertions"),
        '</div>',
        '<h2>Games under test</h2>',
        _render_games(results),
        '<h2>Tests</h2>',
        '<div class="tests">',
    ]
    for result in results:
        body.append(_render_test(result))
    body.append('</div>')
    return _document(title, "".join(body))


def write_report(results: list[runner.TestResult], path: Path, *, title: str = "ifPlayer Report") -> None:
    path.write_text(emit_html(results, title=title), encoding="utf-8")


_INLINE_JS = r"""
document.addEventListener('click', function (e) {
  var btn = e.target.closest && e.target.closest('.match-toggle');
  if (!btn) return;
  var turn = btn.closest('.turn-detail');
  if (!turn) return;
  var idx = btn.getAttribute('data-assertion-idx');
  var wasActive = btn.classList.contains('active');
  // Reset every toggle + highlight in this turn (radio-style: only one
  // assertion's highlights visible at a time within a turn).
  Array.prototype.forEach.call(turn.querySelectorAll('.match-toggle.active'),
    function (b) { b.classList.remove('active'); });
  Array.prototype.forEach.call(turn.querySelectorAll('.hl.hl-active'),
    function (s) { s.classList.remove('hl-active'); });
  if (wasActive) return;  // re-click on the active toggle deselects → done
  btn.classList.add('active');
  Array.prototype.forEach.call(turn.querySelectorAll('.hl'), function (span) {
    var ids = (span.getAttribute('data-asserts') || '').split(' ');
    if (ids.indexOf(idx) >= 0) span.classList.add('hl-active');
  });
});
"""


def _document(title: str, body: str) -> str:
    return (
        '<!DOCTYPE html>\n<html lang="en"><head>'
        f'<meta charset="utf-8"><title>{html.escape(title)}</title>'
        f'<style>{_CSS}</style></head><body>'
        + body +
        f'<script>{_INLINE_JS}</script>'
        '</body></html>'
    )


# ─── Sections ────────────────────────────────────────────────────────


def _summary_item(num_class: str, value: object, label: str) -> str:
    cls = f"num {num_class}".strip()
    return (
        f'<div class="item">'
        f'<span class="{cls}">{html.escape(str(value))}</span>'
        f'<span class="label">{html.escape(label)}</span>'
        f'</div>'
    )


def _render_games(results: list[runner.TestResult]) -> str:
    """List every distinct game referenced across the suite."""
    seen: dict[str, dict] = {}
    for r in results:
        rel = r.test.header.game or "(unset)"
        abs_path = _resolve_for_display(r.test)
        key = abs_path or rel
        if key not in seen:
            seen[key] = {
                "rel": rel,
                "abs": abs_path,
                "name": Path(abs_path or rel).name,
                "seeds": set(),
                "tests": [],
            }
        if r.test.header.seed is not None:
            seen[key]["seeds"].add(r.test.header.seed)
        seen[key]["tests"].append(r.test.header.test or "?")

    if not seen:
        return '<div class="games"><div class="game-row"><span class="game-name">(no games)</span></div></div>'

    parts = ['<div class="games">']
    for info in seen.values():
        seeds = ", ".join(sorted(info["seeds"])) or "(no seed)"
        parts.append('<div class="game-row">')
        parts.append(f'<div class="game-name">{html.escape(info["name"])}</div>')
        parts.append('<div class="game-paths">')
        parts.append(f'<span class="relpath">{html.escape(info["rel"])}</span>')
        parts.append(f'<span class="seed">seed: {html.escape(seeds)}</span>')
        if info["abs"] and info["abs"] != info["rel"]:
            parts.append(
                f'<span class="abspath">resolved: {html.escape(info["abs"])}</span>'
            )
        parts.append('</div>')
        parts.append('</div>')
    parts.append('</div>')
    return "".join(parts)


def _render_test(result: runner.TestResult) -> str:
    """Render one test as a <details> card. Failed tests open by default."""
    name = _test_name(result)
    glyph_cls = "pass" if result.status == "pass" else "fail"
    glyph = "✓" if result.status == "pass" else "✗"

    outcome_cls = {
        "walkthrough": "outcome-walkthrough",
        "scenario": "outcome-scenario",
        "error": "outcome-error",
    }[result.outcome]
    outcome_text = {
        "walkthrough": "WALKTHROUGH",
        "scenario": "scenario",
        "error": "ERROR",
    }[result.outcome]

    score_str = _final_score_str(result)
    asserts_str = f"{result.assertion_pass}/{result.assertion_total} ✓"

    summary_meta = (
        f'<span class="test-summary-meta">'
        f'{result.turn_count} turns'
        f' · <span class="{outcome_cls}">{outcome_text}</span>'
        + (f' · {html.escape(score_str)}' if score_str else '')
        + f' · {asserts_str}'
        f' · {result.duration_ms:.0f}ms'
        f'</span>'
    )

    open_attr = ' open' if result.status == "fail" else ''

    # Note: the per-test "WALKTHROUGH · seed N · game: ..." bar that used
    # to live here has been removed — that info is already in the global
    # summary at the top of the page (Games under test section) and in
    # this card's summary-meta line. Re-add only if a future suite mixes
    # different games per test and needs to clarify per-card.

    parts = [
        f'<details class="test"{open_attr} data-status="{result.status}">',
        '<summary class="test-summary">',
        f'<span class="glyph {glyph_cls}">{glyph}</span>',
        f'<span class="test-name">{html.escape(name)}</span>',
        summary_meta,
        '</summary>',
        '<div class="test-body">',
    ]

    if result.error:
        parts.append(f'<div class="error">{html.escape(result.error)}</div>')

    # Chronological order: banner (game launch) → setup → body turns.
    # The banner is part of the game itself; setup happens in real game
    # turns AFTER the banner; body starts where setup left off.
    parts.append('<ul class="transcript">')
    if result.opening_text.strip():
        parts.append(_render_opening(result.opening_text))
    parts.append('</ul>')

    if result.setup_turns:
        parts.append(_render_setup(result.setup_turns))

    parts.append('<ul class="transcript">')
    for record in result.turns:
        parts.append(_render_turn(record))
    parts.append('</ul>')

    parts.append('</div></details>')
    return "".join(parts)


def _render_setup(setup_turns: list[runner.TurnRecord]) -> str:
    """Collapsed Setup section between banner and body.

    Summary line shows where setup ends: room, score, turn — so you can
    see at a glance the state the body is starting in without expanding.
    """
    files = sorted({t.before_source or "" for t in setup_turns if t.before_source})
    files_label = " · ".join(files) if files else ""

    last = setup_turns[-1].state_after
    end_state_bits: list[str] = []
    if last.room:
        end_state_bits.append(
            f'ends in <span class="setup-end-room room-fp" '
            f'title="fingerprint: {html.escape(last.room.fingerprint)}">'
            f'{html.escape(last.room.label)}</span>'
        )
    end_state_bits.append(f'T:{last.turn}')
    if last.score or last.score_max is not None:
        end_state_bits.append(f'score {last.score}')
    end_state = ' · '.join(end_state_bits)

    parts = [
        '<details class="setup">',
        '<summary class="setup-summary">',
        '<span>Setup</span>',
        f'<span class="setup-count">{len(setup_turns)} command'
        f'{"s" if len(setup_turns) != 1 else ""}</span>',
    ]
    if files_label:
        parts.append(f'<span class="setup-files">{html.escape(files_label)}</span>')
    parts.append(f'<span class="setup-end-state">{end_state}</span>')
    parts.append('</summary>')

    parts.append('<ul class="setup-list">')
    for rec in setup_turns:
        if rec.state_after.room:
            room_html = (
                f'<span class="room-fp" '
                f'title="fingerprint: {html.escape(rec.state_after.room.fingerprint)}">'
                f'{html.escape(rec.state_after.room.label)}'
                f'</span>'
            )
        else:
            room_html = ""
        parts.append(
            '<li class="setup-step">'
            f'<span class="setup-glyph">·</span>'
            f'<span class="setup-tcount">T:{rec.state_after.turn}</span>'
            f'<span class="setup-cmd">&gt; {html.escape(rec.command)}'
            f'<span class="setup-room">{room_html}</span></span>'
            '</li>'
        )
    parts.append('</ul></details>')
    return "".join(parts)


def _render_opening(text: str) -> str:
    """Render the game's pre-command banner as the first transcript item."""
    return (
        '<li class="opening">'
        f'<pre class="opening-text">{html.escape(text)}</pre>'
        '</li>'
    )


def _render_response_html(
    text: str,
    room_name: Optional[str],
    assertion_matches: Optional[list[list[tuple[int, int]]]] = None,
) -> str:
    """Render game response text with two overlays:

    * Room title bolded as `<strong class="room-title">…</strong>`
    * Per-assertion match ranges wrapped in
      `<span class="hl" data-asserts="N M">…</span>` so the inline JS
      can toggle their `.hl-active` class.

    Both can overlap freely (a match could fall inside the room title);
    we resolve via a sweep-line over span boundaries.
    """
    if not text:
        return ""

    spans: list[tuple[int, int, str]] = []  # (start, end, key)
    if room_name:
        room_range = _find_room_title_range(text, room_name)
        if room_range:
            spans.append((room_range[0], room_range[1], "room"))
    if assertion_matches:
        for idx, ranges in enumerate(assertion_matches):
            for start, end in ranges:
                if start < end:
                    spans.append((start, end, str(idx)))

    if not spans:
        return html.escape(text)

    # Sweep-line: at each boundary, update active set; emit one segment
    # per (boundary, next-boundary) pair wrapped per active keys.
    n = len(text)
    events: list[tuple[int, int, str]] = []  # (pos, +1 add / -1 remove, key)
    for start, end, key in spans:
        events.append((start, 1, key))
        events.append((end, -1, key))
    # Order at same position: removals before additions (so a span ending
    # at the same point another begins doesn't carry over)
    events.sort(key=lambda e: (e[0], e[1]))

    breakpoints = sorted({0, n} | {p for p, _, _ in events})
    active: list[str] = []
    pieces: list[str] = []
    eidx = 0
    for i in range(len(breakpoints) - 1):
        bp = breakpoints[i]
        while eidx < len(events) and events[eidx][0] == bp:
            _, delta, key = events[eidx]
            if delta == -1:
                if key in active:
                    active.remove(key)
            else:
                active.append(key)
            eidx += 1
        segment = text[bp:breakpoints[i + 1]]
        if not segment:
            continue
        pieces.append(_wrap_segment(segment, active))
    return "".join(pieces)


def _wrap_segment(segment: str, active_keys: list[str]) -> str:
    if not active_keys:
        return html.escape(segment)
    is_room = "room" in active_keys
    asserts = [k for k in active_keys if k != "room"]
    out = html.escape(segment)
    if asserts:
        attrs = " ".join(asserts)
        out = f'<span class="hl" data-asserts="{attrs}">{out}</span>'
    if is_room:
        out = f'<strong class="room-title">{out}</strong>'
    return out


def _find_room_title_range(text: str, room_name: str) -> Optional[tuple[int, int]]:
    """Locate a standalone room-title line and return its (start, end) bytes."""
    cursor = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if stripped.strip() == room_name:
            ws_len = len(stripped) - len(stripped.lstrip())
            start = cursor + ws_len
            return (start, start + len(room_name))
        cursor += len(line)
    return None


def _render_turn(record: runner.TurnRecord) -> str:
    """Render one turn as a <details>. Failed turns auto-open."""
    glyph_cls = "pass" if record.status == "pass" else "fail"
    glyph = "✓" if record.status == "pass" else "✗"

    cmd = html.escape(record.command or "(empty)")
    state = record.state_after
    if state.room:
        room = (
            f'<span class="room-fp" '
            f'title="fingerprint: {html.escape(state.room.fingerprint)}">'
            f'{html.escape(state.room.label)}'
            f'</span>'
        )
    else:
        room = "—"
    score = f"score {state.score}" if state.score or state.score_max else ""

    delta_text, delta_cls = "", "turn-delta"
    if record.analysis.score_delta > 0:
        delta_text = f"+{record.analysis.score_delta}"
    elif record.analysis.score_delta < 0:
        delta_text = str(record.analysis.score_delta)
        delta_cls = "turn-delta neg"

    outcome_text, outcome_cls = "", ""
    if state.won:
        outcome_text, outcome_cls = "WIN", "win"
    elif state.lost:
        outcome_text, outcome_cls = "LOSE", "lose"
    elif state.ended:
        outcome_text, outcome_cls = "END", "end"

    asserts_html = _format_turn_asserts(record)

    turn_open = ' open' if record.status == "fail" else ''
    extras_open = ' open' if record.status == "fail" else ''

    parts = [
        '<li class="turn">',
        f'<details class="turn-detail"{turn_open} data-status="{record.status}">',
        '<summary class="turn-row">',
        f'<span class="turn-tcount">T:{state.turn}</span>',
        f'<span class="turn-cmd">&gt; {cmd}</span>',
        f'<span class="turn-room">{room}</span>',
        f'<span class="turn-score">{html.escape(score)}</span>',
        f'<span class="{delta_cls}">{html.escape(delta_text)}</span>',
        f'<span class="turn-outcome {outcome_cls}">{html.escape(outcome_text)}</span>',
        f'<span class="turn-asserts">{asserts_html}</span>',
        '</summary>',
        '<div class="turn-body">',
    ]

    if record.error:
        parts.append(f'<div class="error">{html.escape(record.error)}</div>')

    assertion_matches = [a.matches for a in record.assertions]
    response_html = _render_response_html(
        record.observed_output, record.analysis.room_name,
        assertion_matches=assertion_matches,
    )
    if response_html:
        parts.append(f'<pre class="turn-response">{response_html}</pre>')

    extras_label = _build_extras_label(record)
    parts.append(f'<details class="turn-extras"{extras_open}>')
    parts.append(
        f'<summary class="turn-extras-summary">{extras_label}</summary>'
    )
    parts.append('<div class="turn-extras-body">')

    if record.assertions:
        parts.append('<h4>Checks</h4><ul class="assertions">')
        for idx, a in enumerate(record.assertions):
            parts.append(_render_assertion(a, idx))
        parts.append('</ul>')

    if record.drift:
        parts.append(
            '<h4>Drift '
            '<span style="font-weight:400;color:var(--muted);">'
            '— recorded vs observed, word level</span></h4>'
        )
        parts.append(_render_drift_legend())
        parts.append(_render_drift_diff(record.drift))

    parser_errors = record.analysis.parser_errors
    if parser_errors:
        parts.append('<h4>Parser notes</h4>')
        parts.append(
            '<pre class="turn-response" style="border-top:none;padding:8px 12px;">'
            + html.escape("\n".join(parser_errors))
            + '</pre>'
        )

    parts.append('</div></details>')  # /turn-extras
    parts.append('</div></details></li>')
    return "".join(parts)


def _render_assertion(a: runner.AssertionResult, idx_in_turn: int) -> str:
    """Render one assertion as a list item.

    If the assertion has matches in the observed output (positive on pass,
    or negative on fail), include a `<button class="match-toggle">` whose
    JS click handler toggles `.hl-active` on the matching `<span class="hl">`
    elements inside this turn's response block. Radio-style: one
    assertion's highlights visible at a time per turn.
    """
    cls = "pass" if a.passed else "fail"
    raw = html.escape(a.assertion.raw_line.strip())
    detail_span = (
        f'<span class="detail">{html.escape(a.detail)}</span>'
        if not a.passed and a.detail else ""
    )

    toggle = ""
    if a.matches:
        toggle = (
            f'<button type="button" class="match-toggle" '
            f'data-assertion-idx="{idx_in_turn}">show</button>'
        )

    return f'<li class="{cls}">{raw}{toggle}{detail_span}</li>'


def _format_turn_asserts(record: runner.TurnRecord) -> str:
    """Compose the right-side assert-count cell.

    All-pass:   ✓ 2/2
    All-fail:   ✗ 2/2
    Mixed:      ✓ 1/2  ✗ 1/2
    No checks:  —
    """
    total = len(record.assertions)
    if total == 0:
        return "—"
    passed = sum(1 for a in record.assertions if a.passed)
    failed = total - passed
    if failed == 0:
        return f'<span class="pass">✓ {passed}/{total}</span>'
    if passed == 0:
        return f'<span class="fail">✗ {failed}/{total}</span>'
    return (
        f'<span class="pass">✓ {passed}/{total}</span>'
        f'<span class="fail">✗ {failed}/{total}</span>'
    )


def _build_extras_label(record: runner.TurnRecord) -> str:
    """Compose a short status line for the collapsed extras row."""
    bits: list[str] = []
    asserts_pass = sum(1 for a in record.assertions if a.passed)
    asserts_total = len(record.assertions)
    if asserts_total:
        if asserts_pass == asserts_total:
            bits.append(f"{asserts_total} check{'s' if asserts_total != 1 else ''} ✓")
        else:
            failed_n = asserts_total - asserts_pass
            bits.append(
                f'<span style="color:var(--fail);">'
                f'{asserts_total} checks ({failed_n} failed)'
                f'</span>'
            )
    else:
        bits.append("no checks")
    if record.drift:
        bits.append('<span style="color:var(--warn);">drift</span>')
    if record.analysis.parser_errors:
        bits.append('<span style="color:var(--warn);">parser notes</span>')
    bits.append(f"{record.elapsed_ms:.0f}ms")
    return "  ·  ".join(bits)


# ─── Drift rendering ─────────────────────────────────────────────────


def _render_drift_legend() -> str:
    return (
        '<div class="drift-legend">'
        '<span><span class="swatch del">removed</span>in recorded snapshot only</span>'
        '<span><span class="swatch ins">added</span>in observed output only</span>'
        '<span style="color:var(--muted);">unchanged words shown in grey</span>'
        '</div>'
    )


def _render_drift_diff(chunks: list[diff_mod.DiffChunk]) -> str:
    cls_map = {"equal": "equal", "delete": "del", "insert": "ins"}
    pieces: list[str] = []
    for chunk in chunks:
        text = chunk.text.strip()
        if not text:
            continue
        css_cls = cls_map[chunk.kind]
        pieces.append(f'<span class="{css_cls}">{html.escape(text)}</span>')
    return f'<div class="drift-diff">{" ".join(pieces)}</div>'


# ─── helpers ─────────────────────────────────────────────────────────


def _test_name(result: runner.TestResult) -> str:
    return (
        result.test.header.test
        or (result.test.path.name if result.test.path else "test")
    )


def _resolve_for_display(test) -> Optional[str]:
    if not test.header.game:
        return None
    raw = Path(test.header.game)
    if raw.is_absolute():
        return str(raw).replace("\\", "/")
    if test.path is not None:
        try:
            return str((test.path.parent / raw).resolve()).replace("\\", "/")
        except OSError:
            return None
    return None


def _final_score_str(result: runner.TestResult) -> str:
    if not result.turns:
        return ""
    last = result.turns[-1].state_after
    if last.score or last.score_max:
        return f"score {last.score}"
    return ""
