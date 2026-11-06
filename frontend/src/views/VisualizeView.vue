<script setup>
import * as echarts from 'echarts'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getTask, listTasks, visualizeTask } from '../api'

const tasks = ref([])
const taskId = ref('')
const detail = ref(null)
const hint = ref('')
const loading = ref(false)
const generating = ref(false)
const error = ref('')
const plan = ref(null)
const chartEls = ref([])
const message = ref('')

const selectedTask = computed(() => tasks.value.find((t) => t.id === taskId.value) || null)
const items = computed(() => {
  try {
    const data = JSON.parse(detail.value?.result_json || '[]')
    return Array.isArray(data) ? data : []
  } catch {
    return []
  }
})
const taskTimings = computed(() => {
  try {
    return JSON.parse(detail.value?.timings_json || '{}')
  } catch {
    return {}
  }
})

let chartInstances = []

async function loadTasks() {
  loading.value = true
  error.value = ''
  try {
    const res = await listTasks('')
    tasks.value = (res.items || []).filter((t) => t.item_count > 0)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

watch(taskId, async (id) => {
  plan.value = null
  message.value = ''
  error.value = ''
  detail.value = null
  disposeCharts()
  if (!id) return
  try {
    detail.value = await getTask(id)
  } catch (e) {
    error.value = e.message
  }
})

function setChartEl(el, index) {
  chartEls.value[index] = el
}

function disposeCharts() {
  chartInstances.forEach((c) => c && c.dispose())
  chartInstances = []
}

function toNumber(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string') {
    const n = parseFloat(value.replace(/,/g, '').match(/-?\d+(?:\.\d+)?/)?.[0] ?? '')
    return Number.isFinite(n) ? n : null
  }
  return null
}

function cellText(value) {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

// 类别名过长时改用序号标签，完整内容放到 tooltip，避免坐标轴互相挤压
const LONG_LABEL = 12
const MAX_CATEGORIES = 12

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => {
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
    return map[c]
  })
}

function numberText(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return cellText(value)
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function truncate(text, max = 10) {
  const s = cellText(text)
  return s.length > max ? `${s.slice(0, max)}…` : s
}

function tooltipHtml(fullName, value, seriesName = '') {
  const head = `<div style="max-width:260px;white-space:normal;line-height:1.5">${escapeHtml(fullName)}</div>`
  return seriesName ? `${head}${escapeHtml(seriesName)}：${numberText(value)}` : `${head}${numberText(value)}`
}

function groupValues(chart) {
  const groups = new Map()
  for (const item of items.value) {
    const key = cellText(item[chart.x])
    if (!groups.has(key)) groups.set(key, { count: 0, nums: [] })
    const g = groups.get(key)
    g.count += 1
    if (chart.y) {
      const n = toNumber(item[chart.y])
      if (n !== null) g.nums.push(n)
    }
  }
  const aggregate = chart.aggregate || (chart.y ? 'sum' : 'count')
  return [...groups.entries()].map(([name, g]) => {
    if (aggregate === 'count' || !chart.y) return [name, g.count]
    if (!g.nums.length) return [name, 0]
    const total = g.nums.reduce((a, b) => a + b, 0)
    const value = aggregate === 'avg' ? total / g.nums.length : total
    return [name, Math.round(value * 100) / 100]
  })
}

function buildOption(chart) {
  const title = {
    text: truncate(chart.title, 30),
    left: 'center',
    top: 6,
    textStyle: { fontSize: 14, fontWeight: 600, color: '#1f2937' },
  }

  if (chart.type === 'scatter') {
    const points = items.value
      .map((it) => [toNumber(it[chart.x]), chart.y ? toNumber(it[chart.y]) : null])
      .filter((p) => p[0] !== null && p[1] !== null)
    return {
      title,
      tooltip: { trigger: 'item' },
      grid: { left: 56, right: 28, top: 56, bottom: 48 },
      xAxis: { type: 'value', name: chart.x, nameLocation: 'middle', nameGap: 28 },
      yAxis: { type: 'value', name: chart.y || '' },
      series: [{ type: 'scatter', name: chart.y || chart.x, data: points, symbolSize: 10 }],
    }
  }

  const values = groupValues(chart)

  if (chart.type === 'pie') {
    const fullNames = values.map((v) => v[0])
    const needShort = fullNames.some((n) => n.length > LONG_LABEL)
    return {
      title,
      tooltip: {
        trigger: 'item',
        formatter: (p) => tooltipHtml(p.data.fullName || p.name, p.value),
      },
      legend: { type: 'scroll', bottom: 0, formatter: (name) => truncate(name, 12) },
      series: [
        {
          type: 'pie',
          radius: ['38%', '62%'],
          center: ['50%', '52%'],
          data: values.map(([name, value]) => ({ name, value, fullName: name })),
          label: {
            formatter: (p) =>
              needShort
                ? `#${p.dataIndex + 1}: ${numberText(p.value)}`
                : `${truncate(p.name, 8)}: ${numberText(p.value)}`,
          },
        },
      ],
    }
  }

  if (chart.type === 'bar') {
    values.sort((a, b) => b[1] - a[1])
  }

  const fullNames = values.map((v) => v[0])
  const needShort = values.length > MAX_CATEGORIES || fullNames.some((n) => n.length > LONG_LABEL)
  const labels = needShort ? fullNames.map((_, i) => `#${i + 1}`) : fullNames
  const aggregate = chart.aggregate || (chart.y ? 'sum' : 'count')
  const useZoom = values.length > MAX_CATEGORIES
  return {
    title,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: chart.type === 'line' ? 'line' : 'shadow' },
      formatter: (params) => {
        const p = Array.isArray(params) ? params[0] : params
        if (!p) return ''
        return tooltipHtml(fullNames[p.dataIndex] ?? p.name, p.value, p.seriesName)
      },
    },
    grid: { left: 56, right: 28, top: 56, bottom: useZoom ? 64 : 48 },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { interval: 0, rotate: 0 },
    },
    yAxis: { type: 'value' },
    dataZoom: useZoom
      ? [{ type: 'inside' }, { type: 'slider', height: 12, bottom: 10 }]
      : undefined,
    series: [
      {
        type: chart.type,
        name: chart.y ? `${chart.y}（${aggregate}）` : '数量',
        data: values.map((v) => v[1]),
        barMaxWidth: 40,
        label: {
          show: values.length <= MAX_CATEGORIES,
          position: 'top',
          formatter: (p) => numberText(p.value),
        },
      },
    ],
  }
}

