<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  autoClassifyDocuments,
  createCategory,
  createDocument,
  deleteCategory,
  deleteDocument,
  exportUrl,
  listCategories,
  listDocuments,
  updateCategory,
  updateDocument,
} from '../api'

import { confirmDialog } from '../composables/useConfirm'

const PAGE_SIZE = 24

const docs = ref([])
const categories = ref([])
const loading = ref(false)
const error = ref('')
const query = ref('')
const activeCategory = ref('')
const visibleCount = ref(PAGE_SIZE)

const selected = ref(null)
const editor = ref(null)
const showCategories = ref(false)
const categoryDrafts = ref([])
const newCategoryName = ref('')
const categoryError = ref('')
const classifyBusy = ref(false)
const notice = ref('')

const TITLE_PATTERN = /title|name|标题|名称|书名|姓名|article|headline|产品/i
const LINK_PATTERN = /url|link|href|网址|链接|地址/i

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [docRes, catRes] = await Promise.all([listDocuments(), listCategories()])
    docs.value = docRes.items || []
    categories.value = catRes.items || []
    if (activeCategory.value && activeCategory.value !== '__none') {
      if (!categories.value.some((c) => c.id === activeCategory.value)) activeCategory.value = ''
    }
    visibleCount.value = PAGE_SIZE
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

const counts = computed(() => {
  const map = {}
  for (const d of docs.value) {
    const key = d.category_id || ''
    map[key] = (map[key] || 0) + 1
  }
  return map
})

const uncategorizedCount = computed(() => counts.value[''] || 0)

function categoryCount(id) {
  return counts.value[id] || 0
}

const filteredDocs = computed(() => {
  const q = query.value.trim().toLowerCase()
  return docs.value.filter((d) => {
    if (activeCategory.value === '__none') {
      if (d.category_id) return false
    } else if (activeCategory.value && d.category_id !== activeCategory.value) {
      return false
    }
    if (!q) return true
    return `${d.title || ''} ${d.task_desc || ''} ${JSON.stringify(d.fields)}`.toLowerCase().includes(q)
  })
})

const visibleDocs = computed(() => filteredDocs.value.slice(0, visibleCount.value))

