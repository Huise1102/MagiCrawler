<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { createTask, generateSchema, getTask } from '../api'
import AgentChain from '../components/AgentChain.vue'
import ResultDetail from '../components/ResultDetail.vue'

const RUNNING = ['pending', 'running']
const TASK_KEY = 'ac.home.task-id'
const FORM_KEY = 'ac.home.form'

const form = reactive({ task_desc: '', start_url: '', schema_json: '' })
const task = ref(null)
const submitting = ref(false)
const generating = ref(false)
const error = ref('')
const schemaHint = ref('')
const schemaEdited = ref(false)
let lastAutoDesc = ''
let timer = null

const running = computed(() => !!task.value && RUNNING.includes(task.value.status))
const busy = computed(() => submitting.value || running.value)
/** 任务已结束（成功/失败/部分完成），可一键重复采集 */
const finished = computed(() => !!task.value && !running.value)

/** 采集链路优先用实时事件，历史任务回退到轨迹记录。 */
const chainSteps = computed(() => {
  const chain = task.value?.chain
  if (Array.isArray(chain) && chain.length) return chain
  return task.value?.trajectory || []
})
const taskTotalMs = computed(() => {
  try {
    return Number(JSON.parse(task.value?.timings_json || '{}').total_ms) || 0
  } catch {
    return 0
  }
})

function stopPolling() {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
}

async function refresh(id, silent = false) {
  try {
    task.value = await getTask(id)
    if (RUNNING.includes(task.value.status)) {
      timer = setTimeout(() => refresh(id, silent), 2000)
    }
  } catch (e) {
    if (silent && e.message.includes('不存在')) {
      forgetTask()
      return
    }
    error.value = e.message
    stopPolling()
  }
}

/** 记住当前任务，切换页面或刷新后仍能在首页看到结果卡片。 */
function rememberTask(id) {
  localStorage.setItem(TASK_KEY, id)
}

function forgetTask() {
  localStorage.removeItem(TASK_KEY)
}

/** 表单内容常驻：除非主动开始下一个任务，否则一直保留。 */
function restoreForm() {
  try {
    const saved = JSON.parse(localStorage.getItem(FORM_KEY) || 'null')
    if (!saved || typeof saved !== 'object') return
    form.task_desc = saved.task_desc || ''
    form.start_url = saved.start_url || ''
    form.schema_json = saved.schema_json || ''
    if (form.schema_json) {
      schemaEdited.value = true
      lastAutoDesc = form.task_desc
    }
  } catch {
    // 存储损坏时忽略，保持空表单
  }
}

let formReady = false
watch(
  form,
  () => {
    if (!formReady) return
    localStorage.setItem(FORM_KEY, JSON.stringify({ ...form }))
  },
  { deep: true },
)

onMounted(() => {
  restoreForm()
  formReady = true
  const saved = localStorage.getItem(TASK_KEY)
  if (saved) refresh(saved, true)
})

/** 让大模型根据目标描述识别意图并生成字段 Schema。 */
async function runGenerate(auto = false) {
  if (busy.value || generating.value) return
  const desc = form.task_desc.trim()
  if (!desc) {
    if (!auto) error.value = '请先填写「目标描述」，再生成字段。'
    return
  }

  generating.value = true
  if (!auto) error.value = ''
  schemaHint.value = '正在根据目标描述识别字段……'
  try {
    const res = await generateSchema({ task_desc: desc, start_url: form.start_url.trim() })
    form.schema_json = res.schema_json
    schemaEdited.value = false
    lastAutoDesc = desc
    schemaHint.value = `已识别 ${res.field_count} 个字段，可按需微调。`
  } catch (e) {
    schemaHint.value = ''
    if (auto) {
      schemaHint.value = '自动识别失败，可点击「AI 生成字段」重试。'
    } else {
      error.value = e.message
    }
  } finally {
    generating.value = false
  }
}

/** 目标描述失焦时，若字段为空或描述已改动则自动生成。 */
function maybeAutoGenerate() {
  if (busy.value || generating.value || schemaEdited.value) return
  if (!form.task_desc.trim()) return
  if (form.schema_json.trim() && form.task_desc.trim() === lastAutoDesc) return
  runGenerate(true)
}

function onSchemaInput() {
  schemaEdited.value = true
  schemaHint.value = ''
}

