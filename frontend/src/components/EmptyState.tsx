import type { ReactNode } from "react";

export function EmptyState({
  eyebrow,
  title,
  children,
  action,
}: {
  eyebrow: string;
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="empty-state">
      <span className="eyebrow">{eyebrow}</span>
      <h2>{title}</h2>
      <div className="empty-state__copy">{children}</div>
      {action}
    </section>
  );
}

