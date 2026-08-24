<script setup lang="ts">
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

export interface Column {
  key: string;
  label: string;
  align?: "left" | "right" | "center";
}

export interface Props {
  columns: Column[];
  rows: Record<string, unknown>[];
  rowKey?: (row: Record<string, unknown>, index: number) => string;
  loading?: boolean;
  emptyLabel?: string;
}

const props = withDefaults(defineProps<Props>(), {
  emptyLabel: "No data",
});

function rowId(row: Record<string, unknown>, index: number): string {
  return props.rowKey ? props.rowKey(row, index) : `row-${index}`;
}

function alignClass(align?: string): string {
  return align ? `app-table__cell--${align}` : "app-table__cell--left";
}
</script>

<template>
  <table class="app-table" :aria-busy="props.loading ? 'true' : 'false'">
    <thead>
      <tr>
        <th
          v-for="column in props.columns"
          :key="column.key"
          :class="alignClass(column.align)"
        >
          <slot :name="`column-${column.key}`" :column="column">
            {{ column.label }}
          </slot>
        </th>
      </tr>
    </thead>
    <tbody>
      <tr v-if="props.loading">
        <td :colspan="props.columns.length">
          <SkeletonLoader v-for="i in 3" :key="i" variant="list-row" />
        </td>
      </tr>
      <tr v-else-if="props.rows.length === 0">
        <td :colspan="props.columns.length" class="app-table__empty">
          {{ props.emptyLabel }}
        </td>
      </tr>
      <tr v-for="(row, index) in props.rows" v-else :key="rowId(row, index)">
        <td
          v-for="column in props.columns"
          :key="column.key"
          :class="['app-table__cell', alignClass(column.align)]"
        >
          <slot :name="`row-${column.key}`" :row="row" :value="row[column.key]">
            {{ row[column.key] }}
          </slot>
        </td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.app-table {
  width: 100%;
  border-collapse: collapse;
  color: var(--color-text);
}

.app-table th,
.app-table td {
  padding: var(--space-3);
  text-align: left;
  border-bottom: 1px solid var(--color-border);
}

.app-table th {
  font-weight: 600;
  color: var(--color-text-muted);
}

.app-table__cell--right {
  text-align: right;
}

.app-table__cell--center {
  text-align: center;
}

.app-table__empty {
  text-align: center;
  padding: var(--space-6);
  color: var(--color-text-muted);
}

.app-table[aria-busy="true"] td {
  border-bottom: none;
}
</style>
