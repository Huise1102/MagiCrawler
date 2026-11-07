<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  cleanupRetention,
  getRetentionStats,
  getSettings,
  listModels,
  saveNotify,
  saveRetention,
  saveSettings,
  saveTheme,
  testNotify,
  testSettings,
} from '../api'
import { confirmDialog } from '../composables/useConfirm'
import { applyTheme, getThemePreference } from '../theme'

const THEME_OPTIONS = [
  { key: 'light', label: '浅色模式', desc: '明亮清爽，适合白天使用' },
  { key: 'dark', label: '深色模式', desc: '低亮度界面，夜间更护眼' },
  { key: 'system', label: '跟随系统', desc: '随系统外观自动切换' },
]

const PRESETS = [
  { key: 'custom', name: '自定义', base_url: '', model: '', hint: '填写任意 OpenAI 兼容接口' },
  { key: 'openai', name: 'OpenAI', base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini', hint: '填写 OpenAI API Key' },
  { key: 'deepseek', name: 'DeepSeek', base_url: 'https://api.deepseek.com', model: 'deepseek-chat', hint: '填写 DeepSeek API Key' },
  {
    key: 'dashscope',
    name: '通义千问',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen-plus',
    hint: '填写 DashScope API Key',
  },
  { key: 'moonshot', name: 'Kimi', base_url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k', hint: '填写 Moonshot API Key' },
  {
    key: 'zhipu',
    name: '智谱 GLM',
    base_url: 'https://open.bigmodel.cn/api/paas/v4',
    model: 'glm-4-flash',
    hint: '填写智谱 API Key',
  },
]

const themePref = ref(getThemePreference())
const resolvedTheme = ref(document.documentElement.dataset.theme || 'light')

const NOTIFY_EVENTS = [
  { key: 'success', label: '采集成功' },
  { key: 'partial', label: '部分完成' },
  { key: 'failed', label: '采集失败' },
]

const notify = reactive({
  enabled: false,
  webhook_url: '',
  events: ['success', 'partial', 'failed'],
})
const notifySaving = ref(false)
const notifyTesting = ref(false)
const notifyResult = ref(null)
const notifyError = ref('')
const savedNotify = ref(null)

const retention = reactive({ enabled: false, days: 90 })
const retentionStats = ref({ tasks: 0, documents: 0, cutoff: '' })
const retentionSaving = ref(false)
const retentionCleaning = ref(false)
const retentionError = ref('')
const retentionResult = ref(null)

const form = reactive({
  provider: 'ollama',
  base_url: '',
  api_key: '',
  model: '',
  temperature: 0,
  max_tokens: 4096,
  timeout_s: 120,
})

const current = ref(null)
const settingsFile = ref('')
const models = ref([])
const activePreset = ref('custom')
const showKey = ref(false)
const loading = ref(true)
const saving = ref(false)
const testing = ref(false)
const modelLoading = ref(false)
const error = ref('')
const notice = ref('')
const testResult = ref(null)

const presetHint = computed(
  () => PRESETS.find((item) => item.key === activePreset.value)?.hint || '',
)

async function chooseTheme(key) {
  themePref.value = key
  resolvedTheme.value = applyTheme(key)
  try {
    await saveTheme(key)
  } catch (e) {
    error.value = `主题已在本机切换，但同步到服务端失败：${e.message}`
  }
}

function setProvider(provider) {
  form.provider = provider
  error.value = ''
  testResult.value = null
  models.value = []
  if (provider === 'ollama') {
    activePreset.value = 'custom'
    form.base_url = 'http://127.0.0.1:11434/v1'
    loadModels(true)
  } else if (!form.base_url || form.base_url.includes('11434')) {
    applyPreset(PRESETS[1])
  }
}

function applyPreset(preset) {
  activePreset.value = preset.key
  if (preset.base_url) form.base_url = preset.base_url
  if (preset.model) form.model = preset.model
  models.value = []
  error.value = ''
  testResult.value = null
}

async function loadModels(silent = false) {
  if (!form.base_url.trim()) {
    if (!silent) error.value = '请先填写接口地址。'
    return
  }
  modelLoading.value = true
  if (!silent) {
    error.value = ''
    notice.value = ''
  }
  try {
    const res = await listModels({
      provider: form.provider,
      base_url: form.base_url.trim(),
      api_key: form.api_key.trim(),
    })
    models.value = res.models || []
    if (form.provider === 'ollama' && !form.model && models.value.length) {
      form.model = models.value[0]
    }
    if (!silent) {
      notice.value = models.value.length
        ? `已读取 ${models.value.length} 个可用模型。`
        : '接口连接成功，但没有返回模型列表。'
    }
  } catch (e) {
    if (!silent) error.value = e.message
  } finally {
    modelLoading.value = false
  }
}

function buildPayload() {
  return {
    provider: form.provider,
    base_url: form.base_url.trim(),
    api_key: form.api_key.trim(),
    model: form.model.trim(),
    temperature: Number(form.temperature),
    max_tokens: Number(form.max_tokens),
    timeout_s: Number(form.timeout_s),
  }
}

function validate() {
  if (!form.base_url.trim()) return '请填写接口地址。'
  if (!/^https?:\/\//.test(form.base_url.trim())) return '接口地址需以 http:// 或 https:// 开头。'
  if (!form.model.trim()) return '请填写或选择模型名称。'
  return ''
}

async function test() {
  const message = validate()
  if (message) {
    error.value = message
    return
  }
  testing.value = true
  error.value = ''
  notice.value = ''
  testResult.value = null
  try {
    testResult.value = await testSettings(buildPayload())
  } catch (e) {
    error.value = e.message
  } finally {
    testing.value = false
  }
}

async function save() {
  const message = validate()
  if (message) {
    error.value = message
    return
  }
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const res = await saveSettings(buildPayload())
    current.value = res.llm
    testResult.value = null
    notice.value = `已保存并立即切换到 ${res.llm.provider === 'ollama' ? '本地' : '云端'}模型「${res.llm.model}」。`
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

function notifyPayload() {
  return {
    enabled: notify.enabled,
    webhook_url: notify.webhook_url.trim(),
    events: [...notify.events],
  }
}

function validateNotify() {
  const url = notify.webhook_url.trim()
  if (notify.enabled && !url) return '启用推送前请填写 Webhook 地址。'
  if (url && !/^https?:\/\//.test(url)) return 'Webhook 地址需以 http:// 或 https:// 开头。'
  if (!notify.events.length) return '请至少选择一个推送时机。'
  return ''
}

async function saveNotifySettings() {
  const message = validateNotify()
  notifyError.value = message
  notifyResult.value = null
  if (message) return
  notifySaving.value = true
  try {
    const res = await saveNotify(notifyPayload())
    savedNotify.value = res.notify
    Object.assign(notify, res.notify)
    notifyResult.value = { message: res.notify.enabled ? '推送设置已保存并启用。' : '推送设置已保存（当前未启用）。' }
  } catch (e) {
    notifyError.value = e.message
  } finally {
    notifySaving.value = false
  }
}

async function testNotifySettings() {
  const message = validateNotify()
  notifyError.value = message
  notifyResult.value = null
  if (message) return
  notifyTesting.value = true
  try {
    notifyResult.value = await testNotify(notifyPayload())
  } catch (e) {
    notifyError.value = e.message
  } finally {
    notifyTesting.value = false
  }
}

async function loadRetentionStats() {
  try {
    const res = await getRetentionStats()
    retentionStats.value = {
      tasks: res.tasks || 0,
      documents: res.documents || 0,
      cutoff: res.cutoff || '',
    }
  } catch (e) {
    retentionError.value = e.message
  }
}

function validateRetention() {
  const days = Number(retention.days)
  if (!Number.isFinite(days) || days < 1 || days > 3650) {
    return '保留天数需在 1 到 3650 之间。'
  }
  return ''
}

async function saveRetentionSettings() {
  const message = validateRetention()
  retentionError.value = message
  retentionResult.value = null
  if (message) return
  retentionSaving.value = true
  try {
    const res = await saveRetention({ enabled: retention.enabled, days: Number(retention.days) })
    Object.assign(retention, res.retention)
    retentionStats.value = {
      tasks: res.tasks || 0,
      documents: res.documents || 0,
      cutoff: res.cutoff || '',
    }
    retentionResult.value = {
      message: res.retention.enabled
        ? `保留策略已保存：超过 ${res.retention.days} 天的采集数据将在下次启动时自动清理。`
        : '保留策略已保存（当前未启用，不会自动清理）。',
    }
  } catch (e) {
    retentionError.value = e.message
  } finally {
    retentionSaving.value = false
  }
}

async function cleanupNow() {
  const ok = await confirmDialog({
    title: '立即清理过期数据',
    message: `将删除早于 ${retentionStats.value.cutoff || '保留期限'} 的已结束任务及其同步文档，手动文档不受影响。此操作不可撤销。`,
    confirmText: '清理',
    danger: true,
  })
  if (!ok) return
  retentionCleaning.value = true
  retentionError.value = ''
  retentionResult.value = null
  try {
    const res = await cleanupRetention()
    retentionResult.value = {
      message: `已清理 ${res.removed.tasks} 个任务、${res.removed.documents} 条文档。`,
    }
    await loadRetentionStats()
  } catch (e) {
    retentionError.value = e.message
  } finally {
    retentionCleaning.value = false
  }
}

onMounted(async () => {
  try {
    const res = await getSettings()
    current.value = res.llm
    settingsFile.value = res.settings_file || ''
    Object.assign(form, {
      provider: res.llm.provider || 'ollama',
      base_url: res.llm.base_url || '',
      api_key: res.llm.api_key || '',
      model: res.llm.model || '',
      temperature: res.llm.temperature ?? 0,
      max_tokens: res.llm.max_tokens ?? 4096,
      timeout_s: res.llm.timeout_s ?? 120,
    })
    if (form.provider === 'ollama') loadModels(true)

    if (res.notify) {
      savedNotify.value = res.notify
      Object.assign(notify, {
        enabled: Boolean(res.notify.enabled),
        webhook_url: res.notify.webhook_url || '',
        events: Array.isArray(res.notify.events) ? res.notify.events : ['success', 'partial', 'failed'],
      })
    }

    if (res.retention) {
      Object.assign(retention, {
        enabled: Boolean(res.retention.enabled),
        days: Number(res.retention.days) || 90,
      })
    }
    loadRetentionStats()

    const serverTheme = res.ui && res.ui.theme
    if (serverTheme && serverTheme !== themePref.value) {
      themePref.value = serverTheme
      resolvedTheme.value = applyTheme(serverTheme)
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="page settings-page">
    <div v-if="current" class="page-toolbar">
      <span class="settings-current">
        当前模型
        <strong>{{ current.provider === 'ollama' ? '本地 · ' : '云端 · ' }}{{ current.model }}</strong>
      </span>
    </div>

    <div v-if="error" class="alert error">{{ error }}</div>
    <div v-if="notice" class="alert info">{{ notice }}</div>

    <section v-if="!loading" class="card settings-card">
      <div class="settings-head">
        <h2>模型服务</h2>
        <p>支持本地 Ollama 与 OpenAI 兼容接口；填入地址、密钥和模型名即可。</p>
      </div>

      <div class="provider-switch">
        <button
          type="button"
          class="provider-chip"
          :class="{ active: form.provider === 'ollama' }"
          @click="setProvider('ollama')"
        >
          <span class="provider-dot local" />
          本地 Ollama
        </button>
        <button
          type="button"
          class="provider-chip"
          :class="{ active: form.provider === 'openai' }"
          @click="setProvider('openai')"
        >
          <span class="provider-dot cloud" />
          OpenAI 兼容接口
        </button>
      </div>

      <div v-if="form.provider === 'openai'" class="preset-bar">
        <span class="preset-label">快速选择：</span>
        <button
          v-for="preset in PRESETS"
          :key="preset.key"
          type="button"
          class="preset-chip"
          :class="{ active: activePreset === preset.key }"
          @click="applyPreset(preset)"
        >
          {{ preset.name }}
        </button>
      </div>

      <div class="settings-grid">
        <label class="field">
          <span>接口地址（Base URL）</span>
          <input v-model="form.base_url" spellcheck="false" placeholder="http://127.0.0.1:11434/v1" />
          <small v-if="form.provider === 'ollama'">Ollama 默认地址为 http://127.0.0.1:11434/v1</small>
          <small v-else>填写兼容 OpenAI Chat Completions 的接口地址</small>
        </label>

        <label class="field">
          <span>模型名称</span>
          <div class="model-row">
            <input
              v-model="form.model"
              spellcheck="false"
              list="model-options"
              :placeholder="form.provider === 'ollama' ? '例如 qwen3-8b-16k' : '例如 gpt-4o-mini'"
            />
            <button
              type="button"
              class="btn ghost small"
              :disabled="modelLoading"
              @click="loadModels(false)"
            >
              {{ modelLoading ? '读取中……' : '读取模型' }}
            </button>
          </div>
          <datalist id="model-options">
            <option v-for="name in models" :key="name" :value="name" />
          </datalist>
          <small v-if="models.length">已发现 {{ models.length }} 个模型，可从下拉选择或手动输入。</small>
        </label>

        <label class="field settings-key">
          <span>API Key</span>
          <div class="model-row">
            <input
              v-model="form.api_key"
              :type="showKey ? 'text' : 'password'"
              spellcheck="false"
              :placeholder="form.provider === 'ollama' ? '本地 Ollama 可留空' : 'sk-...'"
              autocomplete="off"
            />
            <button type="button" class="btn ghost small" @click="showKey = !showKey">
              {{ showKey ? '隐藏' : '显示' }}
            </button>
          </div>
          <small v-if="presetHint">{{ presetHint }}</small>
          <small v-else>密钥仅保存在本机 data/settings.json</small>
        </label>
      </div>

      <details class="settings-advanced">
        <summary>高级参数</summary>
        <div class="settings-grid three">
          <label class="field">
            <span>Temperature</span>
            <input v-model.number="form.temperature" type="number" min="0" max="2" step="0.1" />
            <small>0 更稳定，适合结构化采集</small>
          </label>
          <label class="field">
            <span>最大输出 Tokens</span>
            <input v-model.number="form.max_tokens" type="number" min="64" max="32768" step="64" />
            <small>模型单次回复上限</small>
          </label>
          <label class="field">
            <span>超时时间（秒）</span>
            <input v-model.number="form.timeout_s" type="number" min="10" max="600" step="10" />
            <small>单次模型调用最长等待</small>
          </label>
        </div>
      </details>

      <div v-if="testResult" class="settings-result ok">
        连接正常 · {{ testResult.latency_ms }} ms · {{ testResult.provider }} / {{ testResult.model }}
        <span v-if="testResult.reply">· 返回「{{ testResult.reply }}」</span>
      </div>

      <div class="settings-actions">
        <button type="button" class="btn ghost" :disabled="testing || saving" @click="test">
          {{ testing ? '测试中……' : '测试连接' }}
        </button>
        <button type="button" class="btn primary" :disabled="saving || testing" @click="save">
          {{ saving ? '保存中……' : '保存并启用' }}
        </button>
      </div>

      <p class="settings-note">
        保存后立即生效；正在运行的任务会继续使用开始时的模型。配置文件：<code>{{ settingsFile || 'data/settings.json' }}</code>
      </p>
    </section>

    <section class="card settings-card">
      <div class="settings-head">
        <h2>外观</h2>
        <p>主题只保存在本机浏览器，切换后立即生效。</p>
      </div>
      <div class="theme-options">
        <button
          v-for="opt in THEME_OPTIONS"
          :key="opt.key"
          type="button"
          class="theme-option"
          :class="{ active: themePref === opt.key }"
          @click="chooseTheme(opt.key)"
        >
          <span class="theme-preview" :class="`preview-${opt.key}`">
            <i /><i /><i />
          </span>
          <strong>{{ opt.label }}</strong>
          <small>{{ opt.desc }}</small>
          <span v-if="opt.key === themePref" class="theme-check">当前</span>
        </button>
      </div>
      <p class="settings-note">
        当前实际使用：{{ resolvedTheme === 'dark' ? '深色' : '浅色' }}（<code>{{ themePref }}</code>）
      </p>
    </section>

    <section class="card settings-card">
      <div class="settings-head">
        <h2>任务推送</h2>
        <p>任务结束时向 Webhook 发送 JSON 消息，可接入飞书/钉钉机器人、n8n 或其他自动化流程。</p>
      </div>

      <label class="notify-toggle">
        <input v-model="notify.enabled" type="checkbox" />
        <span>
          <strong>启用推送</strong>
          <small>{{ notify.enabled ? '任务结束后自动发送消息' : '当前不会发送任何消息' }}</small>
        </span>
      </label>

      <label class="field">
        <span>Webhook 地址</span>
        <input
          v-model="notify.webhook_url"
          spellcheck="false"
          placeholder="https://example.com/webhook"
        />
        <small>接收端需支持 POST JSON；消息包含任务描述、状态、条目数、错误信息与时间。</small>
      </label>

      <div class="notify-events">
        <span class="preset-label">推送时机：</span>
        <label
          v-for="ev in NOTIFY_EVENTS"
          :key="ev.key"
          class="event-chip"
          :class="{ active: notify.events.includes(ev.key) }"
        >
          <input v-model="notify.events" type="checkbox" :value="ev.key" />
          {{ ev.label }}
        </label>
      </div>

      <div v-if="notifyError" class="alert error">{{ notifyError }}</div>
      <div v-if="notifyResult" class="settings-result ok">{{ notifyResult.message }}</div>

      <div class="settings-actions">
        <button
          type="button"
          class="btn ghost"
          :disabled="notifyTesting || notifySaving"
          @click="testNotifySettings"
        >
          {{ notifyTesting ? '发送中……' : '测试推送' }}
        </button>
        <button
          type="button"
          class="btn primary"
          :disabled="notifySaving || notifyTesting"
          @click="saveNotifySettings"
        >
          {{ notifySaving ? '保存中……' : '保存推送设置' }}
        </button>
      </div>

      <p class="settings-note">
        推送失败只记录到系统日志，不影响采集任务；定时任务使用同一份推送配置。
        <span v-if="savedNotify && savedNotify.enabled">当前已启用。</span>
      </p>
    </section>

    <section class="card settings-card">
      <div class="settings-head">
        <h2>数据保留</h2>
        <p>超过保留期限的采集任务及其同步文档会在服务启动时自动清理，手动文档始终保留。</p>
      </div>

      <label class="notify-toggle">
        <input v-model="retention.enabled" type="checkbox" />
        <span>
          <strong>启用自动清理</strong>
          <small>
            {{
              retention.enabled
                ? `每次启动清理超过 ${retention.days} 天的采集数据`
                : '当前不会自动删除任何数据'
            }}
          </small>
        </span>
      </label>

      <label class="field">
        <span>保留天数</span>
        <input v-model.number="retention.days" type="number" min="1" max="3650" step="1" />
        <small>可设置 1 到 3650 天；仅清理已结束（成功 / 部分完成 / 失败）的采集。</small>
      </label>

      <p class="settings-note">
        当前可清理：{{ retentionStats.tasks }} 个任务 · {{ retentionStats.documents }} 条文档
        <span v-if="retentionStats.cutoff">（截止 {{ retentionStats.cutoff.replace('T', ' ') }}）</span>
      </p>

      <div v-if="retentionError" class="alert error">{{ retentionError }}</div>
      <div v-if="retentionResult" class="settings-result ok">{{ retentionResult.message }}</div>

      <div class="settings-actions">
        <button
          type="button"
          class="btn ghost danger"
          :disabled="retentionCleaning || retentionSaving"
          @click="cleanupNow"
        >
          {{ retentionCleaning ? '清理中……' : '立即清理' }}
        </button>
        <button
          type="button"
          class="btn primary"
          :disabled="retentionSaving || retentionCleaning"
          @click="saveRetentionSettings"
        >
          {{ retentionSaving ? '保存中……' : '保存保留策略' }}
        </button>
      </div>
    </section>
  </section>
</template>
