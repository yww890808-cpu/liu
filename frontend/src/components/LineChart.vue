<template>
  <div class="viz-root">
    <div class="viz-head">
      <div class="viz-title">{{ title }}</div>
      <div v-if="series.length > 1" class="viz-legend">
        <span v-for="s in series" :key="s.name" class="viz-legend-item">
          <span class="viz-key" :style="{ background: s.color }" />{{ s.name }}
        </span>
      </div>
      <el-button link size="small" @click="showTable = !showTable">{{ showTable ? '图表' : '表格' }}</el-button>
    </div>

    <el-table v-if="showTable" :data="tableRows" size="small" max-height="260" border>
      <el-table-column prop="label" label="日期" width="110" />
      <el-table-column v-for="s in series" :key="s.name" :label="s.name">
        <template #default="{ row }">{{ fmt(row[s.name]) }}</template>
      </el-table-column>
    </el-table>

    <div v-else class="viz-plot" ref="plot" @pointermove="onMove" @pointerleave="hover = null">
      <svg :viewBox="`0 0 ${W} ${H}`" :width="W" :height="H" role="img" :aria-label="title">
        <!-- 网格与 Y 轴 -->
        <g v-for="t in ticks" :key="t">
          <line :x1="padL" :x2="W - padR" :y1="y(t)" :y2="y(t)" class="viz-grid" />
          <text :x="padL - 6" :y="y(t)" class="viz-axis" text-anchor="end" dominant-baseline="middle">{{ fmtAxis(t) }}</text>
        </g>
        <!-- X 轴标签：首、中、尾 -->
        <text v-for="i in xLabelIdx" :key="'x' + i" :x="x(i)" :y="H - 6" class="viz-axis"
              :text-anchor="i === 0 ? 'start' : i === labels.length - 1 ? 'end' : 'middle'">{{ labels[i] }}</text>
        <!-- 数据线（空值处断开） -->
        <path v-for="s in series" :key="s.name" :d="pathFor(s)" fill="none" :stroke="s.color" stroke-width="2"
              stroke-linejoin="round" stroke-linecap="round" />
        <!-- 只有一个数据点时画点 -->
        <template v-if="labels.length === 1">
          <circle v-for="s in series" :key="'p' + s.name" v-show="s.values[0] != null" :cx="x(0)" :cy="y(s.values[0] ?? 0)"
                  r="4" :fill="s.color" />
        </template>
        <!-- 悬停十字线 -->
        <g v-if="hover !== null">
          <line :x1="x(hover)" :x2="x(hover)" :y1="padT" :y2="H - padB" class="viz-crosshair" />
          <template v-for="s in series" :key="'h' + s.name">
            <circle v-if="s.values[hover] != null" :cx="x(hover)" :cy="y(s.values[hover])" r="4.5"
                    :fill="s.color" stroke="var(--viz-surface)" stroke-width="2" />
          </template>
        </g>
      </svg>
      <div v-if="hover !== null" class="viz-tip" :style="tipStyle">
        <div class="viz-tip-label">{{ labels[hover] }}</div>
        <div v-for="s in series" :key="'t' + s.name" class="viz-tip-row">
          <span class="viz-key" :style="{ background: s.color }" />
          <b>{{ fmt(s.values[hover]) }}</b><span class="viz-tip-name">{{ s.name }}</span>
        </div>
      </div>
      <div v-if="!labels.length" class="viz-empty">暂无数据</div>
    </div>
  </div>
</template>

<script setup>
// 轻量折线图：单一 Y 轴、2px 线、悬停十字线 + 提示、≥2 条线时显示图例、可切换表格
import { computed, ref } from 'vue'

const props = defineProps({
  title: String,
  labels: { type: Array, default: () => [] },     // X 轴（日期字符串）
  series: { type: Array, default: () => [] },     // [{ name, color, values: [number|null] }]
  format: { type: Function, default: (v) => (v == null ? '-' : Number(v).toLocaleString('zh-CN')) },
  height: { type: Number, default: 200 },
  zero: { type: Boolean, default: true },          // 量值（销量、销售额）从 0 起；价格等可设为 false 自动取范围
})

