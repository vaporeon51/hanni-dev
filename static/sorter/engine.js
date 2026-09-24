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
  static replay(ids, choices) {
    const sorter = new BiasSorter(ids);
    choices.forEach((choice) => sorter.choose(choice));
    return sorter;
  }
  static bound(n) {
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
if (typeof module !== "undefined") module.exports = BiasSorter;
