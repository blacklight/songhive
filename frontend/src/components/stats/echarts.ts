/**
 * Shared ECharts setup for the listening-stats components.
 *
 * Modules are registered once here (on-demand imports keep the stats chunk
 * small); chart components import ``VChart`` from this module rather than
 * ``vue-echarts`` directly.
 */
import { use } from "echarts/core";
import { BarChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  PolarComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import VChart from "vue-echarts";

use([
  BarChart,
  GridComponent,
  LegendComponent,
  PolarComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export { VChart };

/** Resolve a CSS custom property at chart-build time (theme colors live in CSS vars). */
export function cssVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return value || fallback;
}

/** Categorical palette for stacked series. */
export const CHART_PALETTE = [
  "#5470c6",
  "#91cc75",
  "#fac858",
  "#ee6666",
  "#73c0de",
  "#3ba272",
  "#fc8452",
  "#9a60b4",
  "#ea7ccc",
  "#ffb248",
];

/** Axis/tooltip colors derived from the active theme. */
export function chartColors() {
  return {
    accent: cssVar("--color-accent", "#fca5a5"),
    text: cssVar("--color-text", "#111827"),
    muted: cssVar("--color-text-muted", "#6b7280"),
    border: cssVar("--color-border", "#d1d5db"),
    surface: cssVar("--color-surface", "#ffffff"),
  };
}
