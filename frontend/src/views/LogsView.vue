<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { clearLogs, listLogs } from '../api'
import { confirmDialog } from '../composables/useConfirm'

const LEVELS = [
  { key: '', label: '全部' },
  { key: 'INFO', label: 'INFO' },
  { key: 'WARNING', label: 'WARNING' },
  { key: 'ERROR', label: 'ERROR' },
]

const logs = ref([])
const counts = ref({})
const total = ref(0)
const level = ref('')
const query = ref('')
const autoRefresh = ref(true)
const loading = ref(false)
const error = ref('')
const clearing = ref(false)

let timer = null
let debounceTimer = null

async function load(silent = false) {
  if (!silent) loading.value = true
  error.value = ''
  try {
    const res = await listLogs(level.value, query.value.trim(), 300)
    logs.value = res.items || []
    counts.value = res.counts || {}
    total.value = res.total || 0
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function levelCount(key) {
  if (!key) return Object.values(counts.value).reduce((sum, n) => sum + n, 0)
  return counts.value[key] || 0
}

function formatTime(value) {
  return (value || '').replace('T', ' ')
}

async function clearAll() {
  const ok = await confirmDialog({
    title: '清空系统日志',
    message: '确定清空全部系统日志吗？清空后不可恢复。',
    confirmText: '清空',
    danger: true,
  })
  if (!ok) return
  clearing.value = true
  try {
    await clearLogs()
    await load(true)
  } catch (e) {
    error.value = e.message
  } finally {
    clearing.value = false
  }
}

watch(level, () => load())

watch(query, () => {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => load(), 300)
})

onMounted(() => {
  load()
  timer = setInterval(() => {
    if (autoRefresh.value) load(true)
  }, 5000)
})

onBeforeUnmount(() => {
  clearInterval(timer)
  clearTimeout(debounceTimer)
})
</script>

<template>
  <section class="page">
    <div class="page-toolbar">
      <div class="filters">
        <label class="log-switch">
          <input v-model="autoRefresh" type="checkbox" />
          自动刷新（5 秒）
        </label>
        <input v-model="query" class="doc-search" type="search" placeholder="搜索日志内容或来源" />
        <button class="btn ghost" :disabled="loading" @click="load()">
          {{ loading ? '加载中……' : '刷新' }}
        </button>
        <button class="btn ghost" :disabled="clearing" @click="clearAll">清空</button>
      </div>
    </div>

    <div v-if="error" class="alert error">{{ error }}</div>

    <div class="category-bar">
      <button
        v-for="item in LEVELS"
        :key="item.key || 'all'"
        class="cat-chip"
        :class="{ active: level === item.key }"
        @click="level = item.key"
      >
        {{ item.label }}
        <span class="cat-count">{{ levelCount(item.key) }}</span>
      </button>
    </div>

    <p class="log-count">
      命中 {{ total }} 条，最多展示最近 300 条
    </p>

    <div class="card log-card">
      <div class="log-list">
        <div v-for="item in logs" :key="item.id" class="log-row">
          <span class="log-time">{{ formatTime(item.time) }}</span>
          <span class="log-level" :class="`log-level-${item.level.toLowerCase()}`">
            {{ item.level }}
          </span>
          <span class="log-source">{{ item.logger }}</span>
          <span class="log-msg">{{ item.message }}</span>
        </div>
        <div v-if="!logs.length" class="empty">
          {{ loading ? '正在加载日志……' : '暂无日志' }}
        </div>
      </div>
    </div>
  </section>
</template>
