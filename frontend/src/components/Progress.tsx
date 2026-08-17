export function Progress({ value, label }: { value: number; label: string }) {
  return (
    <div className="progress-wrap">
      <div className="progress-copy">
        <span>{label}</span>
        <span className="tabular">{value}%</span>
      </div>
      <div
        className="progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value}
        aria-label={label}
      >
        <span style={{ transform: `scaleX(${Math.max(0, Math.min(100, value)) / 100})` }} />
      </div>
    </div>
  );
}

