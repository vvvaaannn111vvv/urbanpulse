import * as d3 from "d3";
import { useEffect, useRef } from "react";

import type { HistoryPoint, Prediction } from "../types";

interface Props {
  points: HistoryPoint[];
  predictions: Prediction[];
  capacity: number;
  height?: number;
}

interface Row {
  t: Date;
  avg: number;
  lo: number;
  hi: number;
}

/**
 * D3 time-series of bikes available: a min/max band behind the 15-minute mean,
 * with the model's +15/+30/+60 forecasts drawn as a dashed continuation.
 */
export function TimeSeriesChart({ points, predictions, capacity, height = 220 }: Props) {
  const ref = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    const svg = d3.select(ref.current);
    svg.selectAll("*").remove();
    const node = ref.current;
    if (!node || points.length === 0) return;

    const width = node.clientWidth || 520;
    const margin = { top: 12, right: 14, bottom: 26, left: 34 };
    const innerW = Math.max(width - margin.left - margin.right, 10);
    const innerH = height - margin.top - margin.bottom;

    const rows: Row[] = points.map((p) => ({
      t: new Date(p.bucket),
      avg: p.avg_bikes,
      lo: p.min_bikes,
      hi: p.max_bikes,
    }));
    const forecast = predictions
      .map((p) => ({ t: new Date(p.target_ts), v: p.predicted_bikes }))
      .sort((a, b) => a.t.getTime() - b.t.getTime());

    const lastRow = rows[rows.length - 1];
    const tMax = forecast.length > 0 ? forecast[forecast.length - 1]!.t : lastRow!.t;
    const x = d3.scaleUtc().domain([rows[0]!.t, tMax]).range([0, innerW]);
    const yMax = Math.max(capacity, d3.max(rows, (r) => r.hi) ?? 1);
    const y = d3.scaleLinear().domain([0, yMax]).nice().range([innerH, 0]);

    const g = svg
      .attr("viewBox", `0 0 ${width} ${height}`)
      .append("g")
      .attr("transform", `translate(${margin.left},${margin.top})`);

    g.append("g")
      .attr("class", "grid")
      .call(d3.axisLeft(y).ticks(4).tickSize(-innerW).tickFormat(() => ""));

    g.append("path")
      .datum(rows)
      .attr("fill", "rgba(46,158,155,0.22)")
      .attr(
        "d",
        d3
          .area<Row>()
          .x((d) => x(d.t))
          .y0((d) => y(d.lo))
          .y1((d) => y(d.hi))
          .curve(d3.curveMonotoneX),
      );

    g.append("path")
      .datum(rows)
      .attr("fill", "none")
      .attr("stroke", "#2e9e9b")
      .attr("stroke-width", 2)
      .attr(
        "d",
        d3
          .line<Row>()
          .x((d) => x(d.t))
          .y((d) => y(d.avg))
          .curve(d3.curveMonotoneX),
      );

    if (forecast.length > 0 && lastRow) {
      const path = [{ t: lastRow.t, v: lastRow.avg }, ...forecast];
      g.append("path")
        .datum(path)
        .attr("fill", "none")
        .attr("stroke", "#f2c14e")
        .attr("stroke-width", 2)
        .attr("stroke-dasharray", "5 4")
        .attr(
          "d",
          d3
            .line<{ t: Date; v: number }>()
            .x((d) => x(d.t))
            .y((d) => y(d.v)),
        );
      g.selectAll("circle.fc")
        .data(forecast)
        .join("circle")
        .attr("class", "fc")
        .attr("cx", (d) => x(d.t))
        .attr("cy", (d) => y(d.v))
        .attr("r", 3.5)
        .attr("fill", "#f2c14e");
    }

    g.append("g")
      .attr("transform", `translate(0,${innerH})`)
      .call(d3.axisBottom(x).ticks(Math.max(2, Math.floor(innerW / 90))));
    g.append("g").call(d3.axisLeft(y).ticks(4));
  }, [points, predictions, capacity, height]);

  return <svg className="chart" ref={ref} role="img" aria-label="bikes available over time" />;
}
