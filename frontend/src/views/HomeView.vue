<script setup>
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { createTask, getTask } from '../api'
import ResultDetail from '../components/ResultDetail.vue'

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
const RUNNING = ['pending', 'running']

const form = reactive({ task_desc: '', start_url: '', schema_json: DEFAULT_SCHEMA })
const task = ref(null)
const submitting = ref(false)
const error = ref('')
let timer = null

const running = computed(() => !!task.value && RUNNING.includes(task.value.status))
const busy = computed(() => submitting.value || running.value)

function stopPolling() {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
}

async function refresh(id) {
  try {
    task.value = await getTask(id)
    if (RUNNING.includes(task.value.status)) {
      timer = setTimeout(() => refresh(id), 2000)
    }
  } catch (e) {
    error.value = e.message
    stopPolling()
  }
}

async function submit() {
  error.value = ''
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

  submitting.value = true
  stopPolling()
  try {
    const res = await createTask({
      task_desc: form.task_desc.trim(),
      start_url: form.start_url.trim(),
      schema_json: form.schema_json.trim(),
    })
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
      <p class="sub">输入目标描述、起始网址与字段 Schema，AI 智能体自动完成网页采集与结构化输出。</p>
    </header>

    <form class="card form-card" @submit.prevent="submit">
      <div class="grid-2">
        <label class="field">
          <span>目标描述</span>
          <input
            v-model="form.task_desc"
            :disabled="busy"
            placeholder="例：抓取某图书网站「计算机」分类下前 10 本书的书名与价格"
          />
        </label>
        <label class="field">
          <span>起始网址</span>
          <input v-model="form.start_url" :disabled="busy" placeholder="https://example.com/books" />
        </label>
      </div>

      <label class="field">
        <span>字段 Schema（JSON）</span>
        <textarea v-model="form.schema_json" :disabled="busy" rows="9" spellcheck="false" />
        <small>支持标准 JSON Schema，也支持简单键值映射，如 {"title": "string"}</small>
      </label>

      <div v-if="error" class="alert error">{{ error }}</div>

      <div class="actions">
        <button type="submit" class="btn primary" :disabled="busy">
          {{ busy ? '爬取中……' : '开始爬取' }}
        </button>
        <span v-if="task" class="hint">任务 ID：{{ task.id.slice(0, 8) }}</span>
      </div>
    </form>

    <ResultDetail v-if="task" :task="task" />
  </section>
</template>
