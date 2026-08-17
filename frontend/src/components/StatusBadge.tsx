const labels: Record<string, string> = {
  queued: "Queued",
  scanning: "Scanning",
  paused: "Paused",
  completed: "Completed",
  completed_with_errors: "Completed with errors",
  ready: "Ready",
  ignored: "Ignored",
  invalid: "Needs attention",
  downloading: "Downloading",
  converting: "Converting",
  sending: "Sending",
  sent: "Sent",
  failed: "Failed",
  cancelled: "Cancelled",
  sent_unconfirmed: "Sent — unconfirmed",
  verification_required: "Verification required",
  verified: "Verified",
  rejected: "Rejected",
  unknown: "Unknown",
  action_required: "Action required",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`status status--${status}`}>
      <span className="status__dot" aria-hidden="true" />
      {labels[status] ?? status}
    </span>
  );
}

