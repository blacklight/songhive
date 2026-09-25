<script setup lang="ts">
import { computed } from "vue";
import type { EChartsCoreOption } from "echarts/core";
import { VChart, chartColors } from "./echarts";

const props = defineProps<{
  buckets: { bucket: string; count: number }[];
}>();

const option = computed<EChartsCoreOption>(() => {
  const colors = chartColors();
  return {
    grid: { top: 16, right: 16, bottom: 32, left: 48 },
    tooltip: { trigger: "axis" },
    xAxis: {
      type: "category",
      data: props.buckets.map((b) => b.bucket),
      axisLabel: { color: colors.muted },
      axisLine: { lineStyle: { color: colors.border } },
    },
    yAxis: {
      type: "value",
      minInterval: 1,
      axisLabel: { color: colors.muted },
      splitLine: { lineStyle: { color: colors.border } },
    },
    series: [
      {
        type: "bar",
        data: props.buckets.map((b) => b.count),
        itemStyle: { color: colors.accent, borderRadius: [3, 3, 0, 0] },
      },
    ],
  };
});
</script>

<template>
  <VChart class="stats-histogram" :option="option" autoresize />
</template>

<style scoped>
.stats-histogram {
  width: 100%;
  height: 16rem;
}
</style>
