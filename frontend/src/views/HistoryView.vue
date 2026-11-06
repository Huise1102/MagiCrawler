<script setup>
import { onMounted, ref } from 'vue'
import { deleteTask, getTask, listTasks } from '../api'
import ResultDetail from '../components/ResultDetail.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { confirmDialog } from '../composables/useConfirm'

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
const deletingId = ref('')
const detail = ref(null)
const detailLoading = ref(false)
const detailError = ref('')

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
  detail.value = null
  detailError.value = ''
  detailLoading.value = true
  try {
    detail.value = await getTask(id)
  } catch (e) {
    detailError.value = e.message
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  detail.value = null
  detailError.value = ''
}

async function remove(t) {
  if (deletingId.value) return
  const ok = await confirmDialog({
    title: '删除任务',
    message: `确定删除任务「${t.task_desc}」？文档库中由它同步的内容会保留。`,
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return
  deletingId.value = t.id
  error.value = ''
  try {
    await deleteTask(t.id)
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    deletingId.value = ''
  }
}

function formatTime(value) {
  return (value || '').replace('T', ' ')
}

onMounted(load)
</script>

<template>
  <section class="page">
    <div class="page-toolbar">
      <div class="filters">
        <select v-model="statusFilter" @change="load">
          <option value="">全部状态</option>
          <option v-for="s in STATUSES" :key="s.key" :value="s.key">{{ s.label }}</option>
        </select>
        <button class="btn ghost" :disabled="loading" @click="load">
          {{ loading ? '加载中……' : '刷新' }}
        </button>
      </div>
    </div>

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
            <tr v-for="t in tasks" :key="t.id">
              <td class="mono">{{ t.id.slice(0, 8) }}</td>
              <td class="desc">{{ t.task_desc }}</td>
              <td><StatusBadge :status="t.status" /></td>
              <td class="mono">{{ t.item_count }}</td>
              <td class="mono">{{ formatTime(t.created_at) }}</td>
              <td class="ops">
                <button class="btn ghost small" @click="open(t.id)">查看</button>
                <button
                  class="btn ghost small danger"
                  :disabled="t.status === 'running' || deletingId === t.id"
                  :title="t.status === 'running' ? '运行中的任务不可删除' : ''"
                  @click="remove(t)"
                >
                  {{ deletingId === t.id ? '删除中…' : '删除' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!tasks.length" class="empty">{{ loading ? '加载中……' : '暂无历史任务。' }}</p>
    </div>

    <div
      v-if="detailLoading || detail || detailError"
      class="modal-backdrop"
      @click.self="closeDetail"
    >
      <div class="modal wide" role="dialog" aria-modal="true">
        <div class="modal-head">
          <h2>任务详情</h2>
          <button type="button" class="modal-close" aria-label="关闭" @click="closeDetail">
            ×
          </button>
        </div>
        <div class="modal-body">
          <div v-if="detailError" class="alert error">{{ detailError }}</div>
          <p v-if="detailLoading" class="empty"><span class="spinner" /> 加载中……</p>
          <ResultDetail v-else-if="detail" :task="detail" />
        </div>
        <div class="modal-foot">
          <button type="button" class="btn ghost" @click="closeDetail">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>
