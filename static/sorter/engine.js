/* A resumable bottom-up merge sort. A bucket contains equally ranked IDs. */
class BiasSorter {
  constructor(ids) {
    this.runs = ids.map((id) => [[id]]);
    this.next = [];
    this.merged = [];
    this.left = null;
    this.right = null;
    this.comparisons = 0;
    this.prepare();
  }
  prepare() {
    while (
      !this.left || !this.right || !this.left.length || !this.right.length
    ) {
      if (this.left) {
        this.next.push([
          ...this.merged,
          ...this.left,
          ...this.right,
        ]);
      }
      this.left = this.right = null;
      this.merged = [];
      if (this.runs.length === 1) this.next.push(this.runs.shift());
      if (!this.runs.length) {
        this.runs = this.next;
        this.next = [];
        if (this.runs.length <= 1) {
          this.result = this.runs[0] || [];
          return;
        }
      }
      this.left = this.runs.shift();
      this.right = this.runs.shift();
    }
  }
  pair() {
    return this.result ? null : [this.left[0][0], this.right[0][0]];
  }
  choose(choice) {
    if (this.result) return;
    if (choice === "tie") {
      this.merged.push([...this.left.shift(), ...this.right.shift()]);
    } else {this.merged.push(
        (choice === "left" ? this.left : this.right).shift(),
      );}
    this.comparisons++;
    this.prepare();
  }
  static create(ids, algorithm) {
    if (!BiasSorter.validAlgorithm(algorithm, ids)) throw new Error("Unknown sorter version");
    return algorithm ? new AdaptiveSorter(ids, algorithm) : new BiasSorter(ids);
  }
  static validAlgorithm(algorithm, ids) {
    return algorithm === undefined || (!!algorithm &&
      algorithm.name === "adaptive-v1" &&
      Number.isFinite(algorithm.focus) && algorithm.focus >= 0 && algorithm.focus <= 1 &&
      Number.isInteger(algorithm.top) && algorithm.top >= 1 && algorithm.top <= 100 &&
      Array.isArray(algorithm.seedOrder) && algorithm.seedOrder.length === ids.length &&
      new Set(algorithm.seedOrder).size === ids.length &&
      algorithm.seedOrder.every((id) => ids.includes(id)));
  }
  static replay(ids, choices, algorithm, matchups) {
    const sorter = BiasSorter.create(ids, algorithm);
    if (algorithm) {
      if (!Array.isArray(matchups) || matchups.length !== choices.length ||
          choices.length > sorter.budget) throw new Error("Invalid comparison history");
      matchups.forEach((pair, i) => sorter.record(pair, choices[i]));
      sorter.prepare();
      return sorter;
    }
    choices.forEach((choice) => sorter.choose(choice));
    return sorter;
  }
  static ranking(sorter, rounds) {
    if (sorter instanceof AdaptiveSorter) return sorter.withReviews(rounds).rankedBuckets();
    const buckets = sorter.result.map((bucket) => [...bucket]);
    BiasSorter.applyVerifySwaps(buckets, rounds);
    return buckets;
  }
  // Every pair that met during a replayed session, as "lo:hi" keys.
  static facedPairs(ids, choices) {
    const seen = new Set();
    const sorter = new BiasSorter(ids);
    for (const choice of choices || []) {
      const pair = sorter.pair();
      if (!pair) break;
      const [a, b] = pair;
      seen.add(a < b ? `${a}:${b}` : `${b}:${a}`);
      sorter.choose(choice);
    }
    return seen;
  }
  // Replays verify rounds onto merge-sort buckets: a pick preferring the
  // lower-ranked idol exchanges the two idols' bucket membership (pairs are
  // disjoint by construction; ties confirm with no change). Mutates buckets
  // in place and returns the number of picks that moved an idol.
  static applyVerifySwaps(buckets, rounds) {
    let moved = 0;
    const locate = (id) => {
      for (let b = 0; b < buckets.length; b++) {
        const i = buckets[b].indexOf(id);
        if (i >= 0) return [b, i];
      }
      return [-1, -1];
    };
    for (const round of rounds || []) {
      for (const pick of round || []) {
        if (!pick || pick.winner === "tie") continue;
        const loser = pick.winner === pick.a ? pick.b : pick.a;
        const [wb, wi] = locate(pick.winner);
        const [lb, li] = locate(loser);
        if (wb < 0 || lb < 0 || wb <= lb) continue;
        buckets[wb][wi] = loser;
        buckets[lb][li] = pick.winner;
        moved += 1;
      }
    }
    return moved;
  }
  static bound(n, algorithm) {
    if (algorithm) return AdaptiveSorter.bound(n);
    let runs = Array.from({ length: n }, () => 1), count = 0;
    while (runs.length > 1) {
      const next = [];
      for (let i = 0; i < runs.length; i += 2) {
        const size = runs[i] + (runs[i + 1] || 0);
        if (runs[i + 1]) count += size - 1;
        next.push(size);
      }
      runs = next;
    }
    return count;
  }
}

