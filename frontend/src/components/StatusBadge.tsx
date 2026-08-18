import { statusLabels } from "../statusLabels";

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`status status--${status}`}>
      <span className="status__dot" aria-hidden="true" />
      {statusLabels[status] ?? status}
    </span>
  );
}
