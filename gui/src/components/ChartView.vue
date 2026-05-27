<template>
  <div class="chart-container" ref="chartRef">
    <div v-if="error" class="chart-error">
      <span class="chart-error-icon">⚠️</span>
      <span>图表渲染失败</span>
      <pre class="chart-error-json">{{ rawJson }}</pre>
    </div>
    <div v-else class="chart-canvas" ref="canvasRef"></div>
  </div>
</template>

<script setup lang="ts">
import { ref, shallowRef, watch, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart, BarChart, PieChart } from 'echarts/charts'
import { TitleComponent, TooltipComponent, LegendComponent, GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([LineChart, BarChart, PieChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent, CanvasRenderer])

const props = defineProps<{
  option: Record<string, unknown>
}>()

const chartRef = ref<HTMLDivElement>()
const canvasRef = ref<HTMLDivElement>()
const error = ref(false)
const rawJson = ref('')

let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

function initChart() {
  if (!canvasRef.value) return
  try {
    chart = echarts.init(canvasRef.value, 'dark')
    const opt = props.option?.option || props.option
    chart.setOption(opt, { notMerge: true })
    error.value = false
  } catch (e) {
    console.error('Chart init error:', e)
    error.value = true
    rawJson.value = JSON.stringify(props.option, null, 2)
  }
}

onMounted(() => {
  nextTick(() => {
    initChart()
    if (canvasRef.value) {
      resizeObserver = new ResizeObserver(() => {
        chart?.resize()
      })
      resizeObserver.observe(canvasRef.value)
    }
  })
})

onUnmounted(() => {
  resizeObserver?.disconnect()
  chart?.dispose()
  chart = null
})

watch(
  () => props.option,
  (newVal) => {
    if (!newVal) return
    if (chart) {
      try {
        const opt = newVal.option || newVal
        chart.setOption(opt, { notMerge: true })
        error.value = false
      } catch (e) {
        console.error('Chart update error:', e)
      }
    } else {
      nextTick(initChart)
    }
  },
  { flush: 'post' }
)
</script>

<style scoped>
.chart-container {
  margin: 8px 0;
  border-radius: 8px;
  border: 1px solid rgba(148, 163, 184, 0.15);
  overflow: hidden;
}
.chart-canvas {
  width: 100%;
  min-height: 300px;
  height: auto;
}
.chart-error {
  padding: 16px;
  color: var(--oaa-warning, #f59e0b);
  font-size: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.chart-error-icon {
  margin-right: 6px;
}
.chart-error-json {
  font-size: 12px;
  color: var(--oaa-text-secondary, #94a3b8);
  max-height: 200px;
  overflow: auto;
  background: rgba(0, 0, 0, 0.2);
  padding: 8px;
  border-radius: 4px;
}
</style>
