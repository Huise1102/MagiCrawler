<script setup>
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'

const faqs = [
  {
    id: 'login-wall',
    tag: '采集失败',
    question: '采集结果为 0 条，或提示“部分完成”，页面看起来是空壳怎么办？',
    summary: '最常见的原因是页面需要登录、验证码或触发了平台风控。',
    detail: [
      '典型例子是抖音：未登录时个人主页正文只有导航和登录入口，智能体拿到的是空壳页面，因此采不到数据。系统不会绕过登录、验证码或任何访问控制，这是合规底线。',
      '可以先确认页面在无痕模式下是否需要登录：如果必须登录才能看到内容，智能体无法直接采集。',
    ],
    steps: [
      '换一个不需要登录的公开页面作为起始网址，例如公开榜单、搜索列表或文章详情页。',
      '在“系统日志”里查看最后一次任务的失败原因，确认是登录墙、风控还是页面结构问题。',
      '如果只是部分条目缺失，可降低目标数量，或在目标描述中限定“只采集列表中已公开显示的内容”。',
    ],
  },
  {
    id: 'partial',
    tag: '采集失败',
    question: '任务显示“部分完成”，但目标里写了采集 20 条，实际只有 8 条？',
    summary: '通常受页面翻页上限、加载延迟或部分详情不可访问影响。',
    detail: [
      '“部分完成”表示已经采集到数据，但没有达到目标数量。系统会在结果说明里记录原因，可以在“历史任务”或“系统日志”中查看。',
      '如果目标使用中文数字（如“前五个视频”），系统已支持识别；仍建议写成阿拉伯数字，例如“前 5 条”，避免歧义。',
    ],
    steps: [
      '确认列表页是否可以持续翻页，或滚动到底部是否还有“加载更多”。',
      '适当增加“等待时间”或在目标描述里写清“滚动加载，直到出现 N 条”。',
      '把目标数量调低到页面实际可提供的范围，再重新采集。',
    ],
  },
  {
    id: 'missing-fields',
    tag: '数据问题',
    question: '采集结果字段缺失，或者价格、时长被采成了文字怎么办？',
    summary: '字段 Schema 的描述越明确，模型提取越稳定。',
    detail: [
      '价格、数量、时长等字段建议在 Schema 中声明为 number，并在 description 里写明单位与期望格式，例如“价格（数字，单位元）”。',
      '中文单位换算（万、亿）、货币符号、时长格式（分钟 / 小时）会在入库前统一处理，但仍建议在目标描述里说明。',
    ],
    steps: [
      '在“智能采集”页编辑字段 Schema，补充清晰的字段描述。',
      '在目标描述中写出数量与单位，例如“采集书名、价格（元）、评分（数字）”。',
      '重新采集后，用“可视化”页确认数字字段是否可用于图表。',
    ],
  },
  {
    id: 'model',
    tag: '模型与设置',
    question: '提示模型调用失败、超时，或者一直卡在“识别字段”？',
    summary: '先确认模型服务可用，再检查地址、密钥与模型名是否匹配。',
    detail: [
      '本地 Ollama 需要先启动服务并拉取对应模型；云端服务需要正确的 Base URL 与 API Key。',
      '保存设置后立即对新任务生效，正在运行的任务会继续使用启动时的模型。',
    ],
    steps: [
      '打开“设置”页，点击“测试连接”，确认接口与模型可用。',
      '本地模型：确认 Ollama 已运行（默认 127.0.0.1:11434），并点击“读取本地模型”选择已下载的模型。',
      '云端模型：确认 Base URL 以 /v1 结尾（如 DeepSeek 为 https://api.deepseek.com），API Key 有效且余额充足。',
      '若模型响应慢，可适当调大“超时时间”，或换用更小的模型。',
    ],
  },
  {
    id: 'schedule',
    tag: '任务与存储',
    question: '定时任务没有按时间运行？',
    summary: '调度依赖后端常驻；停机期间错过的运行点不会补跑。',
    detail: [
      '后端服务必须在运行，定时调度才会触发。服务重启后会重新计算下次运行时间，错过的任务不会集中补跑。',
    ],
    steps: [
      '在“定时任务”页确认开关已打开，并查看“下次运行”时间。',
      '确认后端 8000 端口服务仍在运行。',
      '点击“立即运行”验证任务配置本身是否正常。',
    ],
  },
  {
    id: 'browser',
    tag: '采集失败',
    question: '浏览器启动失败，或页面一直打不开？',
    summary: '多为浏览器内核或网络环境问题。',
    detail: [
      '项目优先复用系统 Edge / Chrome，无需额外下载 Chromium。若系统浏览器缺失或被安全软件拦截，可能出现启动失败。',
    ],
    steps: [
      '确认本机已安装 Microsoft Edge 或 Google Chrome，并能在普通模式下正常打开网页。',
      '在 .env 中设置 BROWSER_CHANNEL=msedge 或 chrome，然后重启后端。',
      '关闭占用浏览器的调试实例，并检查代理、网络是否能访问目标站点。',
    ],
  },
  {
    id: 'documents',
    tag: '任务与存储',
    question: '文档库里的批次、子库是怎么工作的？删除后会恢复吗？',
    summary: '批次对应一次采集；删除批次后不会自动从旧结果回填。',
    detail: [
      '同一 task_id 的采集结果会归为一个批次；手动新建的文档单独成组。批次可通过右侧“归属”整体移动到子库。',
      '删除批次会同时删除该次采集归档的文档，且系统会标记该任务已同步，之后不会再次自动回填；原始任务记录仍保留在“历史任务”中。',
      '删除子库时，其中的文档会自动退回默认库；默认库不可删除。',
    ],
    steps: [
      '在“文档库”左栏切换“全部 / 默认库 / 子库”查看对应内容。',
      '需要保留数据时优先使用“导出 CSV / JSON”，再执行删除。',
    ],
  },
  {
    id: 'export',
    tag: '数据问题',
    question: '导出的 CSV 用 Excel 打开出现乱码？',
    summary: 'CSV 已带 UTF-8 BOM，少数表格软件仍需手动指定编码。',
    detail: [
      '导出 CSV 时已使用 utf-8-sig 编码，绝大多数 Excel 可直接识别。若仍乱码，可在 Excel 的“数据 → 从文本/CSV”中手动选择 UTF-8。',
    ],
    steps: [
      '改用 JSON 或 Excel（xlsx）格式导出。',
      '或在 Excel 导入时选择“UTF-8”编码。',
    ],
  },
  {
    id: 'port',
    tag: '任务与存储',
    question: '启动后端提示 8000 端口被占用？',
    summary: '通常是上一个服务实例没有退出。',
    steps: [
      '在任务管理器结束旧的 python.exe / uvicorn 进程，或修改 .env 中的 BACKEND_PORT。',
      '重新启动后访问 http://127.0.0.1:8000 检查页面是否正常打开。',
    ],
  },
]