const W = 560
const H = computed(() => props.height)
const padL = 52, padR = 12, padT = 10, padB = 22
const showTable = ref(false)
const hover = ref(null)
const plot = ref(null)

const fmt = (v) => props.format(v)
const allValues = computed(() => props.series.flatMap((s) => s.values.filter((v) => v != null).map(Number)))

// “好看”的刻度，约 4 档
const ticks = computed(() => {
  const vals = allValues.value
  const max = Math.max(0, ...vals) || 1
  const min = props.zero || !vals.length ? 0 : Math.min(...vals)
  const span = max - min || max || 1
  const raw = span / 4
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw)
  const lo = Math.floor(min / step) * step
  return Array.from({ length: Math.ceil((max - lo) / step) + 1 }, (_, i) => +(lo + i * step).toFixed(6))
})
const bottom = computed(() => ticks.value[0] || 0)
const top = computed(() => ticks.value[ticks.value.length - 1] || 1)

const x = (i) => {
  const n = props.labels.length
  return n <= 1 ? (padL + W - padR) / 2 : padL + (i * (W - padL - padR)) / (n - 1)
}
const y = (v) => padT + (1 - (Number(v) - bottom.value) / (top.value - bottom.value || 1)) * (H.value - padT - padB)

function pathFor(s) {
  let d = ''
  let pen = false
  s.values.forEach((v, i) => {
    if (v == null) { pen = false; return }
    d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`
    pen = true
  })
  return d
}

const xLabelIdx = computed(() => {
  const n = props.labels.length
  if (!n) return []
  return [...new Set([0, Math.floor((n - 1) / 2), n - 1])]
})

const fmtAxis = (v) => (v >= 10000 ? `${+(v / 10000).toFixed(1)}万` : v.toLocaleString('zh-CN'))

function onMove(e) {
  const n = props.labels.length
  if (!n || !plot.value) return
  const rect = plot.value.querySelector('svg').getBoundingClientRect()
  const px = ((e.clientX - rect.left) / rect.width) * W
  const i = n === 1 ? 0 : Math.round(((px - padL) / (W - padL - padR)) * (n - 1))
  hover.value = Math.min(n - 1, Math.max(0, i))
}

const tipStyle = computed(() => {
  const pct = (x(hover.value) / W) * 100
  return pct > 60 ? { right: `${100 - pct + 2}%` } : { left: `${pct + 2}%` }
})

const tableRows = computed(() => props.labels.map((label, i) => ({
  label, ...Object.fromEntries(props.series.map((s) => [s.name, s.values[i]])),
})))
</script>

<style scoped>
.viz-root {
  --viz-surface: #ffffff;
  --viz-text: #0b0b0b;
  --viz-text-2: #52514e;
  --viz-muted: #8a8984;
  --viz-grid: #ebeae6;
  margin-bottom: 16px;
}
.viz-head { display: flex; align-items: center; gap: 12px; margin-bottom: 4px; }
.viz-title { font-weight: 600; font-size: 14px; color: var(--viz-text); }
.viz-legend { display: flex; gap: 12px; font-size: 12px; color: var(--viz-text-2); flex: 1; }
.viz-legend-item { display: inline-flex; align-items: center; gap: 4px; }
.viz-head .el-button { margin-left: auto; }
.viz-key { display: inline-block; width: 12px; height: 2px; border-radius: 1px; }
.viz-plot { position: relative; }
.viz-plot svg { width: 100%; height: auto; display: block; touch-action: pan-y; }
.viz-grid { stroke: var(--viz-grid); stroke-width: 1; }
.viz-axis { font-size: 11px; fill: var(--viz-muted); }
.viz-crosshair { stroke: var(--viz-text-2); stroke-width: 1; stroke-dasharray: 3 3; }
.viz-tip {
  position: absolute; top: 4px; pointer-events: none; background: var(--viz-surface);
  border: 1px solid #dcdfe6; border-radius: 6px; padding: 6px 8px; font-size: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, .08); color: var(--viz-text); white-space: nowrap;
}
.viz-tip-label { color: var(--viz-muted); margin-bottom: 2px; }
.viz-tip-row { display: flex; align-items: center; gap: 6px; }
.viz-tip-name { color: var(--viz-text-2); }
.viz-empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: var(--viz-muted); font-size: 13px; }
</style>