async function generate() {
  if (!taskId.value) {
    error.value = '请先选择一份采集数据。'
    return
  }
  error.value = ''
  message.value = ''
  generating.value = true
  try {
    plan.value = await visualizeTask(taskId.value, hint.value.trim())
    await nextTick()
    renderCharts()
  } catch (e) {
    error.value = e.message
  } finally {
    generating.value = false
  }
}

function renderCharts() {
  disposeCharts()
  if (!plan.value) return
  chartInstances = (plan.value.charts || []).map((chart, i) => {
    const el = chartEls.value[i]
    if (!el) return null
    const instance = echarts.init(el)
    instance.setOption(buildOption(chart))
    return instance
  })
}

function resizeCharts() {
  chartInstances.forEach((c) => c && c.resize())
}

function formatMs(value) {
  if (!value) return '—'
  return `${(Number(value) / 1000).toFixed(2)} 秒`
}

onMounted(() => {
  loadTasks()
  window.addEventListener('resize', resizeCharts)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeCharts)
  disposeCharts()
})
</script>

<template>
  <section class="page">
    <div class="card">
      <div class="grid-2">
        <label class="field">
          <span>采集数据</span>
          <select v-model="taskId" :disabled="loading">
            <option value="">{{ loading ? '加载中……' : '选择一份数据' }}</option>
            <option v-for="t in tasks" :key="t.id" :value="t.id">
              #{{ t.id.slice(0, 8) }} · {{ t.task_desc }}（{{ t.item_count }} 条）
            </option>
          </select>
        </label>
        <label class="field">
          <span>可视化方向（可选）</span>
          <input v-model="hint" placeholder="例：重点对比价格分布，用柱状图" :disabled="generating" />
        </label>
      </div>

      <div v-if="error" class="alert error">{{ error }}</div>
      <div v-if="message" class="alert info">{{ message }}</div>

      <div class="actions">
        <button class="btn primary" :disabled="!taskId || generating" @click="generate">
          {{ generating ? '智能体正在设计图表……' : '生成可视化' }}
        </button>
        <span v-if="selectedTask" class="hint">共 {{ items.length }} 条数据</span>
      </div>
    </div>

    <div v-if="detail" class="card viz-meta">
      <div class="result-head">
        <div class="result-title">
          <h2>{{ plan ? '可视化结果' : '数据预览' }}</h2>
          <p class="sub">{{ detail.task_desc }}</p>
        </div>
        <span class="badge badge-pending">{{ items.length }} 条</span>
      </div>

      <p v-if="plan" class="viz-summary">{{ plan.summary }}</p>

      <div v-if="plan" class="timing-strip">
        <span class="chip">智能体设计 {{ formatMs(plan.elapsed_ms) }}</span>
        <span v-if="taskTimings.total_ms" class="chip">采集总耗时 {{ formatMs(taskTimings.total_ms) }}</span>
        <span v-if="taskTimings.llm_calls" class="chip">LLM 调用 {{ taskTimings.llm_calls }} 次</span>
      </div>

      <div v-if="plan" class="chart-grid">
        <div v-for="(chart, i) in plan.charts" :key="i" class="chart-card" :title="chart.title">
          <div class="chart-box" :ref="(el) => setChartEl(el, i)" />
          <p class="chart-reason">{{ chart.type }} · x={{ chart.x }}<template v-if="chart.y"> · y={{ chart.y }}</template> · {{ chart.aggregate }}</p>
        </div>
      </div>

      <p v-else class="empty">
        选择数据后点击「生成可视化」，智能体将自动挑选图表类型与字段映射。
      </p>
    </div>

    <div v-else-if="!loading" class="card">
      <p class="empty">暂无可用于可视化的数据，请先在首页或定时任务中完成一次采集。</p>
    </div>
  </section>
</template>
