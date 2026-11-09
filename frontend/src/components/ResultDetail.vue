<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { exportUrl, getTaskDiff } from '../api'
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
    if (Object.keys(propsObj).length) {
      return Object.entries(propsObj).map(([name, spec]) => ({
        name,
        desc: (spec && typeof spec === 'object' && spec.description) || '',
      }))
    }
    // 兼容旧版 {"fields": [{"name": ..., "desc": ...}]} 格式
    if (Array.isArray(parsed.fields)) {
      return parsed.fields
        .filter((f) => f && f.name)
        .map((f) => ({ name: f.name, desc: f.desc || f.description || '' }))
    }
    return []
  } catch {
    return []
  }
})

function formatMs(value) {
  const n = Number(value)
  if (!Number.isFinite(n) || n <= 0) return '—'
  return n >= 1000 ? `${(n / 1000).toFixed(2)} 秒` : `${Math.round(n)} 毫秒`
}

/** 与上一次采集的对比结果（同一网址/站点最近的完成记录）。 */
const diff = ref(null)
const diffLoading = ref(false)
const diffError = ref('')

async function loadDiff() {
  diff.value = null
  diffError.value = ''
  if (running.value || !props.task.id) return
  diffLoading.value = true
  try {
    diff.value = await getTaskDiff(props.task.id)
  } catch (e) {
    diffError.value = e.message
  } finally {
    diffLoading.value = false
  }
}

function diffTime(value) {
  return (value || '').replace('T', ' ').slice(0, 16)
}

function diffValue(value) {
  if (value === null || value === undefined || value === '') return '—'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

onMounted(loadDiff)
watch(() => props.task.id, loadDiff)
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
      <span v-if="timings.recipe_ms" class="chip">接口直采 {{ formatMs(timings.recipe_ms) }}</span>
      <span v-if="timings.action_ms" class="chip">页面动作 {{ formatMs(timings.action_ms) }}</span>
      <span v-if="timings.llm_calls" class="chip">LLM 调用 {{ timings.llm_calls }} 次</span>
      <span v-if="timings.steps" class="chip">{{ timings.steps }} 步</span>
    </div>

    <div v-if="!running" class="diff-block">
      <div class="diff-head">
        <h3>与上次对比</h3>
        <span v-if="diff && diff.previous" class="diff-meta">
          上次 {{ diffTime(diff.previous.created_at) }} · {{ diff.previous.item_count }} 条
        </span>
      </div>
      <p v-if="diffLoading" class="diff-note"><span class="spinner" /> 正在对比……</p>
      <p v-else-if="diffError" class="diff-note">{{ diffError }}</p>
      <p v-else-if="diff && !diff.comparable" class="diff-note">{{ diff.reason }}</p>
      <template v-else-if="diff">
        <div class="diff-chips">
          <span class="chip diff-add">新增 {{ diff.counts.added }}</span>
          <span class="chip diff-remove">消失 {{ diff.counts.removed }}</span>
          <span class="chip diff-change">变更 {{ diff.counts.changed }}</span>
          <span class="chip">不变 {{ diff.counts.unchanged }}</span>
        </div>
        <div v-if="diff.counts.added || diff.counts.removed || diff.counts.changed" class="diff-cols">
          <div v-if="diff.added.length" class="diff-col">
            <h4>新增</h4>
            <ul>
              <li v-for="(x, i) in diff.added" :key="`a${i}`" :title="JSON.stringify(x.fields, null, 2)">
                {{ x.label }}
              </li>
            </ul>
            <p v-if="diff.counts.added > diff.added.length" class="diff-more">
              还有 {{ diff.counts.added - diff.added.length }} 条
            </p>
          </div>
          <div v-if="diff.removed.length" class="diff-col">
            <h4>消失</h4>
            <ul>
              <li v-for="(x, i) in diff.removed" :key="`r${i}`" :title="JSON.stringify(x.fields, null, 2)">
                {{ x.label }}
              </li>
            </ul>
            <p v-if="diff.counts.removed > diff.removed.length" class="diff-more">
              还有 {{ diff.counts.removed - diff.removed.length }} 条
            </p>
          </div>
          <div v-if="diff.changed.length" class="diff-col">
            <h4>变更</h4>
            <ul>
              <li v-for="(x, i) in diff.changed" :key="`c${i}`">
                <span class="diff-name">{{ x.label }}</span>
                <span v-for="(c, j) in x.changes" :key="`${i}-${j}`" class="diff-field">
                  {{ c.field }}：{{ diffValue(c.before) }} → {{ diffValue(c.after) }}
                </span>
              </li>
            </ul>
            <p v-if="diff.counts.changed > diff.changed.length" class="diff-more">
              还有 {{ diff.counts.changed - diff.changed.length }} 条
            </p>
          </div>
        </div>
        <p v-else class="diff-note">与上次相比没有差异。</p>
      </template>
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
