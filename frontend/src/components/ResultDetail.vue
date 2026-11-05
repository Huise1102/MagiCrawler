<script setup>
import { computed, ref } from 'vue'
import { exportUrl } from '../api'
import ResultTable from './ResultTable.vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps({
  task: { type: Object, required: true },
})

const FORMATS = { json: 'JSON', csv: 'CSV', xlsx: 'Excel', md: 'Markdown', docx: 'Word' }
const showTrajectory = ref(false)

const running = computed(() => ['pending', 'running'].includes(props.task.status))
const trajectory = computed(() => props.task.trajectory || [])
const items = computed(() => {
  try {
    const data = JSON.parse(props.task.result_json || '[]')
    return Array.isArray(data) ? data : []
  } catch {
    return []
  }
})
const missing = computed(() =>
  items.value.reduce(
    (sum, item) =>
      sum + Object.values(item || {}).filter((v) => v === null || v === undefined || v === '').length,
    0,
  ),
)
const timings = computed(() => {
  try {
    return JSON.parse(props.task.timings_json || '{}')
  } catch {
    return {}
  }
})
const hasTimings = computed(() => Object.keys(timings.value).length > 0)

function formatMs(value) {
  const n = Number(value)
  if (!Number.isFinite(n) || n <= 0) return '—'
  return n >= 1000 ? `${(n / 1000).toFixed(2)} 秒` : `${Math.round(n)} 毫秒`
}

function formatArgs(args) {
  try {
    return JSON.stringify(args || {})
  } catch {
    return ''
  }
}
</script>

<template>
  <div class="card result-card">
    <div class="result-head">
      <div class="result-title">
        <h2>采集结果</h2>
        <p class="sub">{{ task.task_desc }}</p>
      </div>
      <StatusBadge :status="task.status" />
    </div>

    <div class="metrics">
      <div class="metric">
        <span class="metric-label">条目数</span>
        <strong>{{ items.length }}</strong>
      </div>
      <div class="metric">
        <span class="metric-label">缺失字段值</span>
        <strong>{{ missing }}</strong>
      </div>
      <div class="metric">
        <span class="metric-label">动作步数</span>
        <strong>{{ trajectory.length }}</strong>
      </div>
    </div>

    <div v-if="running" class="alert info">
      <span class="spinner" />
      智能体正在执行，结果会自动刷新……
    </div>
    <div v-if="task.note" class="alert info">{{ task.note }}</div>
    <div v-if="task.error" class="alert error">{{ task.error }}</div>

    <div v-if="hasTimings" class="timing-strip">
      <span v-if="timings.total_ms" class="chip strong">总耗时 {{ formatMs(timings.total_ms) }}</span>
      <span v-if="timings.browser_ms" class="chip">浏览器启动 {{ formatMs(timings.browser_ms) }}</span>
      <span v-if="timings.navigate_ms" class="chip">首次导航 {{ formatMs(timings.navigate_ms) }}</span>
      <span v-if="timings.decision_ms" class="chip">LLM 决策 {{ formatMs(timings.decision_ms) }}</span>
      <span v-if="timings.extract_ms" class="chip">数据提取 {{ formatMs(timings.extract_ms) }}</span>
      <span v-if="timings.action_ms" class="chip">页面动作 {{ formatMs(timings.action_ms) }}</span>
      <span v-if="timings.llm_calls" class="chip">LLM 调用 {{ timings.llm_calls }} 次</span>
      <span v-if="timings.steps" class="chip">{{ timings.steps }} 步</span>
    </div>

    <p v-if="task.start_url" class="source">
      来源：<a :href="task.start_url" target="_blank" rel="noopener">{{ task.start_url }}</a>
    </p>

    <div v-if="trajectory.length" class="trajectory-block">
      <button type="button" class="btn ghost small" @click="showTrajectory = !showTrajectory">
        {{ showTrajectory ? '收起轨迹' : `查看轨迹（${trajectory.length} 步）` }}
      </button>
      <ol v-if="showTrajectory" class="trajectory">
        <li v-for="t in trajectory" :key="t.step">
          <span class="step">第 {{ t.step }} 步</span>
          <code>{{ t.tool }}</code>
          <span class="args">{{ formatArgs(t.args) }}</span>
          <span v-if="t.total_ms" class="ms">{{ t.total_ms }}ms</span>
          <span class="time">{{ t.time }}</span>
        </li>
      </ol>
    </div>

    <ResultTable v-if="items.length" :items="items" />
    <p v-else-if="!running" class="empty">未采集到任何数据。</p>

    <div v-if="items.length" class="export-bar">
      <span>导出：</span>
      <a v-for="(label, fmt) in FORMATS" :key="fmt" class="btn ghost small" :href="exportUrl(task.id, fmt)">
        {{ label }}
      </a>
    </div>
  </div>
</template>
