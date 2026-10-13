interface Entry {
  name?: string | number;
  value?: number | string | (number | string)[];
  color?: string;
  stroke?: string;
  fill?: string;
  dataKey?: string | number;
}

/** One tooltip lists every series at the hovered x. Values lead, labels follow;
 *  rows are keyed with a short line in the series colour. */
export function ChartTooltip({
  active, payload, label, labelFormat, valueFormat, hide,
}: {
  active?: boolean;
  payload?: Entry[];
  label?: any;
  labelFormat?: (l: any) => string;
  valueFormat?: (v: number, key: string) => string;
  hide?: string[];
}) {
  if (!active || !payload?.length) return null;
  const rows = payload.filter((p) => !hide?.includes(String(p.dataKey)) && p.value != null);
  return (
    <div className="tt">
      <div className="tt-title">{labelFormat ? labelFormat(label) : String(label)}</div>
      {rows.map((p) => {
        const v = Array.isArray(p.value) ? p.value : [p.value];
        const text = v.map((x) => (valueFormat && typeof x === "number" ? valueFormat(x, String(p.dataKey)) : String(x))).join(" – ");
        return (
          <div className="tt-row" key={String(p.dataKey)}>
            <i className="line-key" style={{ background: p.stroke || p.color || p.fill }} />
            <strong>{text}</strong>
            <span>{p.name}</span>
          </div>
        );
      })}
    </div>
  );
}
