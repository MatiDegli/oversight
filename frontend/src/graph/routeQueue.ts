interface Entry {
  key: number;
  cost: number;
}

const before = (a: Entry, b: Entry) => a.cost < b.cost || (a.cost === b.cost && a.key < b.key);

/** The visibility search's cost/key order, without sorting the entire frontier on every pop. */
export class RouteQueue {
  private entries: Entry[] = [];

  get length(): number {
    return this.entries.length;
  }

  push(entry: Entry): void {
    let index = this.entries.length;
    this.entries.push(entry);
    while (index > 0) {
      const parent = Math.floor((index - 1) / 2);
      const above = this.entries[parent];
      if (!above || !before(entry, above)) break;
      this.entries[index] = above;
      index = parent;
    }
    this.entries[index] = entry;
  }

  pop(): Entry | undefined {
    const first = this.entries[0];
    const last = this.entries.pop();
    if (!last || !this.entries.length) return first;
    let index = 0;
    while (index * 2 + 1 < this.entries.length) {
      let child = index * 2 + 1;
      const left = this.entries[child];
      const right = this.entries[child + 1];
      if (left && right && before(right, left)) child += 1;
      const below = this.entries[child];
      if (!below || !before(below, last)) break;
      this.entries[index] = below;
      index = child;
    }
    this.entries[index] = last;
    return first;
  }
}
