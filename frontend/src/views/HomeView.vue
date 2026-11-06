<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

const slides = [
  {
    tag: '智能体决策',
    title: '先看懂页面，再决定下一步',
    desc: '大模型每一步都观察页面快照，自主判断该点击、翻页还是提取，并把决策过程完整记录成链路。',
    bullets: ['实时观察 DOM 快照', '自主点选、翻页与重试', '每一步决策都可回看'],
    chips: ['观察页面', '点击进入', '翻页', '提取字段'],
    accent: '#2563eb',
  },
  {
    tag: '结构化字段',
    title: '从一句话，到能分析的表格',
    desc: '只需描述想采集什么，系统自动识别字段并生成 Schema；价格、时长、数量等会统一成数字。',
    bullets: ['目标描述自动生成 Schema', '数字、货币、时长自动归一', 'CSV / JSON / Excel 多格式导出'],
    chips: ['auto_schema', 'price: 12.5', 'duration: 36', 'export.csv'],
    accent: '#7c3aed',
  },
  {
    tag: '批次归档',
    title: '每次采集，就是一颗时间胶囊',
    desc: '同一次采集的结果自动收进一个批次，展开即表格；还能新建子库，把不同批次的成果分门别类存放。',
    bullets: ['按单次采集自动分组', '子库自由新建与移动', '紧凑表格浏览与检索'],
    chips: ['批次 A · 25 条', '子库：视频素材', '详情快照', '按需导出'],
    accent: '#0891b2',
  },
  {
    tag: '定时与可视化',
    title: '采集交给计划，洞察交给图表',
    desc: '支持间隔与每日定点运行；采集完成后让大模型根据数据生成图表方案，趋势与分布一目了然。',
    bullets: ['间隔 / 每日定点调度', '模型自动生成图表方案', '任务耗时与失败原因可追踪'],
    chips: ['每日 09:00', 'ECharts', '趋势图', '耗时分析'],
    accent: '#059669',
  },
]

const current = ref(0)
let timer = null

function go(index) {
  current.value = (index + slides.length) % slides.length
}

function next() {
  go(current.value + 1)
}

function prev() {
  go(current.value - 1)
}

function startAuto() {
  stopAuto()
  timer = setInterval(next, 5200)
}

function stopAuto() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

function select(index) {
  go(index)
  startAuto()
}

onMounted(startAuto)
onBeforeUnmount(stopAuto)
</script>

<template>
  <section class="page intro-page">
    <div class="hero">
      <div class="hero-copy">
        <span class="hero-badge">
          <span class="hero-badge-dot" />
          多模型驱动 · 本地优先 · 数据留在本机
        </span>
        <h1>
          让 AI 替你读懂网页<br />
          <span class="hero-highlight">把页面变成可分析的数据</span>
        </h1>
        <p class="hero-sub">
          描述采集目标、给出起始网址，智能体会自己浏览页面、识别字段、翻页提取，
          并把每一次采集归档成结构化数据。
        </p>
        <div class="hero-actions">
          <RouterLink to="/crawl" class="btn primary large">
            开始智能采集
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
          </RouterLink>
          <RouterLink to="/help" class="btn ghost large">遇到问题？查看帮助</RouterLink>
        </div>
        <div class="hero-tags">
          <span>本地 Ollama</span>
          <span>OpenAI 兼容接口</span>
          <span>定时采集</span>
          <span>可视化分析</span>
        </div>
      </div>

      <div class="hero-carousel">
        <div class="carousel-head">
          <span class="carousel-kicker">它能做什么</span>
          <div class="carousel-buttons">
            <button type="button" aria-label="上一张" @click="select((current - 1 + slides.length) % slides.length)">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M15 18l-6-6 6-6" />
              </svg>
            </button>
            <button type="button" aria-label="下一张" @click="select((current + 1) % slides.length)">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
          </div>
        </div>

        <div class="carousel-stage">
          <article
            v-for="(slide, index) in slides"
            :key="slide.tag"
            class="hero-slide"
            :class="{ active: index === current }"
            :style="{ '--slide-accent': slide.accent }"
          >
            <span class="slide-tag">{{ slide.tag }}</span>
            <h2>{{ slide.title }}</h2>
            <p>{{ slide.desc }}</p>
            <ul class="slide-points">
              <li v-for="point in slide.bullets" :key="point">{{ point }}</li>
            </ul>
            <div class="slide-visual" aria-hidden="true">
              <div class="sv-bar">
                <i /><i /><i />
                <span>{{ slide.tag }}</span>
              </div>
              <div class="sv-body">
                <div class="sv-line w-90" />
                <div class="sv-line w-60" />
                <div class="sv-chips">
                  <span v-for="chip in slide.chips" :key="chip">{{ chip }}</span>
                </div>
              </div>
            </div>
          </article>
        </div>

        <div class="carousel-dots">
          <button
            v-for="(slide, index) in slides"
            :key="slide.tag"
            type="button"
            :class="{ active: index === current }"
            :aria-label="slide.tag"
            @click="select(index)"
          />
        </div>
      </div>
    </div>

    <section class="intro-features">
      <article class="feature-card">
        <span class="feature-icon tone-blue">01</span>
        <h3>智能体驱动</h3>
        <p>不再依赖固定选择器。模型观察页面快照，自主决定点击、翻页与提取，页面改版也能重新探索。</p>
      </article>
      <article class="feature-card">
        <span class="feature-icon tone-purple">02</span>
        <h3>自动字段识别</h3>
        <p>一句目标描述即可生成 Schema；中文单位、货币与时长在入库前统一转换为数字。</p>
      </article>
      <article class="feature-card">
        <span class="feature-icon tone-cyan">03</span>
        <h3>按批次归档</h3>
        <p>每次采集自动成为一个批次，支持自建子库与整批移动，检索、导出、删除都不再零散。</p>
      </article>
      <article class="feature-card">
        <span class="feature-icon tone-green">04</span>
        <h3>定时与可视化</h3>
        <p>按间隔或每日定点自动运行；结果交给模型生成图表方案，趋势与分布直接呈现。</p>
      </article>
    </section>

  </section>
</template>
