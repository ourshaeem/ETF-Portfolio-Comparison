import { useMemo } from "react";
import {
  CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { ChartTooltip } from "./ChartTooltip";
import { Colors } from "../theme";
import { shortDate, year } from "../format";

export interface Series {
  key: string;
  label: string;
  color: string;
}

/** Multi-series time chart: 2px lines, solid hairline grid, crosshair tooltip
 *  listing every series at the hovered date. One y-axis only. */
export function TimeLineChart({
  data, series, colors, yFormat, height = 280, logScale, referenceY, xKey = "date", yDomain,
}: {
  data: Record<string, any>[];
  series: Series[];
  colors: Colors;
  yFormat: (v: number) => string;
  height?: number;
  logScale?: boolean;
  referenceY?: number;
  xKey?: string;
  yDomain?: [number | string, number | string];
}) {
  const spanYears = data.length > 1 ? (Date.parse(data[data.length - 1][xKey]) - Date.parse(data[0][xKey])) / 3.15e10 : 0;
  // Long ranges: one tick on the first trading day of every (2nd/4th) year, so labels never repeat.
  const yearTicks = useMemo(() => {
    if (spanYears <= 3) return undefined;
    const step = spanYears > 16 ? 4 : spanYears > 8 ? 2 : 1;
    const seen = new Set<number>();
    const out: string[] = [];
    for (const d of data) {
      const y = Number(String(d[xKey]).slice(0, 4));
      if (!seen.has(y)) {
        seen.add(y);
        if (y % step === 0) out.push(d[xKey]);
      }
    }
    return out;
  }, [data, xKey, spanYears]);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid vertical={false} stroke={colors.grid} />
        <XAxis
          dataKey={xKey}
          tickFormatter={spanYears > 3 ? year : shortDate}
          ticks={yearTicks}
          interval={yearTicks ? 0 : "preserveStartEnd"}
          minTickGap={yearTicks ? 0 : 48}
          tick={{ fill: colors.muted, fontSize: 12 }}
          axisLine={{ stroke: colors.axis }}
          tickLine={false}
        />
        <YAxis
          scale={logScale ? "log" : "auto"}
          domain={yDomain ?? (logScale ? ["auto", "auto"] : ["auto", "auto"])}
          tickFormatter={yFormat}
          tick={{ fill: colors.muted, fontSize: 12 }}
          axisLine={false}
          tickLine={false}
          width={64}
          allowDataOverflow={false}
        />
        {referenceY != null && <ReferenceLine y={referenceY} stroke={colors.axis} />}
        <Tooltip
          cursor={{ stroke: colors["ink-2"], strokeWidth: 1 }}
          content={(p: any) => <ChartTooltip {...p} labelFormat={(l) => shortDate(String(l))} valueFormat={(v) => yFormat(v)} />}
        />
        {series.map((s) => (
          <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={s.color} strokeWidth={2}
                dot={false} activeDot={{ r: 4, stroke: colors.surface, strokeWidth: 2 }}
                isAnimationActive={false} connectNulls strokeLinecap="round" strokeLinejoin="round" />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
