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
and are rounded for display. Idol ranks, group ranks, and snapshot ranks use
unrounded scores; alphabetical ordering only breaks exact ties. One recorded
matchup is enough to join the ranks.
There is no confidence multiplier or lifetime contributor tracking.

New idol sorts submit their actual comparisons once on completion, using
K=8 without daily volume decay. Ties count as half-wins; Undo before completion
removes the answer from the ballot. Each distinct pair uses its last answer
within the ballot. Daily pair deduplication still applies across ballots.

All eligible comparisons use the same pre-update ratings. Each comparison is
weighted by one divided by the larger endpoint comparison count. Contributions
are combined per idol before applying the remaining 12-point visitor/idol/UTC-day
budget. Budgets count absolute net movement per ballot, not individual clicks.
If clipping leaves unequal total gains and losses, the larger side is scaled
down to preserve total Elo. Integer decimal units preserve exact zero-sum
updates. This bounds an idol's uncapped movement below 8 points per ballot;
a two-idol win against an equal-rated opponent moves the winner 4 points.

Migration 46 adds durable UUID ballot receipts. Receipts, pair deduplication,
budgets, counters and ratings commit atomically. Refreshes, retries and shared
copies of the same ballot cannot count again, even on another day. Failed
submissions retain a frozen payload. Temporary network/server failures and rate
limits retry up to three times after 2, 5, and 15 seconds while the same results
remain open. Each request times out after 15 seconds. Invalid submissions do not
automatically retry; reopening results can retry an unacknowledged ballot.
Only newly started sessions carry ballot IDs; existing saved sessions are not
retroactively submitted. Cached clients' old per-click endpoint returns a no-op.

The first completed result submits automatically. Refinement and Undo after
submission affect the personal ranking only; they do not retract or replace a
submitted ballot. Unfinished sorts and group sorts do not submit global ballots.
The leaderboard UI is unchanged.

A process-local burst limiter remains as a cheap backstop. Anonymous cookies
are not verified people; clearing cookies can bypass visitor-level limits.
Daily accounting stores hashed visitor keys and expires through the worker.
Ballot receipts remain durable. Match counts represent newly accepted unique
comparisons, including ties and balanced results; fully budget-blocked ballots
add no counts. These are not unique-voter counts.

The board requests up to 45 ranked idols plus five fresh faces, selected
independently and rotated by UTC date. `include_provisional=false` suppresses
the extra sample for sorter seeding. Snapshots include only ranked idols,
serialize by week, and commit as a whole; unexpected rank collisions fail.

Selection, omitted idols, and personal sorting are unchanged. The first
submitted global answer for a pair on a given day still stands.

Groups retain the original Discord-mapped population and top-three average,
now using the single live idol score without another shrinkage calculation.
Recorded matchup totals include legacy history; they are not counts of
unique voters, and historical totals were not retrospectively deduplicated.
