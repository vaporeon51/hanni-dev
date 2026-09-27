# Sorter ranking

New sessions use `adaptive-v1`. Sessions without an `algorithm` field continue
to use the original merge sort, including old result links.

Completed sessions persist in local storage until a new sort replaces them.
Unfinished sessions and lineup drafts expire after two hours. Returning to
lineup selection does not replace a completed ranking; pressing Start does.

“Copy progress link” copies a compressed session snapshot in a
`#continue=` link. Opening it restores and saves progress locally, then removes
the fragment so refreshing keeps subsequent choices. Devices do not sync;
copy a fresh link to transfer newer progress. Final `#ranking=` links remain supported.

## Controls

In `sorter.js`, `adaptiveDefaults.focus` is the proportion of follow-up choices
scheduled with extra weight near the top. It defaults to `0.6`; the remaining
`0.4` improves the whole list. `adaptiveDefaults.top` controls how gradually
that extra weight fades with rank (default `10`, not a hard eligibility cutoff).
These settings are saved with the session.

`seedStrength` defaults to `0.25`. Opening positions mix leaderboard percentiles
with 75% random noise. Unlisted entries get random hints. This order only
selects opening opponents: every personal rating starts at zero. A separate
uniformly shuffled order connects the entire lineup, and everyone meets at
least three distinct opponents where lineup size permits.

`AdaptiveSorter.bound` controls total effort independently of focus: 80% of the
legacy merge-sort worst-case budget, with a `2n` coverage allowance and a cap
of all distinct pairs. For example, 128 entries take 616 choices, compared
with the merge sort's worst-case 769. Small lineups may use more comparisons
than merge sort to provide coverage.

## Evidence and replay

The engine fits a regularized Bradley–Terry model to the user's actual answers,
counting a tie as a half-win. Scheduling uses score differences, approximate
uncertainty, coverage, and a smoothly decreasing rank weight. These are
heuristics, not calibrated confidence guarantees. Lower ranks are approximate.
Explicit, compatible ties can share a rank; lack of evidence alone is not a tie.

Adaptive sessions persist both choices and the actual ID pairs in `matchups`,
plus the opening `seedOrder`. Resume and undo refit this evidence without
replaying every scheduling decision or fetching updated seeds. Changes to the
fitting or scheduling rules require a new algorithm version and retaining the
old implementation so shared results remain reproducible.

Refinement asks up to ten adaptive questions per round, permits challengers
outside the top ten, and refits all evidence after each answer. Completed rounds
are used by both the sorter results and the Mine leaderboard. Legacy sessions
retain their existing review swaps.

## Checks

Run `deno test --allow-read tests/sorter_engine_test.js`. It checks coverage,
seed neutrality, legacy compatibility, replay/undo, ties, refinement, browser
event flows with mocked data, and reproducible noisy-ranking simulations.
The simulation is a regression check for the intended tradeoff, not evidence
of accuracy on real users' preferences.

## Idol leaderboard contributions

Every idol has a stable `leaderboard_id`; `role_id` remains optional Discord
metadata. Existing mapped idols retain their identity; others use
`sorter:<catalog ID>`. Multiple cards for the same person share an identity
and cannot vote against themselves. Catalog IDs are append-only; the builder
preserves existing identities and rejects reused IDs. Catalog registration
happens through `scripts/sync_sorter_idols.py`, not embedded migration data.

There is one live Elo score per idol. Existing idols start from their previous
displayed score; new idols start at 1200. Scores retain fractional precision
and are rounded for display. One recorded matchup is enough to join the ranks.
There is no confidence multiplier or lifetime contributor tracking.

Votes use K=8 with the existing daily volume decay. Each visitor can move each
idol by at most 12 Elo points per UTC day, counting gains and losses together.
A pair transfers the same amount in both directions, limited by the remaining
budget of both idols, and counts at most once per visitor per day. Repeats and
exhausted budgets add neither points nor match counts. All accounting commits
atomically, with stable lock order for simultaneous requests.

A process-local token bucket allows 12 immediate requests and replenishes four
per second, with a maximum of 2,048 tracked visitor keys. It is a cheap burst
backstop, not the authoritative vote limit. Requests beyond it are rejected;
ordinary fast choices are no longer subject to a two-second cooldown. The
API distinguishes invalid payloads, unknown IDs, repeats, exhausted budgets,
and rate limiting. Unknown IDs produce a diagnostic log without visitor data.

New daily accounting rows store hashes instead of raw cookies and expire via
the existing worker cleanup. Cookies are anonymous, not verified people.
Clearing cookies can bypass visitor-level limits. The daily cap limits Elo
movement, not how many leaderboard positions can change.

The board requests up to 45 ranked idols plus five fresh faces, selected
independently and rotated by UTC date. `include_provisional=false` suppresses
the extra sample for sorter seeding. Snapshots include only ranked idols,
serialize by week, and commit as a whole; unexpected rank collisions fail.

Selection, omitted idols, ties, and personal sorting are unchanged. Global
votes remain immediate; personal Undo does not retract a submitted global
vote. The first global answer for a pair on a given day still stands.

Groups retain the original Discord-mapped population and top-three average,
now using the single live idol score without another shrinkage calculation.
Recorded matchup totals include legacy history; they are not counts of
unique voters, and historical totals were not retrospectively deduplicated.