const query = ref('')
const openId = ref('')

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return faqs
  return faqs.filter((item) =>
    `${item.tag} ${item.question} ${item.summary} ${(item.detail || []).join(' ')}`
      .toLowerCase()
      .includes(q),
  )
})

function toggle(id) {
  openId.value = openId.value === id ? '' : id
}
</script>

<template>
  <section class="page help-page">
    <header class="page-head row">
      <div>
        <h1>帮助中心</h1>
        <p class="sub">常见问题的排查思路与解决办法；先看这里，大多数问题能快速定位。</p>
      </div>
      <div class="doc-actions">
        <input v-model="query" class="doc-search" placeholder="搜索问题关键词，例如 登录 / 部分完成 / 模型" />
      </div>
    </header>

    <div class="help-notice">
      <span class="help-notice-icon">!</span>
      <div>
        <strong>合规提示</strong>
        <p>
          系统不会绕过登录、验证码、付费墙或访问控制。遇到登录墙时（例如未登录的抖音页面），
          请改用公开可访问的页面，或调整采集目标。
        </p>
      </div>
    </div>

    <div class="help-shortcuts">
      <RouterLink to="/settings" class="help-shortcut">
        <span>模型配置</span>
        <small>接口地址、API Key、本地模型切换</small>
      </RouterLink>
      <RouterLink to="/logs" class="help-shortcut">
        <span>系统日志</span>
        <small>查看报错与失败原因</small>
      </RouterLink>
      <RouterLink to="/history" class="help-shortcut">
        <span>历史任务</span>
        <small>回看采集说明与执行耗时</small>
      </RouterLink>
    </div>

    <div class="faq-list">
      <article
        v-for="item in filtered"
        :key="item.id"
        class="faq-item"
        :class="{ open: openId === item.id }"
      >
        <button type="button" class="faq-head" @click="toggle(item.id)">
          <div class="faq-title">
            <span class="faq-tag">{{ item.tag }}</span>
            <h3>{{ item.question }}</h3>
            <p>{{ item.summary }}</p>
          </div>
          <span class="faq-caret" :class="{ open: openId === item.id }">▾</span>
        </button>
        <div v-if="openId === item.id" class="faq-body">
          <p v-for="(line, i) in item.detail" :key="i">{{ line }}</p>
          <ol v-if="item.steps && item.steps.length" class="faq-steps">
            <li v-for="step in item.steps" :key="step">{{ step }}</li>
          </ol>
        </div>
      </article>

      <div v-if="!filtered.length" class="card doc-empty">
        <p class="empty">没有找到相关问题，换个关键词试试，或到“系统日志”查看具体报错。</p>
      </div>
    </div>
  </section>
</template>
