<script setup lang="ts">
import { computed } from "vue";
import type { EChartsCoreOption } from "echarts/core";
import type { StatsGenreBuckets } from "@/api/stats";
import { CHART_PALETTE, VChart, chartColors } from "./echarts";

const props = withDefaults(
  defineProps<{
    buckets: StatsGenreBuckets;
    /** How many genres get their own series (rest are folded into "Other"). */
    top?: number;
  }>(),
  { top: 8 },
);

const OTHER = "__other__";

const topGenres = computed<string[]>(() => {
  const totals = new Map<string, number>();
  for (const bucket of props.buckets) {
    for (const genre of bucket.genres) {
      totals.set(genre.name, (totals.get(genre.name) ?? 0) + genre.count);
    }
  }
  return [...totals.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, props.top)
    .map(([name]) => name);
});

const option = computed<EChartsCoreOption>(() => {
  const colors = chartColors();
  const names = topGenres.value;
  const series = names.map((name, index) => ({
    name,
    type: "bar" as const,
    stack: "genres",
    emphasis: { focus: "series" as const },
    itemStyle: { color: CHART_PALETTE[index % CHART_PALETTE.length] },
    data: props.buckets.map(
      (bucket) =>
        bucket.genres.find((genre) => genre.name === name)?.count ?? 0,
    ),
  }));

  const seriesNames = new Set(names);
  const otherData = props.buckets.map((bucket) =>
    bucket.genres
      .filter((genre) => !seriesNames.has(genre.name))
      .reduce((sum, genre) => sum + genre.count, 0),
  );
  if (otherData.some((count) => count > 0)) {
    series.push({
      name: OTHER,
      type: "bar" as const,
      stack: "genres",
      emphasis: { focus: "series" as const },
      itemStyle: { color: colors.border },
      data: otherData,
    });
  }

  return {
    color: CHART_PALETTE,
    grid: { top: 16, right: 16, bottom: 56, left: 48 },
    tooltip: { trigger: "axis" },
    legend: {
      type: "scroll",
      bottom: 0,
      textStyle: { color: colors.muted },
    },
    xAxis: {
      type: "category",
      data: props.buckets.map((bucket) => bucket.bucket),
      axisLabel: { color: colors.muted },
      axisLine: { lineStyle: { color: colors.border } },
    },
    yAxis: {
      type: "value",
      minInterval: 1,
      axisLabel: { color: colors.muted },
      splitLine: { lineStyle: { color: colors.border } },
    },
    series,
  };
});
</script>

<template>
  <VChart class="genre-timeline" :option="option" autoresize />
</template>

<style scoped>
.genre-timeline {
  width: 100%;
  height: 18rem;
}
</style>
