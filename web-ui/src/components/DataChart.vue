<script setup lang="ts">
import { BarChart, LineChart } from 'echarts/charts'
import { AriaComponent, GridComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts, type EChartsCoreOption } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

export interface DataChartPoint {
  label: string
  value: number
  emphasis?: boolean
}

const props = withDefaults(
  defineProps<{
    title: string
    points: readonly DataChartPoint[]
    kind: 'line' | 'bar'
    valueKind?: 'currency' | 'percent' | 'number'
    height?: number
  }>(),
  {
    valueKind: 'number',
    height: 300,
  },
)

use([LineChart, BarChart, GridComponent, TooltipComponent, AriaComponent, CanvasRenderer])

const chartElement = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null
let observer: ResizeObserver | null = null

function formatValue(value: number): string {
  if (props.valueKind === 'currency') {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: 2,
    }).format(value)
  }
  if (props.valueKind === 'percent') {
    return new Intl.NumberFormat('zh-CN', {
      style: 'percent',
      maximumFractionDigits: 2,
    }).format(value)
  }
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 }).format(value)
}

function compactValue(value: number): string {
  if (props.valueKind === 'currency') {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      notation: 'compact',
      maximumFractionDigits: 1,
    }).format(value)
  }
  if (props.valueKind === 'percent') {
    return `${String(Math.round(value * 100))}%`
  }
  return new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: 2,
  }).format(value)
}

const accessibleSummary = computed(() => {
  if (props.points.length === 0) {
    return `${props.title}，没有可绘制数据。`
  }
  const first = props.points[0]
  const last = props.points.at(-1)
  if (!first || !last) {
    return props.title
  }
  return `${props.title}，共 ${String(props.points.length)} 个数据点，从 ${first.label} 的 ${formatValue(first.value)} 到 ${last.label} 的 ${formatValue(last.value)}。`
})

function option(): EChartsCoreOption {
  const labels = props.points.map((point) => point.label)
  const values = props.points.map((point) => ({
    value: point.value,
    itemStyle: {
      color: point.emphasis ? '#7357f6' : props.kind === 'bar' ? '#b7bac3' : '#3468ff',
      ...(props.kind === 'bar' ? { borderRadius: [0, 6, 6, 0] } : {}),
    },
  }))
  const common = {
    animationDuration: 360,
    aria: { enabled: true, decal: { show: false } },
    grid:
      props.kind === 'bar'
        ? { left: 14, right: 26, top: 12, bottom: 12, containLabel: true }
        : { left: 12, right: 16, top: 18, bottom: 20, containLabel: true },
    tooltip: {
      trigger: props.kind === 'line' ? 'axis' : 'item',
      confine: true,
      valueFormatter: (value: unknown) => {
        if (typeof value === 'number') {
          return formatValue(value)
        }
        return typeof value === 'string' || typeof value === 'boolean' ? String(value) : ''
      },
    },
  }
  if (props.kind === 'bar') {
    return {
      ...common,
      xAxis: {
        type: 'value',
        axisLabel: { color: '#8e919a', fontSize: 10, formatter: compactValue },
        splitLine: { lineStyle: { color: '#eceef2' } },
      },
      yAxis: {
        type: 'category',
        data: labels,
        inverse: true,
        axisLabel: { color: '#5f626c', fontSize: 11 },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      series: [{ type: 'bar', data: values, barMaxWidth: 18 }],
    }
  }
  return {
    ...common,
    xAxis: {
      type: 'category',
      data: labels,
      boundaryGap: false,
      axisLabel: { color: '#8e919a', fontSize: 10, hideOverlap: true },
      axisLine: { lineStyle: { color: '#dfe1e6' } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { color: '#8e919a', fontSize: 10, formatter: compactValue },
      splitLine: { lineStyle: { color: '#eceef2' } },
    },
    series: [
      {
        type: 'line',
        data: values,
        showSymbol: props.points.length < 18,
        symbolSize: 6,
        lineStyle: { color: '#3468ff', width: 2.5 },
        itemStyle: { color: '#7357f6' },
        areaStyle: { color: 'rgba(52, 104, 255, 0.08)' },
        smooth: false,
      },
    ],
  }
}

async function renderChart(): Promise<void> {
  await nextTick()
  if (!chartElement.value || props.points.length === 0) {
    chart?.clear()
    return
  }
  chart ??= init(chartElement.value, undefined, { renderer: 'canvas' })
  chart.setOption(option(), true)
}

onMounted(() => {
  void renderChart()
  if (chartElement.value && typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(chartElement.value)
  }
})

watch(
  () => [props.points, props.kind, props.valueKind] as const,
  () => void renderChart(),
  { deep: true },
)

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <div class="chart-shell">
    <p class="sr-only">{{ accessibleSummary }}</p>
    <div
      ref="chartElement"
      class="chart-canvas"
      :style="{ height: `${String(height)}px` }"
      role="img"
      :aria-label="accessibleSummary"
    ></div>
  </div>
</template>

<style scoped>
.chart-shell,
.chart-canvas {
  width: 100%;
  min-width: 0;
}
</style>