// Versioned separately: legacy choice-only links must retain their merge order.
// Seed order is ONLY an opening schedule. All personal scores start at zero.
class AdaptiveSorter {
  constructor(ids, algorithm) {
    this.ids = [...ids];
    this.algorithm = algorithm;
    this.index = new Map(ids.map((id, i) => [id, i]));
    this.history = [];
    this.edges = new Map();
    this.counts = ids.map(() => 0);
    this.comparisons = 0;
    this.coverage = this.coveragePairs();
    this.budget = AdaptiveSorter.bound(ids.length);
    this.prepare();
  }
  static bound(n) {
    // Fixed effort budget: roughly 80% of a complete merge sort for large
    // lineups, with room for broad coverage on small ones. Never exceed all
    // distinct pairs. Focus changes allocation, not session duration.
    return Math.min(n * (n - 1) / 2,
      Math.max(2 * n, Math.ceil(0.8 * BiasSorter.bound(n))));
  }
  key(a, b) { return a < b ? `${a}:${b}` : `${b}:${a}`; }
  coveragePairs() {
    const pairs = [], seen = new Set(), counts = this.ids.map(() => 0);
    const add = (a, b) => {
      const key = this.key(a, b);
      if (a === b || seen.has(key)) return;
      seen.add(key);
      pairs.push([a, b]);
      counts[this.index.get(a)]++;
      counts[this.index.get(b)]++;
    };
    const seed = this.algorithm.seedOrder;
    for (let i = 0; i + 1 < seed.length; i += 2) add(seed[i], seed[i + 1]);
    // The independently shuffled cycle connects the entire lineup. Nearby
    // seeded pairs alone would leave disconnected islands of evidence.
    this.ids.forEach((id, i) => add(id, this.ids[(i + 1) % this.ids.length]));
    const minimum = Math.min(3, this.ids.length - 1);
    this.ids.forEach((a, i) => {
      while (counts[i] < minimum) {
        let best = -1;
        this.ids.forEach((b, j) => {
          if (a !== b && !seen.has(this.key(a, b)) &&
              (best < 0 || counts[j] < counts[best])) best = j;
        });
        if (best < 0) break;
        add(a, this.ids[best]);
      }
    });
    return pairs;
  }
  record(pair, choice) {
    if (!Array.isArray(pair) || pair.length !== 2 || pair[0] === pair[1] ||
        !pair.every((id) => this.index.has(id)) ||
        !["left", "right", "tie"].includes(choice)) throw new Error("Invalid matchup");
    const [a, b] = pair, key = this.key(a, b);
    const lo = Math.min(a, b), hi = Math.max(a, b);
    const edge = this.edges.get(key) || {
      a: this.index.get(lo), b: this.index.get(hi), wins: 0, count: 0, ties: 0,
    };
    const winner = choice === "left" ? a : b;
    edge.wins += choice === "tie" ? 0.5 : Number(winner === lo);
    edge.count++;
    edge.ties += Number(choice === "tie");
    edge.last = this.history.length;
    this.edges.set(key, edge);
    this.counts[this.index.get(a)]++;
    this.counts[this.index.get(b)]++;
    this.history.push({ pair: [...pair], choice });
    this.comparisons++;
  }
  fit() {
    // Regularized Bradley–Terry fit to ALL answers, including half-wins for
    // ties. The common zero-centered prior has no leaderboard information.
    // Restarting at zero makes replay independent of intermediate renders.
    const n = this.ids.length, ridge = 0.15;
    this.scores = Array(n).fill(0);
    const adjacency = Array.from({ length: n }, () => []);
    for (const edge of this.edges.values()) {
      adjacency[edge.a].push([edge.b, edge.wins, edge.count]);
      adjacency[edge.b].push([edge.a, edge.count - edge.wins, edge.count]);
    }
    for (let pass = 0; pass < 35; pass++) {
      let change = 0;
      for (let i = 0; i < n; i++) {
        let gradient = -ridge * this.scores[i], curvature = ridge;
        for (const [j, wins, count] of adjacency[i]) {
          const p = 1 / (1 + Math.exp(this.scores[j] - this.scores[i]));
          gradient += wins - count * p;
          curvature += count * p * (1 - p);
        }
        const step = Math.max(-1, Math.min(1, gradient / curvature));
        this.scores[i] += step;
        change = Math.max(change, Math.abs(step));
      }
      if (change < 1e-6) break;
    }
    // Diagonal curvature is a scheduling heuristic, not a calibrated
    // confidence interval (human answers need not be independent).
    this.precision = adjacency.map((list, i) => ridge + list.reduce((sum, [j, , count]) => {
      const p = 1 / (1 + Math.exp(this.scores[j] - this.scores[i]));
      return sum + count * p * (1 - p);
    }, 0));
    this.order = Array.from({ length: n }, (_, i) => i).sort((a, b) =>
      this.scores[b] - this.scores[a] || a - b);
  }
  selectPair(focused, reviews = false) {
    const n = this.ids.length;
    let best = null, bestValue = -1;
    const recent = this.history.slice(-Math.min(3, Math.max(0, n - 2)));
    const consider = (r, s) => {
      const i = this.order[r], j = this.order[s];
      const a = this.ids[i], b = this.ids[j], edge = this.edges.get(this.key(a, b));
      if (edge && (!reviews || edge.count >= 2)) return;
      if (reviews && recent.some((h) => this.key(...h.pair) === this.key(a, b))) return;
      const variance = 1 / this.precision[i] + 1 / this.precision[j];
      const p = 1 / (1 + Math.exp((this.scores[j] - this.scores[i]) / Math.sqrt(1 + variance)));
      const importance = focused ? 0.08 + 1 / (1 + (r / this.algorithm.top) ** 2) : 1;
      const coverage = 1 + 1 / (1 + Math.min(this.counts[i], this.counts[j]));
      const value = importance * p * (1 - p) * variance * coverage / (1 + 3 * (edge?.count || 0));
      if (value > bestValue) { bestValue = value; best = [a, b]; }
    };
    for (let r = 0; r < n; r++) {
      // Nearby rivals plus bridges across the ranking; outsiders can climb.
      if (n <= 64) {
        for (let s = r + 1; s < n; s++) consider(r, s);
      } else {
        for (let d = 1; d <= 4 && r + d < n; d++) consider(r, r + d);
        for (let d = 8; r + d < n; d *= 2) consider(r, r + d);
      }
    }
    // Dense histories on small lists can exhaust the candidate windows.
    if (!best && n > 64) {
      for (let r = 0; r < n; r++) for (let s = r + 1; s < n; s++) consider(r, s);
    }
    // Alternate sides independently of estimated rank; replay is deterministic.
    return best && (this.comparisons % 2 ? best.reverse() : best);
  }
  prepare() {
    this.fit();
    if (this.comparisons >= this.budget || this.ids.length < 2) {
      this.result = this.rankedBuckets();
      this.currentPair = null;
      return;
    }
    if (this.comparisons < this.coverage.length) {
      const pair = this.coverage[this.comparisons];
      this.currentPair = this.comparisons % 2 ? [...pair].reverse() : [...pair];
    } else {
      const step = this.comparisons - this.coverage.length, focus = this.algorithm.focus;
      const focused = Math.floor((step + 1) * focus + 1e-8) > Math.floor(step * focus + 1e-8);
      this.currentPair = this.selectPair(focused);
    }
    if (!this.currentPair) this.result = this.rankedBuckets();
  }
  pair() { return this.result ? null : [...this.currentPair]; }
  choose(choice) {
    if (this.result) return;
    this.record(this.currentPair, choice);
    this.prepare();
  }
  withReviews(rounds) {
    const model = new AdaptiveSorter(this.ids, this.algorithm);
    this.history.forEach((h) => model.record(h.pair, h.choice));
    for (const round of rounds || []) for (const p of round) {
      model.record([p.a, p.b], p.winner === "tie" ? "tie" : p.winner === p.a ? "left" : "right");
    }
    model.fit();
    return model;
  }
  rankedBuckets() {
    // Preserve explicitly expressed ties when the fitted evidence also puts
    // those people together. Uncertainty alone never creates a tie.
    const parent = this.ids.map((_, i) => i);
    const root = (i) => { while (parent[i] !== i) i = parent[i]; return i; };
    for (const edge of this.edges.values()) {
      if (edge.ties === edge.count && Math.abs(this.scores[edge.a] - this.scores[edge.b]) < 0.15) {
        parent[root(edge.b)] = root(edge.a);
      }
    }
    const groups = new Map();
    for (const i of this.order) {
      const key = root(i);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(i);
    }
    // A decisive answer inside a transitive tie component means the group
    // isn't a consistent tie; retain individual estimated positions there.
    const conflicting = new Set();
    for (const e of this.edges.values()) {
      if (e.ties < e.count && root(e.a) === root(e.b)) conflicting.add(root(e.a));
    }
    const buckets = [];
    for (const [key, group] of groups) {
      if (conflicting.has(key)) group.forEach((i) => buckets.push([i]));
      else buckets.push(group);
    }
    const score = (bucket) => bucket.reduce((sum, i) => sum + this.scores[i], 0) / bucket.length;
    return buckets.sort((a, b) => score(b) - score(a) || a[0] - b[0])
      .map((bucket) => bucket.map((i) => this.ids[i]));
  }
}
if (typeof module !== "undefined") module.exports = BiasSorter;
