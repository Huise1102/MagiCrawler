<script setup>
import { computed, onMounted, ref } from 'vue'
import { exportUrl, listDocuments } from '../api'

const PAGE_SIZE = 24

const docs = ref([])
const loading = ref(false)
const error = ref('')
const query = ref('')
const taskFilter = ref('')
const visibleCount = ref(PAGE_SIZE)
const selected = ref(null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await listDocuments()
    docs.value = res.items || []
    visibleCount.value = PAGE_SIZE
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

const taskOptions = computed(() => {
  const seen = new Map()
  for (const d of docs.value) {
    if (!seen.has(d.task_id)) seen.set(d.task_id, d.task_desc)
  }
  return [...seen.entries()].map(([id, desc]) => ({ id, desc }))
})

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return docs.value.filter((d) => {
    if (taskFilter.value && d.task_id !== taskFilter.value) return false
    if (!q) return true
    return `${d.task_desc} ${JSON.stringify(d.fields)}`.toLowerCase().includes(q)
  })
})

const visibleDocs = computed(() => filtered.value.slice(0, visibleCount.value))

const TITLE_PATTERN = /title|name|标题|名称|书名|姓名|article|headline|产品/i
const LINK_PATTERN = /url|link|href|网址|链接|地址/i

function isLinkField(key, value) {
  return typeof value === 'string' && (/^https?:\/\//i.test(value) || LINK_PATTERN.test(key))
}

function titleOf(doc) {
  const entries = Object.entries(doc.fields)
  for (const [k, v] of entries) {
    if (TITLE_PATTERN.test(k) && typeof v === 'string' && v.trim()) return v.trim()
  }
  for (const [k, v] of entries) {
    if (isLinkField(k, v)) continue
    if (typeof v === 'string' && v.trim() && !/^https?:\/\//i.test(v)) return v.trim()
  }
  return '未命名字段'
}

function linkOf(doc) {
  for (const [k, v] of Object.entries(doc.fields)) {
    if (isLinkField(k, v)) return v
  }
  return ''
}

function previewFields(doc) {
  return Object.entries(doc.fields)
    .filter(([k, v]) => v !== null && v !== undefined && v !== '' && !isLinkField(k, v))
    .slice(0, 4)
}

function formatValue(value) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

function formatTime(value) {
  return (value || '').replace('T', ' ')
}

onMounted(load)
</script>

<template>
  <section class="page">
    <header class="page-head row">
      <div>
        <h1>文档库</h1>
        <p class="sub">浏览各任务采集到的条目，支持搜索与按任务筛选</p>
      </div>
      <div class="filters">
        <input v-model="query" class="doc-search" placeholder="搜索标题、字段或任务……" />
        <select v-model="taskFilter">
          <option value="">全部任务</option>
          <option v-for="t in taskOptions" :key="t.id" :value="t.id">{{ t.desc }}</option>
        </select>
        <button class="btn ghost" :disabled="loading" @click="load">
          {{ loading ? '加载中……' : '刷新' }}
        </button>
      </div>
    </header>

    <div v-if="error" class="alert error">{{ error }}</div>

    <p v-if="docs.length" class="doc-count">
      共 {{ filtered.length }} 条内容<template v-if="filtered.length !== docs.length">
        （筛选自 {{ docs.length }} 条）</template
      >
    </p>

    <div v-if="visibleDocs.length" class="doc-grid">
      <article v-for="d in visibleDocs" :key="d.id" class="doc-card" @click="selected = d">
        <h3>{{ titleOf(d) }}</h3>
        <dl v-if="previewFields(d).length" class="doc-fields">
          <div v-for="[k, v] in previewFields(d)" :key="k" class="doc-field-row">
            <dt class="doc-key">{{ k }}</dt>
            <dd class="doc-value">{{ formatValue(v) }}</dd>
          </div>
        </dl>
        <footer class="doc-meta">
          <span class="doc-source">{{ d.task_desc }}</span>
          <span class="mono">{{ formatTime(d.created_at) }}</span>
        </footer>
      </article>
    </div>

    <div v-if="!visibleDocs.length" class="card">
      <p class="empty">{{ loading ? '加载中……' : '暂无采集内容，先去首页执行一次爬取吧。' }}</p>
    </div>

    <div v-if="visibleCount < filtered.length" class="doc-more">
      <button class="btn ghost" @click="visibleCount += PAGE_SIZE">
        加载更多（剩余 {{ filtered.length - visibleCount }} 条）
      </button>
    </div>

    <div v-if="selected" class="modal-backdrop" @click.self="selected = null">
      <div class="modal">
        <div class="modal-head">
          <h2>{{ titleOf(selected) }}</h2>
          <button class="modal-close" aria-label="关闭" @click="selected = null">×</button>
        </div>
        <div class="modal-body">
          <p class="doc-detail-sub">
            {{ selected.task_desc }} · {{ formatTime(selected.created_at) }}
          </p>
          <p v-if="linkOf(selected)" class="doc-detail-link">
            <a :href="linkOf(selected)" target="_blank" rel="noreferrer">{{ linkOf(selected) }}</a>
          </p>
          <div class="doc-detail-list">
            <div v-for="[k, v] in Object.entries(selected.fields)" :key="k" class="doc-detail-row">
              <span class="doc-key">{{ k }}</span>
              <span class="doc-detail-value">{{ formatValue(v) }}</span>
            </div>
          </div>
        </div>
        <div class="modal-foot">
          <a class="btn ghost small" :href="exportUrl(selected.task_id, 'json')">导出 JSON</a>
          <a class="btn ghost small" :href="exportUrl(selected.task_id, 'csv')">导出 CSV</a>
          <button class="btn small" @click="selected = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>
