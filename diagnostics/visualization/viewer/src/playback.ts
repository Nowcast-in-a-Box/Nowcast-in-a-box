export function nextFrame(index: number, count: number): number {
  return (index + 1) % count;
}

export class SelectionGate {
  private version = 0;
  async run<T>(load: () => Promise<T>, commit: (value: T) => void): Promise<boolean> {
    const version = ++this.version;
    const value = await load();
    if (version !== this.version) return false;
    commit(value);
    return true;
  }
  cancel() { ++this.version; }
}
