import { ReactNode, useState } from "react";
import { DataTable, Column } from "./DataTable";

interface Props {
  title: string;
  subtitle?: ReactNode;
  legend?: ReactNode;
  children: ReactNode;
  /** Every chart has a table twin: values never depend on hover or colour alone. */
  table?: { columns: Column[]; rows: Record<string, any>[] };
  loading?: boolean;
  actions?: ReactNode;
  className?: string;
}

export function ChartCard({ title, subtitle, legend, children, table, loading, actions, className }: Props) {
  const [showTable, setShowTable] = useState(false);
  return (
    <section className={`card ${className ?? ""}`} aria-busy={loading}>
      <div className="card-head">
        <div>
          <h3 className="card-title">{title}</h3>
          {subtitle && <p className="card-sub">{subtitle}</p>}
        </div>
        <div className="spacer" />
        {actions}
        {table && (
          <button className="link-btn" onClick={() => setShowTable((v) => !v)} aria-pressed={showTable}>
            {showTable ? "Chart" : "Table"}
          </button>
        )}
      </div>
      {!showTable && legend}
      <div className={loading ? "loading" : undefined}>
        {showTable && table ? <DataTable columns={table.columns} rows={table.rows} maxRows={400} /> : children}
      </div>
    </section>
  );
}
