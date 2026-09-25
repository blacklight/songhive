<script setup lang="ts">
import AppInput from "@/components/ui/AppInput.vue";

const from = defineModel<string>("from", { required: true });
const to = defineModel<string>("to", { required: true });

defineProps<{
  fromLabel: string;
  toLabel: string;
}>();
</script>

<template>
  <div class="stats-range-picker">
    <AppInput
      v-model="from"
      type="date"
      :label="fromLabel"
      class="stats-range-picker__field"
    />
    <AppInput
      v-model="to"
      type="date"
      :label="toLabel"
      class="stats-range-picker__field"
    />
  </div>
</template>

<style scoped>
.stats-range-picker {
  display: flex;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.stats-range-picker__field {
  min-width: 10rem;
}
</style>
