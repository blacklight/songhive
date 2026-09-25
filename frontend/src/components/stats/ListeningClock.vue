<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { EChartsCoreOption } from "echarts/core";
import type { StatsClock } from "@/api/stats";
import { VChart, chartColors } from "./echarts";

const props = defineProps<{ hours: StatsClock }>();

const { t } = useI18n();

const option = computed<EChartsCoreOption>(() => {
  const colors = chartColors();
  const max = Math.max(1, ...props.hours.map((entry) => entry.count));
  return {
    polar: { radius: ["32%", "88%"], center: ["50%", "50%"] },
    angleAxis: {
      type: "category",
      data: props.hours.map((entry) => String(entry.hour).padStart(2, "0")),
      // 00 at the top, hours advancing clockwise like a clock face.
      startAngle: 90,
      clockwise: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { show: false },
      splitLine: { show: false },
    },
    radiusAxis: { max, show: false },
    tooltip: {
      trigger: "item",
      formatter: (params: { name: string; value: number }) =>
        `${params.name}:00 — ${params.value} ${t("pages.stats.playsCount")}`,
    },
    series: [
      {
        type: "bar",
        coordinateSystem: "polar",
        data: props.hours.map((entry) => entry.count),
        roundCap: true,
        itemStyle: { color: colors.accent },
      },
    ],
  };
});
</script>

<template>
  <div class="listening-clock">
    <VChart class="listening-clock__chart" :option="option" autoresize />
    <div class="listening-clock__labels" aria-hidden="true">
      <span class="listening-clock__label listening-clock__label--n">00</span>
      <span class="listening-clock__label listening-clock__label--e">06</span>
      <span class="listening-clock__label listening-clock__label--s">12</span>
      <span class="listening-clock__label listening-clock__label--w">18</span>
    </div>
  </div>
</template>

<style scoped>
.listening-clock {
  position: relative;
  width: 100%;
}

.listening-clock__chart {
  width: 100%;
  height: 18rem;
}

.listening-clock__labels {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.listening-clock__label {
  position: absolute;
  transform: translate(-50%, -50%);
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-text-muted);
}

.listening-clock__label--n {
  top: 36%;
  left: 50%;
}

.listening-clock__label--e {
  top: 50%;
  left: 64%;
}

.listening-clock__label--s {
  top: 64%;
  left: 50%;
}

.listening-clock__label--w {
  top: 50%;
  left: 36%;
}
</style>
