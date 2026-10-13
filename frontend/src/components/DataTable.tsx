export interface Column {
  key: string;
  label: string;
  format?: (v: any, row: Record<string, any>) => string;
}

export function DataTable({
  columns, rows, maxRows, highlight,
}: {
  columns: Column[];
  rows: Record<string, any>[];
  maxRows?: number;
  highlight?: (row: Record<string, any>) => boolean;
}) {
  const shown = maxRows ? rows.slice(-maxRows) : rows;
  return (
    <div className="table-wrap" style={maxRows ? { maxHeight: 360, overflowY: "auto" } : undefined}>
      <table>
        <thead>
          <tr>{columns.map((c) => <th key={c.key} scope="col">{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={i} className={highlight?.(r) ? "highlight" : undefined}>
              {columns.map((c) => (
                <td key={c.key}>{c.format ? c.format(r[c.key], r) : String(r[c.key] ?? "—")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