function isLinkField(key, value) {
  return typeof value === 'string' && (/^https?:\/\//i.test(value) || LINK_PATTERN.test(key))
}

function titleOf(doc) {
  if (doc.title && doc.title.trim()) return doc.title.trim()
  for (const [k, v] of Object.entries(doc.fields)) {
    if (TITLE_PATTERN.test(k) && typeof v === 'string' && v.trim()) return v.trim()
  }
  return '未命名文档'
}

function linkOf(doc) {
  if (doc.source_url) return doc.source_url
  for (const [k, v] of Object.entries(doc.fields)) {
    if (isLinkField(k, v)) return v
  }
  return ''
}

function previewFields(doc) {
  return Object.entries(doc.fields)
    .filter(
      ([k, v]) =>
        v !== null &&
        v !== undefined &&
        v !== '' &&
        !isLinkField(k, v) &&
        !TITLE_PATTERN.test(k),
    )
    .slice(0, 4)
}

function formatValue(value) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

function formatTime(value) {
  return (value || '').replace('T', ' ')
}

function selectCategory(value) {
  activeCategory.value = value
  visibleCount.value = PAGE_SIZE
}

// ---------------- 文档增删改 ----------------

function openCreate() {
  editor.value = {
    mode: 'create',
    id: '',
    title: '',
    category_id: activeCategory.value && activeCategory.value !== '__none' ? activeCategory.value : '',
    source_url: '',
    fields_json: '{\n  "字段": "值"\n}',
    saving: false,
    error: '',
  }
}

function openEdit(doc) {
  editor.value = {
    mode: 'edit',
    id: doc.id,
    title: titleOf(doc),
    category_id: doc.category_id || '',
    source_url: doc.source_url || '',
    fields_json: JSON.stringify(doc.fields, null, 2),
    saving: false,
    error: '',
  }
}

async function saveDocument() {
  if (!editor.value || editor.value.saving) return
  const draft = editor.value
  draft.error = ''
  let fields
  try {
    fields = JSON.parse(draft.fields_json || '{}')
  } catch (e) {
    draft.error = `字段 JSON 格式有误：${e.message}`
    return
  }
  if (!fields || typeof fields !== 'object' || Array.isArray(fields)) {
    draft.error = '字段内容必须是 JSON 对象'
    return
  }
  draft.saving = true
  try {
    const payload = {
      title: draft.title,
      category_id: draft.category_id,
      source_url: draft.source_url,
      fields_json: JSON.stringify(fields),
    }
    if (draft.mode === 'create') await createDocument(payload)
    else await updateDocument(draft.id, payload)
    editor.value = null
    selected.value = null
    await load()
  } catch (e) {
    draft.error = e.message
  } finally {
    draft.saving = false
  }
}

async function removeDocument(doc) {
  const ok = await confirmDialog({
    title: '删除文档',
    message: `确定删除「${titleOf(doc)}」？删除后不会再次同步。`,
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return
  error.value = ''
  try {
    await deleteDocument(doc.id)
    if (selected.value && selected.value.id === doc.id) selected.value = null
    await load()
  } catch (e) {
    error.value = e.message
  }
}

// ---------------- 分类增删改 ----------------

function openCategoryManager() {
  categoryError.value = ''
  newCategoryName.value = ''
  categoryDrafts.value = categories.value.map((c) => ({ id: c.id, name: c.name, draftName: c.name }))
  showCategories.value = true
}

async function refreshDrafts() {
  categoryDrafts.value = categories.value.map((c) => ({ id: c.id, name: c.name, draftName: c.name }))
}

async function addCategory() {
  const name = newCategoryName.value.trim()
  if (!name) {
    categoryError.value = '请输入分类名称'
    return
  }
  categoryError.value = ''
  try {
    await createCategory(name)
    newCategoryName.value = ''
    await load()
    await refreshDrafts()
  } catch (e) {
    categoryError.value = e.message
  }
}

async function saveCategory(draftItem) {
  const name = draftItem.draftName.trim()
  if (!name) {
    categoryError.value = '分类名称不能为空'
    return
  }
  categoryError.value = ''
  try {
    await updateCategory(draftItem.id, name)
    await load()
    await refreshDrafts()
  } catch (e) {
    categoryError.value = e.message
  }
}

async function removeCategory(draftItem) {
  const ok = await confirmDialog({
    title: '删除分类',
    message: `删除分类「${draftItem.name}」？其下文档将变为未分类。`,
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return
  categoryError.value = ''
  try {
    await deleteCategory(draftItem.id)
    await load()
    await refreshDrafts()
  } catch (e) {
    categoryError.value = e.message
  }
}

// ---------------- 自动分类 ----------------

async function runAutoClassify() {
  if (classifyBusy.value) return
  notice.value = ''
  if (!uncategorizedCount.value) {
    notice.value = '没有未分类的文档'
    return
  }
  classifyBusy.value = true
  error.value = ''
  try {
    const res = await autoClassifyDocuments()
    const parts = []
    if (res.classified) parts.push(`已自动归类 ${res.classified} 篇文档`)
    if (res.created_categories && res.created_categories.length) {
      parts.push(`新建分类：${res.created_categories.join('、')}`)
    }
    if (res.remaining) parts.push(`仍有 ${res.remaining} 篇未归类`)
    if (res.note) parts.push(res.note)
    notice.value = parts.length ? parts.join('；') : '没有需要归类的文档'
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    classifyBusy.value = false
  }
}

// ---------------- 导出 ----------------

async function downloadExport(fmt) {
  if (!selected.value || !selected.value.task_id) return
  error.value = ''
  try {
    const res = await fetch(exportUrl(selected.value.task_id, fmt))
    if (!res.ok) throw new Error(`导出失败（${res.status}）`)
    const blob = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const matched = disposition.match(/filename="?([^";]+)"?/)
    const filename = matched ? matched[1] : `task_${selected.value.task_id.slice(0, 8)}.${fmt}`
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <section class="page">
    <header class="page-head row">
      <div>
        <h1>文档库</h1>
        <p class="sub">按分类整理采集内容，支持新增、编辑与删除</p>
      </div>
      <div class="filters">
        <input v-model="query" class="doc-search" placeholder="搜索标题、字段或任务……" />
        <button class="btn ghost" :disabled="classifyBusy || loading" @click="runAutoClassify">
          {{ classifyBusy ? '分类中……' : '自动分类' }}
        </button>
        <button class="btn primary" @click="openCreate">新建文档</button>
        <button class="btn ghost" :disabled="loading" @click="load">
          {{ loading ? '加载中……' : '刷新' }}
        </button>
      </div>
    </header>

    <div v-if="error" class="alert error">{{ error }}</div>
    <div v-if="notice" class="alert info">{{ notice }}</div>

    <div class="category-bar">
      <button
        class="cat-chip"
        :class="{ active: activeCategory === '' }"
        @click="selectCategory('')"
      >
        全部 <span class="cat-count">{{ docs.length }}</span>
      </button>
      <button
        class="cat-chip"
        :class="{ active: activeCategory === '__none' }"
        @click="selectCategory('__none')"
      >
        未分类 <span class="cat-count">{{ uncategorizedCount }}</span>
      </button>
      <button
        v-for="c in categories"
        :key="c.id"
        class="cat-chip"
        :class="{ active: activeCategory === c.id }"
        @click="selectCategory(c.id)"
      >
        {{ c.name }} <span class="cat-count">{{ categoryCount(c.id) }}</span>
      </button>
      <button class="cat-chip manage" @click="openCategoryManager">分类管理</button>
    </div>

    <p v-if="docs.length" class="doc-count">
      共 {{ filteredDocs.length }} 条内容<template v-if="filteredDocs.length !== docs.length">
        （筛选自 {{ docs.length }} 条）</template
      >
    </p>

    <div v-if="visibleDocs.length" class="doc-grid">
      <article v-for="d in visibleDocs" :key="d.id" class="doc-card" @click="selected = d">
        <div class="doc-title-row">
          <h3>{{ titleOf(d) }}</h3>
          <span v-if="d.manual" class="chip">手动</span>
        </div>
        <dl v-if="previewFields(d).length" class="doc-fields">
          <div v-for="[k, v] in previewFields(d)" :key="k" class="doc-field-row">
            <dt class="doc-key">{{ k }}</dt>
            <dd class="doc-value">{{ formatValue(v) }}</dd>
          </div>
        </dl>
        <footer class="doc-meta">
          <span class="doc-source">{{ d.task_desc || '手动创建' }}</span>
          <span class="mono">{{ formatTime(d.created_at) }}</span>
        </footer>
        <div class="doc-ops" @click.stop>
          <span class="cat-tag">{{ d.category_name || '未分类' }}</span>
          <div class="doc-ops-btns">
            <button class="btn ghost small" @click="openEdit(d)">编辑</button>
            <button class="btn ghost small danger" @click="removeDocument(d)">删除</button>
          </div>
        </div>
      </article>
    </div>

    <div v-if="!visibleDocs.length" class="card">
      <p class="empty">{{ loading ? '加载中……' : '暂无文档，先执行一次爬取或手动新建吧。' }}</p>
    </div>

    <div v-if="visibleCount < filteredDocs.length" class="doc-more">
      <button class="btn ghost" @click="visibleCount += PAGE_SIZE">
        加载更多（剩余 {{ filteredDocs.length - visibleCount }} 条）
      </button>
    </div>

    <!-- 文档详情 -->
    <div v-if="selected" class="modal-backdrop" @click.self="selected = null">
      <div class="modal">
        <div class="modal-head">
          <h2>{{ titleOf(selected) }}</h2>
          <button class="modal-close" aria-label="关闭" @click="selected = null">×</button>
        </div>
        <div class="modal-body">
          <p class="doc-detail-sub">
            <span class="chip">{{ selected.category_name || '未分类' }}</span>
            {{ selected.task_desc || '手动创建' }} · {{ formatTime(selected.created_at) }}
          </p>
          <p v-if="linkOf(selected)" class="doc-detail-link">
            <a :href="linkOf(selected)" target="_blank" rel="noreferrer">{{ linkOf(selected) }}</a>
          </p>
          <div class="doc-detail-list">
            <div v-for="[k, v] in Object.entries(selected.fields)" :key="k" class="doc-detail-row">
              <span class="doc-key">{{ k }}</span>
              <span class="doc-detail-value">{{ formatValue(v) }}</span>
            </div>
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn ghost small danger" @click="removeDocument(selected)">删除</button>
          <button class="btn ghost small" @click="openEdit(selected)">编辑</button>
          <template v-if="selected.task_id">
            <button class="btn ghost small" @click="downloadExport('json')">导出 JSON</button>
            <button class="btn ghost small" @click="downloadExport('csv')">导出 CSV</button>
          </template>
          <button class="btn small" @click="selected = null">关闭</button>
        </div>
      </div>
    </div>

    <!-- 文档新增/编辑 -->
    <div v-if="editor" class="modal-backdrop" @click.self="editor = null">
      <div class="modal">
        <div class="modal-head">
          <h2>{{ editor.mode === 'create' ? '新建文档' : '编辑文档' }}</h2>
          <button class="modal-close" aria-label="关闭" @click="editor = null">×</button>
        </div>
        <div class="modal-body">
          <label class="field">
            <span>标题</span>
            <input v-model="editor.title" placeholder="留空则自动从字段中提取" />
          </label>
          <div class="grid-2">
            <label class="field">
              <span>分类</span>
              <select v-model="editor.category_id">
                <option value="">未分类</option>
                <option v-for="c in categories" :key="c.id" :value="c.id">{{ c.name }}</option>
              </select>
            </label>
            <label class="field">
              <span>原文链接</span>
              <input v-model="editor.source_url" placeholder="https://…（可选）" />
            </label>
          </div>
          <label class="field">
            <span>字段内容（JSON 对象）</span>
            <textarea v-model="editor.fields_json" rows="10" spellcheck="false"></textarea>
          </label>
          <div v-if="editor.error" class="alert error">{{ editor.error }}</div>
        </div>
        <div class="modal-foot">
          <button class="btn ghost" @click="editor = null">取消</button>
          <button class="btn primary" :disabled="editor.saving" @click="saveDocument">
            {{ editor.saving ? '保存中……' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 分类管理 -->
    <div v-if="showCategories" class="modal-backdrop" @click.self="showCategories = false">
      <div class="modal narrow">
        <div class="modal-head">
          <h2>分类管理</h2>
          <button class="modal-close" aria-label="关闭" @click="showCategories = false">×</button>
        </div>
        <div class="modal-body">
          <div class="cat-add">
            <input v-model="newCategoryName" placeholder="新分类名称" @keyup.enter="addCategory" />
            <button class="btn small" @click="addCategory">添加</button>
          </div>
          <div v-if="categoryError" class="alert error">{{ categoryError }}</div>
          <div class="cat-list">
            <div v-for="c in categoryDrafts" :key="c.id" class="cat-row">
              <input v-model="c.draftName" />
              <button class="btn ghost small" @click="saveCategory(c)">保存</button>
              <button class="btn ghost small danger" @click="removeCategory(c)">删除</button>
            </div>
            <p v-if="!categoryDrafts.length" class="empty">还没有分类，先添加一个吧。</p>
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="showCategories = false">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>