async function submit() {
  error.value = ''
  if (!form.task_desc.trim() || !form.start_url.trim()) {
    error.value = '请完整填写「目标描述」与「起始网址」。'
    return
  }

  if (!form.schema_json.trim()) {
    await runGenerate()
    if (!form.schema_json.trim()) return
  }

  try {
    JSON.parse(form.schema_json)
  } catch (e) {
    error.value = `Schema 不是合法 JSON：${e.message}`
    return
  }

  submitting.value = true
  stopPolling()
  try {
    const res = await createTask({
      task_desc: form.task_desc.trim(),
      start_url: form.start_url.trim(),
      schema_json: form.schema_json.trim(),
    })
    rememberTask(res.task_id)
    await refresh(res.task_id)
  } catch (e) {
    error.value = e.message
  } finally {
    submitting.value = false
  }
}

/** 用上一次任务的配置原样重跑（成功或失败都可重测）。 */
async function replay() {
  if (!task.value || busy.value || generating.value) return
  form.task_desc = task.value.task_desc || form.task_desc
  form.start_url = task.value.start_url || form.start_url
  form.schema_json = task.value.schema_json || form.schema_json
  schemaEdited.value = true
  lastAutoDesc = form.task_desc
  schemaHint.value = ''
  await submit()
}

/** 清空当前任务与表单，开始下一个采集任务。 */
function startNewTask() {
  if (busy.value) return
  stopPolling()
  task.value = null
  error.value = ''
  schemaHint.value = ''
  schemaEdited.value = false
  lastAutoDesc = ''
  form.task_desc = ''
  form.start_url = ''
  form.schema_json = ''
  forgetTask()
  localStorage.removeItem(FORM_KEY)
}

onBeforeUnmount(stopPolling)
</script>

<template>
  <section class="page home-page">
    <AgentChain
      :steps="chainSteps"
      :status="task ? task.status : 'idle'"
      :created-at="task ? task.created_at : ''"
      :total-ms="taskTotalMs"
    />

    <div class="home-grid">
      <form class="card form-card" @submit.prevent="submit">
        <h2 class="card-title">采集任务</h2>
        <div class="grid-2">
          <label class="field">
            <span>目标描述</span>
            <input
              v-model="form.task_desc"
              :disabled="busy"
              placeholder="例：抓取某图书网站「计算机」分类下前 10 本书的书名与价格"
              @blur="maybeAutoGenerate"
            />
          </label>
          <label class="field">
            <span>起始网址</span>
            <input
              v-model="form.start_url"
              :disabled="busy"
              placeholder="https://example.com/books"
              @blur="maybeAutoGenerate"
            />
          </label>
        </div>

        <div class="field schema-field">
          <span class="label-row">
            字段 Schema（JSON）
            <button
              type="button"
              class="btn ghost small"
              :disabled="busy || generating"
              @click="runGenerate()"
            >
              {{ generating ? '识别中……' : 'AI 生成字段' }}
            </button>
          </span>
          <textarea
            v-model="form.schema_json"
            :disabled="busy || generating"
            rows="9"
            spellcheck="false"
            placeholder="填写目标描述后，大模型会自动识别字段；也可点击右上角按钮手动生成"
            @input="onSchemaInput"
          />
          <small v-if="generating">正在根据目标描述识别字段……</small>
          <small v-else>
            {{ schemaHint || '字段由大模型根据目标描述自动生成，可手动微调。' }}
          </small>
        </div>

        <div v-if="error" class="alert error">{{ error }}</div>

        <div class="actions">
          <button type="submit" class="btn primary" :disabled="busy || generating">
            {{ submitting ? '爬取中……' : generating ? '生成字段中……' : '开始爬取' }}
          </button>
          <button
            v-if="finished"
            type="button"
            class="btn ghost"
            :disabled="busy || generating"
            title="用上一次任务的配置再跑一次"
            @click="replay"
          >
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 12a9 9 0 1 1-2.64-6.36" />
              <path d="M21 3v6h-6" />
            </svg>
            重复采集
          </button>
          <button
            type="button"
            class="btn ghost"
            :disabled="running || submitting"
            :title="running ? '采集进行中，结束后可开始下一个任务' : '清空当前任务与表单，开始下一个采集任务'"
            @click="startNewTask"
          >
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 12a9 9 0 1 0 3-6.7" />
              <path d="M3 4v5h5" />
            </svg>
            下一个任务
          </button>
          <span v-if="task" class="hint">任务 ID：{{ task.id.slice(0, 8) }}</span>
        </div>
      </form>

      <ResultDetail v-if="task" :task="task" />
      <div v-else class="card result-card result-placeholder">
        <div class="result-head">
          <h2>采集结果</h2>
        </div>
        <p class="empty">暂无结果，完成一次采集后会在这里展示。</p>
      </div>
    </div>
  </section>
</template>
