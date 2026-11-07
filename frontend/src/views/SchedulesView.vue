<script setup>
import { onMounted, reactive, ref } from 'vue'
import {
  createSchedule,
  deleteSchedule,
  listSchedules,
  listSites,
  runSchedule,
  toggleSchedule,
  updateSchedule,
} from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import { confirmDialog } from '../composables/useConfirm'

const DEFAULT_SCHEMA = JSON.stringify(
  {
    type: 'object',
    properties: {
      title: { type: 'string', description: '书名' },
      price: { type: 'string', description: '价格' },
    },
  },
  null,
  2,
)

const schedules = ref([])
const sites = ref([])
const loading = ref(false)
const error = ref('')
const message = ref('')
const showForm = ref(false)
const editingId = ref('')
const saving = ref(false)

const form = reactive({
  task_desc: '',
  start_url: '',
  schema_json: DEFAULT_SCHEMA,
  site_id: '',
  max_items: 0,
  max_pages: 0,
  schedule_type: 'interval',
  interval_minutes: 60,
  daily_times_text: '09:00',
  enabled: true,
})

function resetForm() {
  form.task_desc = ''
  form.start_url = ''
  form.schema_json = DEFAULT_SCHEMA
  form.site_id = ''
  form.max_items = 0
  form.max_pages = 0
  form.schedule_type = 'interval'
  form.interval_minutes = 60
  form.daily_times_text = '09:00'
  form.enabled = true
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await listSchedules()
    schedules.value = res.items || []
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

/** 读取可用的网站记忆（仅启用项）。 */
async function loadSites() {
  try {
    const res = await listSites()
    sites.value = (res.items || []).filter((s) => s.enabled)
  } catch {
    sites.value = []
  }
}

function openCreate() {
  editingId.value = ''
  resetForm()
  showForm.value = true
}

function openEdit(schedule) {
  editingId.value = schedule.id
  form.task_desc = schedule.task_desc
  form.start_url = schedule.start_url
  form.schema_json = schedule.schema_json
  form.site_id = schedule.site_id || ''
  form.max_items = Number(schedule.max_items) || 0
  form.max_pages = Number(schedule.max_pages) || 0
  form.schedule_type = schedule.schedule_type
  form.interval_minutes = schedule.interval_minutes
  form.daily_times_text = (schedule.daily_times || []).join(', ')
  form.enabled = schedule.enabled
  showForm.value = true
}

function closeForm() {
  if (saving.value) return
  showForm.value = false
}

async function save() {
  error.value = ''
  message.value = ''
  if (!form.task_desc.trim() || !form.start_url.trim()) {
    error.value = '请完整填写「目标描述」与「起始网址」。'
    return
  }
  try {
    JSON.parse(form.schema_json)
  } catch (e) {
    error.value = `Schema 不是合法 JSON：${e.message}`
    return
  }

  const payload = {
    task_desc: form.task_desc.trim(),
    start_url: form.start_url.trim(),
    schema_json: form.schema_json.trim(),
    site_id: form.site_id,
    max_items: Number(form.max_items) || 0,
    max_pages: Number(form.max_pages) || 0,
    schedule_type: form.schedule_type,
    interval_minutes: Number(form.interval_minutes) || 1,
    daily_times:
      form.schedule_type === 'daily'
        ? form.daily_times_text
            .split(',')
            .map((x) => x.trim())
            .filter(Boolean)
        : [],
    enabled: form.enabled,
  }

  saving.value = true
  try {
    if (editingId.value) {
      await updateSchedule(editingId.value, payload)
      message.value = '定时任务已更新。'
    } else {
      await createSchedule(payload)
      message.value = '定时任务已创建。'
    }
    showForm.value = false
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

async function toggle(schedule) {
  error.value = ''
  message.value = ''
  try {
    const updated = await toggleSchedule(schedule.id, !schedule.enabled)
    Object.assign(schedule, updated)
    message.value = updated.enabled ? '已开启定时爬取。' : '已暂停定时爬取。'
  } catch (e) {
    error.value = e.message
  }
}

async function remove(schedule) {
  const ok = await confirmDialog({
    title: '删除定时任务',
    message: `确定删除「${schedule.task_desc}」这条定时任务？历史采集结果不会被删除。`,
    confirmText: '删除',
    danger: true,
  })
  if (!ok) {
    return
  }
  error.value = ''
  message.value = ''
  try {
    await deleteSchedule(schedule.id)
    message.value = '定时任务已删除。'
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function runNow(schedule) {
  error.value = ''
  message.value = ''
  try {
    const res = await runSchedule(schedule.id)
    message.value = `已开始运行，任务 ID：${String(res.task_id).slice(0, 8)}（结果可在「历史任务」查看）。`
  } catch (e) {
    error.value = e.message
  }
}

function shortDesc(text) {
  const t = (text || '').replace(/\s+/g, ' ').trim()
  return t.length > 18 ? `${t.slice(0, 18)}…` : t
}

function scheduleText(schedule) {
  if (schedule.schedule_type === 'daily') {
    return `每日 ${(schedule.daily_times || []).join(' / ')}`
  }
  return `每 ${schedule.interval_minutes} 分钟`
}

function formatTime(value) {
  return value ? value.replace('T', ' ') : '—'
}

onMounted(() => {
  load()
  loadSites()
})
</script>

<template>
  <section class="page">
    <div class="page-toolbar">
      <div class="filters">
        <button class="btn ghost" :disabled="loading" @click="load">
          {{ loading ? '加载中……' : '刷新' }}
        </button>
        <button class="btn primary" @click="openCreate">新建定时任务</button>
      </div>
    </div>

    <div v-if="error" class="alert error">{{ error }}</div>
    <div v-if="message" class="alert info">{{ message }}</div>

    <div class="card">
      <div class="table-wrap">
        <table class="table schedule-table">
          <colgroup>
            <col style="width: 17%" />
            <col style="width: 17%" />
            <col style="width: 9%" />
            <col style="width: 7%" />
            <col style="width: 12%" />
            <col style="width: 14%" />
            <col style="width: 6%" />
            <col style="width: 18%" />
          </colgroup>
          <thead>
            <tr>
              <th>需求</th>
              <th>网址</th>
              <th>调度</th>
              <th>开关</th>
              <th>下次运行</th>
              <th>上次运行</th>
              <th>运行次数</th>
              <th />
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in schedules" :key="s.id">
              <td class="desc" :title="s.task_desc">
                <span class="desc-text">{{ shortDesc(s.task_desc) }}</span>
              </td>
              <td class="url" :title="s.start_url">{{ s.start_url }}</td>
              <td>{{ scheduleText(s) }}</td>
              <td>
                <label class="switch" :title="s.enabled ? '已启用' : '已暂停'">
                  <input type="checkbox" :checked="s.enabled" @change="toggle(s)" />
                  <span class="slider" />
                </label>
              </td>
              <td class="mono">{{ s.enabled ? formatTime(s.next_run_at) : '—' }}</td>
              <td class="mono">
                <span v-if="s.last_run_at">{{ formatTime(s.last_run_at) }}</span>
                <span v-else class="muted">—</span>
                <template v-if="s.last_status">
                  <StatusBadge :status="s.last_status" />
                </template>
              </td>
              <td class="mono">{{ s.run_count }}</td>
              <td class="ops">
                <button class="btn ghost small" @click="runNow(s)">运行</button>
                <button class="btn ghost small" @click="openEdit(s)">编辑</button>
                <button class="btn ghost small danger" @click="remove(s)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!schedules.length" class="empty">
        {{ loading ? '加载中……' : '暂无定时任务，点击右上角「新建定时任务」开始配置。' }}
      </p>
    </div>

    <!-- 新建 / 编辑弹窗 -->
    <div v-if="showForm" class="modal-backdrop" @click.self="closeForm">
      <div class="modal">
        <div class="modal-head">
          <h2>{{ editingId ? '编辑定时任务' : '新建定时任务' }}</h2>
          <button class="modal-close" type="button" @click="closeForm">×</button>
        </div>

        <div class="modal-body">
          <div class="grid-2">
            <label class="field">
              <span>目标描述</span>
              <input v-model="form.task_desc" placeholder="例：抓取前 10 本书的书名与价格" />
            </label>
            <label class="field">
              <span>起始网址</span>
              <input v-model="form.start_url" placeholder="https://example.com/books" />
            </label>
          </div>

          <div class="grid-3">
            <label class="field">
              <span>网站记忆</span>
              <select v-model="form.site_id">
                <option value="">不使用（默认）</option>
                <option v-for="s in sites" :key="s.id" :value="s.id">
                  {{ s.name }} · {{ s.logged_in ? '已登录' : '未登录' }}
                </option>
              </select>
            </label>
            <label class="field">
              <span>最多采集条数</span>
              <input v-model.number="form.max_items" type="number" min="0" max="500" />
              <small>0 表示不限制</small>
            </label>
            <label class="field">
              <span>最多翻页</span>
              <input v-model.number="form.max_pages" type="number" min="0" max="50" />
              <small>0 表示不限制</small>
            </label>
          </div>

          <label class="field">
            <span>字段 Schema（JSON）</span>
            <textarea v-model="form.schema_json" rows="7" spellcheck="false" />
          </label>

          <div class="grid-2">
            <label class="field">
              <span>调度方式</span>
              <select v-model="form.schedule_type">
                <option value="interval">固定间隔</option>
                <option value="daily">每日定点</option>
              </select>
            </label>
            <label v-if="form.schedule_type === 'interval'" class="field">
              <span>间隔（分钟）</span>
              <input v-model.number="form.interval_minutes" type="number" min="1" />
            </label>
            <label v-else class="field">
              <span>定点时间</span>
              <input v-model="form.daily_times_text" placeholder="09:00, 18:00" />
              <small>多个时间用英文逗号分隔（HH:MM）</small>
            </label>
          </div>

          <label class="switch-row">
            <span>启用此定时任务</span>
            <label class="switch">
              <input v-model="form.enabled" type="checkbox" />
              <span class="slider" />
            </label>
          </label>
        </div>

        <div class="modal-foot">
          <button class="btn ghost" type="button" :disabled="saving" @click="closeForm">取消</button>
          <button class="btn primary" type="button" :disabled="saving" @click="save">
            {{ saving ? '保存中……' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>
