<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { createSite, deleteSite, listSites, loginSite, updateSite } from '../api'
import { confirmDialog } from '../composables/useConfirm'

const sites = ref([])
const loading = ref(false)
const error = ref('')
const message = ref('')
const showForm = ref(false)
const editingId = ref('')
const saving = ref(false)
const loggingId = ref('')
let loginTimer = null

const form = reactive({
  name: '',
  domain: '',
  start_url: '',
  login_note: '',
  enabled: true,
})

function resetForm() {
  form.name = ''
  form.domain = ''
  form.start_url = ''
  form.login_note = ''
  form.enabled = true
}

async function load(quiet = false) {
  if (!quiet) loading.value = true
  try {
    const res = await listSites()
    sites.value = res.items || []
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = ''
  resetForm()
  showForm.value = true
}

function openEdit(site) {
  editingId.value = site.id
  form.name = site.name
  form.domain = site.domain || ''
  form.start_url = site.start_url || ''
  form.login_note = site.login_note || ''
  form.enabled = !!site.enabled
  showForm.value = true
}

function closeForm() {
  if (saving.value) return
  showForm.value = false
}

async function save() {
  error.value = ''
  message.value = ''
  if (!form.name.trim()) {
    error.value = '请填写站点名称。'
    return
  }
  if (!form.domain.trim() && !form.start_url.trim()) {
    error.value = '请至少填写域名或起始网址。'
    return
  }

  const payload = {
    name: form.name.trim(),
    domain: form.domain.trim(),
    start_url: form.start_url.trim(),
    login_note: form.login_note.trim(),
    enabled: form.enabled,
  }

  saving.value = true
  try {
    if (editingId.value) {
      await updateSite(editingId.value, payload)
      message.value = '网站记忆已更新。'
    } else {
      await createSite(payload)
      message.value = '网站记忆已创建，可点击「登录」保存登录态。'
    }
    showForm.value = false
    await load(true)
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

/** 打开登录窗口，并轮询登录标记以刷新状态。 */
async function login(site) {
  error.value = ''
  message.value = ''
  loggingId.value = site.id
  try {
    const res = await loginSite(site.id)
    message.value = res.message
    pollLogin()
  } catch (e) {
    error.value = e.message
  } finally {
    loggingId.value = ''
  }
}

function pollLogin() {
  clearInterval(loginTimer)
  let tries = 0
  loginTimer = setInterval(async () => {
    tries += 1
    await load(true)
    const changed = sites.value.some((s) => s.logged_in)
    if (changed || tries >= 24) {
      clearInterval(loginTimer)
      loginTimer = null
    }
  }, 5000)
}

async function toggle(site) {
  error.value = ''
  message.value = ''
  try {
    await updateSite(site.id, {
      name: site.name,
      domain: site.domain || '',
      start_url: site.start_url || '',
      login_note: site.login_note || '',
      enabled: !site.enabled,
    })
    site.enabled = !site.enabled
    message.value = site.enabled ? '已启用该站点。' : '已停用该站点。'
  } catch (e) {
    error.value = e.message
  }
}

async function remove(site) {
  const ok = await confirmDialog({
    title: '删除网站记忆',
    message: `确定删除「${site.name}」？本地保存的登录态也会一并清除。`,
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return

  error.value = ''
  message.value = ''
  try {
    await deleteSite(site.id)
    message.value = '网站记忆已删除。'
    await load(true)
  } catch (e) {
    error.value = e.message
  }
}

function formatTime(value) {
  return value ? value.replace('T', ' ') : '—'
}

onMounted(load)
onBeforeUnmount(() => clearInterval(loginTimer))
</script>

<template>
  <section class="page">
    <div class="page-toolbar">
      <div class="filters">
        <button class="btn ghost" :disabled="loading" @click="load()">
          {{ loading ? '加载中……' : '刷新' }}
        </button>
        <button class="btn primary" @click="openCreate">新增网站记忆</button>
      </div>
    </div>

    <div class="alert info site-tip">
      让智能体记住需要登录的站点：点击「登录」会打开独立浏览器窗口，登录完成后关闭窗口即可，
      Cookie 会保存在本地。采集或定时任务选择该站点后会自动复用登录态。
    </div>

    <div v-if="error" class="alert error">{{ error }}</div>
    <div v-if="message" class="alert info">{{ message }}</div>

    <div v-if="sites.length" class="site-grid">
      <div v-for="site in sites" :key="site.id" class="card site-card">
        <div class="site-head">
          <div class="site-title">
            <h3>{{ site.name }}</h3>
            <p class="site-domain">{{ site.domain || '未填写域名' }}</p>
          </div>
          <span class="badge" :class="site.logged_in ? 'badge-success' : 'badge-pending'">
            <span class="dot" />
            {{ site.logged_in ? '已登录' : '未登录' }}
          </span>
        </div>

        <p class="site-url mono" :title="site.start_url">
          {{ site.start_url || '未填写起始网址' }}
        </p>
        <p class="site-note">{{ site.login_note || '暂无登录说明。' }}</p>

        <div class="site-meta">
          <span>上次登录：{{ formatTime(site.logged_in_at) }}</span>
          <span>上次使用：{{ formatTime(site.last_used_at) }}</span>
        </div>

        <div class="site-actions">
          <button class="btn primary small" :disabled="loggingId === site.id" @click="login(site)">
            {{ loggingId === site.id ? '正在打开……' : site.logged_in ? '重新登录' : '登录' }}
          </button>
          <button class="btn ghost small" @click="openEdit(site)">编辑</button>
          <button class="btn ghost small danger" @click="remove(site)">删除</button>
          <label class="switch" :title="site.enabled ? '已启用' : '已停用'">
            <input type="checkbox" :checked="site.enabled" @change="toggle(site)" />
            <span class="slider" />
          </label>
        </div>
      </div>
    </div>

    <div v-else class="card">
      <p class="empty">
        {{ loading ? '加载中……' : '还没有网站记忆。点击右上角「新增网站记忆」添加需要长期采集的站点。' }}
      </p>
    </div>

    <div v-if="showForm" class="modal-backdrop" @click.self="closeForm">
      <div class="modal">
        <div class="modal-head">
          <h2>{{ editingId ? '编辑网站记忆' : '新增网站记忆' }}</h2>
          <button class="modal-close" type="button" @click="closeForm">×</button>
        </div>

        <div class="modal-body">
          <div class="grid-2">
            <label class="field">
              <span>站点名称</span>
              <input v-model="form.name" placeholder="例：抖音创作者中心" />
            </label>
            <label class="field">
              <span>域名</span>
              <input v-model="form.domain" placeholder="例：creator.douyin.com" />
              <small>选填，用于识别与展示站点。</small>
            </label>
          </div>

          <label class="field">
            <span>起始网址</span>
            <input v-model="form.start_url" placeholder="https://creator.douyin.com/..." />
            <small>点击「登录」时会先打开这个页面。</small>
          </label>

          <label class="field">
            <span>登录说明</span>
            <textarea
              v-model="form.login_note"
              rows="3"
              placeholder="例：扫码登录后，进入个人主页再关闭窗口。"
            />
          </label>

          <label class="switch-row">
            <span>启用此网站记忆</span>
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
