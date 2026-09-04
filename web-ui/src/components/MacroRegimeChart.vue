<script setup lang="ts">
import { LineChart, ScatterChart } from 'echarts/charts'
import {
  AriaComponent,
  GridComponent,
  MarkAreaComponent,
  TooltipComponent,
} from 'echarts/components'
import { init, use, type ECharts, type EChartsCoreOption } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { MacroRegime } from '../api/types'

const props = defineProps<{
  macro: MacroRegime
}>()

use([
  LineChart,
  ScatterChart,
  GridComponent,
  TooltipComponent,
  MarkAreaComponent,
  AriaComponent,
  CanvasRenderer,
])

const chartElement = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null
let observer: ResizeObserver | null = null

const accessibleSummary = computed(() => {
  const current = props.macro.current
  if (!current) {
    return '宏观象限图没有可绘制的当前点。'
  }
  return `宏观象限图，共 ${String(props.macro.trajectory.length)} 个轨迹点。当前为${current.regime_label}，实际利率压力 Z 值 ${current.real_rate_pressure_z.toFixed(2)}，风险偏好得分 ${current.risk_appetite_score.toFixed(2)}。`
})

function option(): EChartsCoreOption {
  const band = props.macro.neutral_band ?? 0.35
  const trajectory = props.macro.trajectory.map((point) => ({
    value: [point.real_rate_pressure_z, point.risk_appetite_score],
    day: point.day,
    regimeLabel: point.regime_label,
  }))
  const current = props.macro.current
  return {
    animationDuration: 380,
    aria: { enabled: true, decal: { show: false } },
    grid: { left: 54, right: 24, top: 28, bottom: 48 },
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: (raw: unknown) => {
        if (typeof raw !== 'object' || raw === null) {
          return ''
        }
        const parameter = raw as {
          data?: { day?: string; regimeLabel?: string; value?: unknown[] }
        }
        const data = parameter.data
        const values = data?.value
        if (!data || !Array.isArray(values) || values.length < 2) {
          return ''
        }
        return `${data.day ?? ''}<br/>${data.regimeLabel ?? ''}<br/>实际利率压力 ${Number(values[0]).toFixed(2)}<br/>风险偏好 ${Number(values[1]).toFixed(2)}`
      },
    },
    xAxis: {
      type: 'value',
      min: -3,
      max: 3,
      name: '实际利率压力 Z →',
      nameLocation: 'middle',
      nameGap: 30,
      nameTextStyle: { color: '#8e919a', fontSize: 10 },
      axisLabel: { color: '#8e919a', fontSize: 10 },
      axisLine: { lineStyle: { color: '#d7d8de' } },
      splitLine: { lineStyle: { color: '#eceef2' } },
    },
    yAxis: {
      type: 'value',
      min: -3,
      max: 3,
      name: '风险偏好 →',
      nameLocation: 'middle',
      nameGap: 38,
      nameTextStyle: { color: '#8e919a', fontSize: 10 },
      axisLabel: { color: '#8e919a', fontSize: 10 },
      axisLine: { lineStyle: { color: '#d7d8de' } },
      splitLine: { lineStyle: { color: '#eceef2' } },
    },
    series: [
      {
        name: '最近轨迹',
        type: 'line',
        data: trajectory,
        showSymbol: false,
        lineStyle: { color: '#8a82a8', width: 1.8, opacity: 0.72 },
        markArea: {
          silent: true,
          label: {
            show: true,
            color: '#7b7e87',
            fontSize: 10,
            position: 'insideTop',
          },
          data: [
            [
              {
                name: '增长担忧',
                xAxis: -3,
                yAxis: -3,
                itemStyle: { color: 'rgba(180, 58, 67, 0.035)' },
              },
              { xAxis: -band, yAxis: -band },
            ],
            [
              {
                name: '宽松型 Risk-on',
                xAxis: -3,
                yAxis: band,
                itemStyle: { color: 'rgba(52, 104, 255, 0.045)' },
              },
              { xAxis: -band, yAxis: 3 },
            ],
            [
              {
                name: '紧缩冲击',
                xAxis: band,
                yAxis: -3,
                itemStyle: { color: 'rgba(180, 58, 67, 0.055)' },
              },
              { xAxis: 3, yAxis: -band },
            ],
            [
              {
                name: '增长 / 再通胀',
                xAxis: band,
                yAxis: band,
                itemStyle: { color: 'rgba(20, 122, 84, 0.04)' },
              },
              { xAxis: 3, yAxis: 3 },
            ],
            [
              {
                name: '过渡区',
                xAxis: -band,
                yAxis: -3,
                itemStyle: { color: 'rgba(115, 87, 246, 0.035)' },
              },
              { xAxis: band, yAxis: 3 },
            ],
            [
              {
                xAxis: -3,
                yAxis: -band,
                itemStyle: { color: 'rgba(115, 87, 246, 0.035)' },
              },
              { xAxis: 3, yAxis: band },
            ],
          ],
        },
      },
      {
        name: '当前',
        type: 'scatter',
        data: current
          ? [
              {
                value: [current.real_rate_pressure_z, current.risk_appetite_score],
                day: current.day,
                regimeLabel: current.regime_label,
              },
            ]
          : [],
        symbolSize: 15,
        itemStyle: {
          color: '#7357f6',
          borderColor: '#ffffff',
          borderWidth: 3,
          shadowBlur: 12,
          shadowColor: 'rgba(115, 87, 246, 0.35)',
        },
        z: 4,
      },
    ],
  }
}

async function renderChart(): Promise<void> {
  await nextTick()
  if (!chartElement.value || !props.macro.current) {
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
  () => props.macro,
  () => void renderChart(),
  { deep: true },
)

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <div class="macro-chart-shell">
    <p class="sr-only">{{ accessibleSummary }}</p>
    <div
      ref="chartElement"
      class="macro-chart-canvas"
      role="img"
      :aria-label="accessibleSummary"
    ></div>
    <div class="macro-chart-legend" aria-hidden="true">
      <span><i class="trajectory"></i>最近 {{ macro.trajectory.length }} 个有效点</span>
      <span><i class="current"></i>当前点</span>
      <span>中性带 ±{{ macro.neutral_band?.toFixed(2) ?? '—' }}</span>
    </div>
  </div>
</template>

<style scoped>
.macro-chart-shell {
  min-width: 0;
}

.macro-chart-canvas {
  width: 100%;
  height: 390px;
}

.macro-chart-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 18px;
  padding: 6px 24px 20px;
  color: var(--color-text-faint);
  font-size: 0.64rem;
}

.macro-chart-legend span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.macro-chart-legend i {
  display: inline-block;
}

.macro-chart-legend .trajectory {
  width: 16px;
  height: 2px;
  background: #8a82a8;
}

.macro-chart-legend .current {
  width: 8px;
  height: 8px;
  background: var(--color-brand-violet);
  border-radius: 50%;
  box-shadow: 0 0 0 3px rgb(115 87 246 / 14%);
}

@media (max-width: 620px) {
  .macro-chart-canvas {
    height: 330px;
  }

  .macro-chart-legend {
    padding-inline: 18px;
  }
}
</style>
