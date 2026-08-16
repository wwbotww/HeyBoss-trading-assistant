<script setup lang="ts">
import type { ReportTable } from '../api/types'
import { formatReportValue } from '../utils/report'
import DataTable from './DataTable.vue'

defineProps<{
  table: ReportTable
  caption: string
}>()
</script>

<template>
  <DataTable
    :caption="caption"
    :min-width="`${String(Math.max(720, table.columns.length * 150))}px`"
  >
    <thead>
      <tr>
        <th v-for="column in table.columns" :key="column">{{ column }}</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="(row, rowIndex) in table.rows" :key="rowIndex">
        <td v-for="column in table.columns" :key="column" class="tabular">
          {{ formatReportValue(row[column] ?? null, column) }}
        </td>
      </tr>
    </tbody>
  </DataTable>
</template>
