<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  steps: { type: Array, default: () => [] },
  status: { type: String, default: 'idle' },
  totalMs: { type: Number, default: 0 },
  createdAt: { type: String, default: '' },
})

const RUNNING = ['pending', 'running']
const TOOL_LABEL = {
  launch: '启动浏览器',
  navigate: '打开页面',
  fetch: '刷新页面',
  click: '点击元素',
  scroll: '滚动页面',
  extract: '提取数据',
  recipe: '配方直采',
  finish: '完成采集',
}
// 无任务时展示的阶段占位，让链路形态始终可见
const IDLE_STAGES = ['启动浏览器', '打开页面', '智能体决策', '页面动作', '提取数据', '完成采集']

const track = ref(null)
const now = ref(Date.now())
let ticker = null

const isRunning = computed(() => RUNNING.includes(props.status))
const isDone = computed(() => props.status === 'success')
const isFailed = computed(() => props.status === 'failed' || props.status === 'partial')

const stateText = computed(() => {
  if (isRunning.value) return props.status === 'pending' ? '排队等待' : '运行中'
  if (isDone.value) return '已完成'
  if (isFailed.value) return '已结束'
  return '等待任务'
})

function formatMs(value) {
  const n = Number(value)
  if (!Number.isFinite(n) || n <= 0) return '—'
  return n >= 1000 ? `${(n / 1000).toFixed(1)} 秒` : `${Math.round(n)} 毫秒`
}

function shortUrl(url) {
  if (!url) return '当前页面'
  const text = String(url).replace(/^https?:\/\//, '')
  return text.length > 42 ? `${text.slice(0, 40)}…` : text
}

function nodeDetail(step) {
  const args = step.args || {}
  switch (step.tool) {
    case 'launch':
      return '拉起 Chromium 实例'
    case 'navigate':
      return shortUrl(args.url)
    case 'fetch':
      return '重新加载当前页面'
    case 'click':
      return args.selector ? `目标 ${args.selector}` : '点击页面元素'
    case 'scroll':
      return `滚动 ${args.times || 1} 次以加载更多`
    case 'extract':
      return step.items != null ? `累计 ${step.items} 条` : '解析页面结构化数据'
    case 'recipe':
      return `${args.items_path || args.item_selector || '已保存配方'}${step.items != null ? ` · ${step.items} 条` : ''}`
    case 'finish':
      return step.items != null ? `最终 ${step.items} 条` : '收尾并校验结果'
    default:
      return ''
  }
}

const nodes = computed(() => {
  if (!props.steps.length) {
    return IDLE_STAGES.map((title, i) => ({
      key: `idle-${i}`,
      title,
      detail: '',
      state: 'waiting',
      ms: 0,
      time: '',
    }))
  }

  const list = props.steps.map((step, i) => ({
    key: `s-${i}-${step.step}-${step.tool}`,
    title: TOOL_LABEL[step.tool] || step.tool || '执行动作',
    detail: nodeDetail(step),
    state: 'done',
    ms: step.total_ms,
    time: step.time || '',
  }))

  if (isRunning.value) {
    list.push({
      key: 'thinking',
      title: '智能体决策',
      detail: '分析页面快照，规划下一步动作',
      state: 'active',
      ms: 0,
      time: '',
    })
  } else if (props.status === 'pending') {
    list.push({
      key: 'queued',
      title: '等待执行',
      detail: '任务已创建，等待浏览器就绪',
      state: 'active',
      ms: 0,
      time: '',
    })
  }
  return list
})

const itemCount = computed(() => {
  let count = 0
  for (const step of props.steps) {
    if (step.items != null) count = Math.max(count, Number(step.items) || 0)
  }
  return count
})

const elapsedText = computed(() => {
  if (props.totalMs > 0) return formatMs(props.totalMs)
  if (isRunning.value && props.createdAt) {
    const start = Date.parse(props.createdAt)
    if (Number.isFinite(start)) return formatMs(Math.max(0, now.value - start))
  }
  return '—'
})

function barWidth(node) {
  if (node.state === 'done') return '100%'
  if (node.state === 'active') return '100%'
  return '0%'
}

function syncTicker() {
  if (ticker) {
    clearInterval(ticker)
    ticker = null
  }
  if (isRunning.value) ticker = setInterval(() => (now.value = Date.now()), 1000)
}

watch(isRunning, syncTicker)
watch(
  () => nodes.value.length,
  async () => {
    if (!props.steps.length) return
    await nextTick()
    if (track.value) track.value.scrollLeft = track.value.scrollWidth
  },
)

onMounted(() => {
  syncTicker()
  if (props.steps.length && track.value) track.value.scrollLeft = track.value.scrollWidth
})

onBeforeUnmount(() => {
  if (ticker) clearInterval(ticker)
})
</script>

<template>
  <section class="agent-chain" :class="{ running: isRunning, finished: !isRunning && steps.length }">
    <header class="chain-head">
      <div class="chain-title">
        <span v-if="isRunning" class="chain-live" />
        <h2>智能体决策采集链路</h2>
        <span class="chain-state" :class="{ live: isRunning }">{{ stateText }}</span>
      </div>
      <div class="chain-stats">
        <span class="chain-chip">{{ nodes.length }} 个节点</span>
        <span v-if="itemCount" class="chain-chip">累计 {{ itemCount }} 条</span>
        <span class="chain-chip strong">{{ elapsedText }}</span>
      </div>
    </header>

    <div ref="track" class="chain-track">
      <article
        v-for="node in nodes"
        :key="node.key"
        class="chain-node"
        :class="node.state"
      >
        <div class="node-top">
          <span class="node-dot" />
          <span class="node-state">
            {{ node.state === 'done' ? '完成' : node.state === 'active' ? '运行中' : '等待中' }}
          </span>
        </div>
        <div class="node-title">{{ node.title }}</div>
        <div class="node-detail">{{ node.detail || '——' }}</div>
        <div class="node-foot">
          <span class="node-ms">{{ formatMs(node.ms) }}</span>
          <span v-if="node.time" class="node-time">{{ node.time }}</span>
        </div>
        <div class="node-bar"><i :style="{ width: barWidth(node) }" /></div>
      </article>
    </div>
  </section>
</template>
