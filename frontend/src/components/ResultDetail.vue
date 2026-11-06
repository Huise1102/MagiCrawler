<script setup>
import { computed } from 'vue'
import { exportUrl } from '../api'
import ResultTable from './ResultTable.vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps({
  task: { type: Object, required: true },
})

const FORMATS = { json: 'JSON', csv: 'CSV', xlsx: 'Excel', md: 'Markdown', docx: 'Word' }

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
const schemaText = computed(() => (props.task.schema_json || '').trim())
const createdText = computed(() => (props.task.created_at || '').replace('T', ' '))

/** 任务介绍：解析 Schema 字段名与说明，便于回看这条任务要采什么。 */
const schemaFields = computed(() => {
  if (!schemaText.value) return []
  try {
    const parsed = JSON.parse(schemaText.value)
    const propsObj = parsed.properties || (parsed.items && parsed.items.properties) || {}
    return Object.entries(propsObj).map(([name, spec]) => ({
      name,
      desc: (spec && typeof spec === 'object' && spec.description) || '',
    }))
  } catch {
    return []
  }
})

function formatMs(value) {
  const n = Number(value)
  if (!Number.isFinite(n) || n <= 0) return '—'
  return n >= 1000 ? `${(n / 1000).toFixed(2)} 秒` : `${Math.round(n)} 毫秒`
}
</script>

<template>
  <div class="card result-card">
    <div class="result-head">
      <div class="result-title">
        <h2>采集结果</h2>
      </div>
      <StatusBadge :status="task.status" />
    </div>

    <div class="result-body">
      <div class="task-intro">
      <h3>采集任务介绍</h3>
      <div class="intro-row">
        <span class="intro-label">目标描述</span>
        <p class="intro-value">{{ task.task_desc || '—' }}</p>
      </div>
      <div class="intro-row">
        <span class="intro-label">起始网址</span>
        <p class="intro-value">
          <a v-if="task.start_url" :href="task.start_url" target="_blank" rel="noopener">
            {{ task.start_url }}
          </a>
          <span v-else>—</span>
        </p>
      </div>
      <div class="intro-row">
        <span class="intro-label">采集字段</span>
        <div class="field-chips">
          <span
            v-for="f in schemaFields"
            :key="f.name"
            class="field-chip"
            :title="f.desc || f.name"
          >
            {{ f.name }}
          </span>
          <span v-if="!schemaFields.length" class="intro-value">—</span>
        </div>
      </div>
      <div class="intro-row">
        <span class="intro-label">创建时间</span>
        <p class="intro-value mono">{{ createdText || '—' }}</p>
      </div>
      <details v-if="schemaText" class="schema-detail">
        <summary>查看字段 Schema</summary>
        <pre>{{ schemaText }}</pre>
      </details>
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

    <ResultTable v-if="items.length" :items="items" />
    <p v-else-if="!running" class="empty">未采集到任何数据。</p>

    <div v-if="items.length" class="export-bar">
      <span>导出：</span>
      <a v-for="(label, fmt) in FORMATS" :key="fmt" class="btn ghost small" :href="exportUrl(task.id, fmt)">
        {{ label }}
      </a>
    </div>
    </div>
  </div>
</template>
