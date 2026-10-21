export function nextFrame(index: number, count: number): number {
  return (index + 1) % count;
}

/** The target interval between two displayed frames: 1× is the original GIF's
 * 5fps pace. A speed is a viewing tempo, never a change to the data. */
export function intervalFor(speed: number): number {
  return 200 / speed;
}

/**
 * The auto-playback beat. Preparation of the next frame starts immediately
 * after a commit, and that frame is due one interval after the *actual*
 * commit it follows: a late frame shifts the beat instead of being chased.
 * start/resume/speed switches call `restart()` so playback continues from
 * "now" rather than chasing deadlines from before the pause.
 */
export class PlaybackPacer {
  private lastCommit: number | null = null;
  private pendingRestart = true;
  private readonly now: () => number;
  constructor(now: () => number = () => performance.now()) { this.now = now; }
  noteCommit() { this.lastCommit = this.now(); this.pendingRestart = false; }
  restart() { this.pendingRestart = true; }
  /** Milliseconds the next auto frame's commit must still wait. */
  commitDelay(speed: number): number {
    const origin = this.pendingRestart || this.lastCommit === null ? this.now() : this.lastCommit;
    this.pendingRestart = false;
    return Math.max(0, origin + intervalFor(speed) - this.now());
  }
}

/**
 * Renders exactly one prepared weather frame at a time: `commit` runs only
 * after `load` resolves while still current, and a superseded request can
 * neither commit nor surface its late failure to the user.
 */
export class SelectionGate {
  private version = 0;
  async run<T>(load: () => Promise<T>, commit: (value: T) => void): Promise<boolean> {
    const version = ++this.version;
    try {
      const value = await load();
      if (version !== this.version) return false;
      commit(value);
      return true;
    } catch (error) {
      if (version !== this.version) return false;
      throw error;
    }
  }
  cancel() { ++this.version; }
}

/**
 * A finished render only proves a frame is presentable when the view it
 * rendered is still the current one: view changes that happen while waiting
 * (pan, zoom, resize) re-arm the wait so a stale-view render is never
 * mistaken for readiness at the new view. `stop` (a rejecting promise such
 * as the selection's failure/timeout race) ends the loop here as well, so
 * no inner render wait stays suspended after the selection has failed.
 */
export async function awaitStableRender(
  readView: () => string, nextRender: () => Promise<void>, stop?: Promise<never>): Promise<void> {
  let view = readView();
  for (;;) {
    await (stop ? Promise.race([nextRender(), stop]) : nextRender());
    const current = readView();
    if (current === view) return;
    view = current;
  }
}
