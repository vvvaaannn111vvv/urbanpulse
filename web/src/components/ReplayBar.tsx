import { useEffect, useMemo, useRef, useState } from "react";

const STEP_MS = 15 * 60 * 1000;
const PLAY_INTERVAL_MS = 450;

interface Props {
  first: Date | null;
  last: Date | null;
  value: Date | null;
  onChange: (ts: Date | null) => void;
}

/**
 * Historical replay: scrub the whole observation window in 15-minute steps, or
 * hit play to animate. Releasing the slider back to "live" returns the map to
 * the websocket feed.
 */
export function ReplayBar({ first, last, value, onChange }: Props) {
  const [playing, setPlaying] = useState(false);
  const timerRef = useRef<number | undefined>(undefined);

  const steps = useMemo(() => {
    if (!first || !last) return 0;
    return Math.max(0, Math.floor((last.getTime() - first.getTime()) / STEP_MS));
  }, [first, last]);

  const index = useMemo(() => {
    if (!first || !value) return steps;
    return Math.round((value.getTime() - first.getTime()) / STEP_MS);
  }, [first, value, steps]);

  useEffect(() => {
    if (!playing || !first || steps === 0) return;
    timerRef.current = window.setInterval(() => {
      const next = index + 1;
      if (next > steps) {
        setPlaying(false);
        onChange(null);
        return;
      }
      onChange(new Date(first.getTime() + next * STEP_MS));
    }, PLAY_INTERVAL_MS);
    return () => window.clearInterval(timerRef.current);
  }, [playing, index, steps, first, onChange]);

  if (!first || !last || steps === 0) {
    return (
      <footer className="replay">
        <span className="muted">no history available for replay</span>
      </footer>
    );
  }

  const live = value === null;

  return (
    <footer className="replay">
      <button
        type="button"
        className="chip"
        onClick={() => setPlaying((p) => !p)}
        aria-label={playing ? "pause replay" : "play replay"}
      >
        {playing ? "❚❚" : "▶"}
      </button>
      <input
        type="range"
        min={0}
        max={steps}
        step={1}
        value={index}
        aria-label="replay time"
        onChange={(event) => {
          const next = Number(event.target.value);
          onChange(next >= steps ? null : new Date(first.getTime() + next * STEP_MS));
        }}
      />
      <span className={live ? "badge live" : "badge"}>
        {live ? "LIVE" : (value as Date).toUTCString().slice(5, 22) + " UTC"}
      </span>
      {!live && (
        <button
          type="button"
          className="chip"
          onClick={() => {
            setPlaying(false);
            onChange(null);
          }}
        >
          back to live
        </button>
      )}
    </footer>
  );
}
