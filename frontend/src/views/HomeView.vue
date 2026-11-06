<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { createTask, generateSchema, getTask } from '../api'
import ResultDetail from '../components/ResultDetail.vue'

const RUNNING = ['pending', 'running']
const TASK_KEY = 'ac.home.task-id'

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

onMounted(() => {
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

onBeforeUnmount(stopPolling)
</script>

<template>
  <section class="page">
    <header class="page-head">
      <h1>智能体爬虫</h1>
      <p class="sub">
        填写目标描述与起始网址，AI 自动识别采集字段并完成网页抓取，输出结构化数据。
      </p>
    </header>

    <form class="card form-card" @submit.prevent="submit">
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

      <div class="field">
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
        <span v-if="task" class="hint">任务 ID：{{ task.id.slice(0, 8) }}</span>
      </div>
    </form>

    <ResultDetail v-if="task" :task="task" />
  </section>
</template>
