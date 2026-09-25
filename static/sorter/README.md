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
