<script setup>
import { onMounted, ref } from 'vue'
import { getTask, listTasks } from '../api'
import ResultDetail from '../components/ResultDetail.vue'
import StatusBadge from '../components/StatusBadge.vue'

const STATUSES = [
  { key: 'pending', label: '等待中' },
  { key: 'running', label: '运行中' },
  { key: 'success', label: '成功' },
  { key: 'partial', label: '部分完成' },
  { key: 'failed', label: '失败' },
]

const tasks = ref([])
const statusFilter = ref('')
const loading = ref(false)
const error = ref('')
const detail = ref(null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await listTasks(statusFilter.value)
    tasks.value = res.items || []
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function open(id) {
  error.value = ''
  try {
    detail.value = await getTask(id)
  } catch (e) {
    error.value = e.message
  }
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
        <h1>历史任务</h1>
        <p class="sub">最近的采集任务与结果</p>
      </div>
      <div class="filters">
        <select v-model="statusFilter" @change="load">
          <option value="">全部状态</option>
          <option v-for="s in STATUSES" :key="s.key" :value="s.key">{{ s.label }}</option>
        </select>
        <button class="btn ghost" :disabled="loading" @click="load">
          {{ loading ? '加载中……' : '刷新' }}
        </button>
      </div>
    </header>

    <div v-if="error" class="alert error">{{ error }}</div>

    <div class="card">
      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>ID</th>
              <th>描述</th>
              <th>状态</th>
              <th>条目数</th>
              <th>创建时间</th>
              <th />
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in tasks" :key="t.id" :class="{ active: detail && detail.id === t.id }">
              <td class="mono">{{ t.id.slice(0, 8) }}</td>
              <td class="desc">{{ t.task_desc }}</td>
              <td><StatusBadge :status="t.status" /></td>
              <td class="mono">{{ t.item_count }}</td>
              <td class="mono">{{ formatTime(t.created_at) }}</td>
              <td class="ops">
                <button class="btn ghost small" @click="open(t.id)">查看</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!tasks.length" class="empty">{{ loading ? '加载中……' : '暂无历史任务。' }}</p>
    </div>

    <ResultDetail v-if="detail" :task="detail" />
  </section>
</template>
