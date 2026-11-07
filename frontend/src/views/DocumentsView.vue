<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  createDocument,
  createLibrary,
  deleteDocument,
  deleteDocumentBatch,
  deleteLibrary,
  exportUrl,
  listDocuments,
  listLibraries,
  moveTaskLibrary,
  updateDocument,
  updateLibrary,
} from '../api'
import { confirmDialog } from '../composables/useConfirm'

const ALL = '__all'
const MANUAL = '__manual'

const docs = ref([])
const libraries = ref([])
const activeLibrary = ref(ALL)
const query = ref('')
const loading = ref(false)
const error = ref('')
const expanded = ref([])
const selected = ref(null)

const editor = ref(null)
const libManager = ref(false)
const libDrafts = ref({})
const newLibName = ref('')
const libError = ref('')

const activeLibName = computed(() => {
  if (activeLibrary.value === ALL) return '全部'
  return libraries.value.find((l) => l.id === activeLibrary.value)?.name || '默认库'
})

function libraryName(id) {
  return libraries.value.find((l) => l.id === (id || ''))?.name || '默认库'
}

/** 按单次采集分组：task_id 相同的文档归为一个批次；已解除任务关联的按采集时间分组。 */
const batches = computed(() => {
  const q = query.value.trim().toLowerCase()
  const groups = new Map()

  for (const doc of docs.value) {
    if (activeLibrary.value !== ALL && (doc.library_id || '') !== activeLibrary.value) continue
    if (q) {
      const hay = `${doc.title || ''} ${doc.task_desc || ''} ${JSON.stringify(doc.fields || {})}`.toLowerCase()
      if (!hay.includes(q)) continue
    }
    const key = doc.task_id || `${MANUAL}:${doc.created_at || ''}`
    let group = groups.get(key)
    if (!group) {
      group = {
        key,
        task_id: doc.task_id || '',
        title: doc.task_desc || (doc.task_id ? '未命名采集' : '手动文档'),
        created_at: doc.created_at || '',
        library_id: doc.library_id || '',
        docs: [],
      }
      groups.set(key, group)
    }
    group.docs.push(doc)
  }

  const list = [...groups.values()]
  for (const g of list) {
    g.library_name = libraryName(g.library_id)
    const cols = []
    for (const doc of g.docs) {
      for (const key of Object.keys(doc.fields || {})) {
        if (!cols.includes(key)) cols.push(key)
      }
    }
    g.cols = cols
  }
  return list.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
})

const totalDocs = computed(() => batches.value.reduce((n, b) => n + b.docs.length, 0))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const docRes = await listDocuments()
    docs.value = docRes.items || []
    const libRes = await listLibraries()
    libraries.value = libRes.items || []
    if (
      activeLibrary.value !== ALL &&
      !libraries.value.some((l) => l.id === activeLibrary.value)
    ) {
      activeLibrary.value = ALL
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function isExpanded(key) {
  return expanded.value.includes(key)
}

function toggle(key) {
  expanded.value = isExpanded(key)
    ? expanded.value.filter((k) => k !== key)
    : [...expanded.value, key]
}

function display(value) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function cellTitle(value) {
  const text = display(value)
  return text.length > 30 ? text : ''
}

function formatTime(value) {
  return (value || '').replace('T', ' ').slice(0, 16)
}

/** 把一次采集的整批结果移入子库。 */
async function moveBatch(batch, event) {
  const libraryId = event.target.value
  if (!batch.task_id) {
    event.target.value = batch.library_id
    return
  }
  try {
    await moveTaskLibrary(batch.task_id, libraryId)
    await load()
  } catch (e) {
    error.value = e.message
    event.target.value = batch.library_id
  }
}

async function removeBatch(batch) {
  const label = batch.task_id ? '这次采集的全部文档' : '这批手动文档'
  const ok = await confirmDialog({
    title: '删除文档批次',
    message: `确定删除「${batch.title}」中的${label}（${batch.docs.length} 条）吗？`,
    confirmText: '删除',
  })
  if (!ok) return
  try {
    if (batch.task_id) {
      await deleteDocumentBatch(batch.task_id)
    } else {
      await Promise.all(batch.docs.map((d) => deleteDocument(d.id)))
    }
    await load()
  } catch (e) {
    error.value = e.message
  }
}

function openDetail(doc) {
  selected.value = doc
}

function openCreate() {
  editor.value = {
    mode: 'create',
    id: '',
    title: '',
    library_id: activeLibrary.value === ALL ? '' : activeLibrary.value,
    source_url: '',
    fields_json: '{\n  \n}',
    error: '',
  }
}

function openEdit(doc) {
  editor.value = {
    mode: 'edit',
    id: doc.id,
    title: doc.title || '',
    library_id: doc.library_id || '',
    source_url: doc.source_url || '',
    fields_json: JSON.stringify(doc.fields || {}, null, 2),
    error: '',
  }
}

async function saveEditor() {
  const ed = editor.value
  if (!ed) return
  let fields
  try {
    fields = JSON.parse(ed.fields_json || '{}')
  } catch (e) {
    ed.error = `字段 JSON 解析失败：${e.message}`
    return
  }
  if (!fields || typeof fields !== 'object' || Array.isArray(fields)) {
    ed.error = '字段内容必须是 JSON 对象'
    return
  }
  const payload = {
    title: ed.title.trim(),
    library_id: ed.library_id || '',
    fields_json: JSON.stringify(fields, null, 2),
    source_url: ed.source_url.trim(),
  }
  try {
    if (ed.mode === 'create') {
      await createDocument(payload)
    } else {
      await updateDocument(ed.id, payload)
    }
    editor.value = null
    if (selected.value) selected.value = null
    await load()
  } catch (e) {
    ed.error = e.message
  }
}

async function removeDocument(doc) {
  const ok = await confirmDialog({
    title: '删除文档',
    message: `确定删除「${doc.title}」吗？`,
    confirmText: '删除',
  })
  if (!ok) return
  try {
    await deleteDocument(doc.id)
    selected.value = null
    await load()
  } catch (e) {
    error.value = e.message
  }
}

function openLibraryManager() {
  libError.value = ''
  newLibName.value = ''
  const drafts = {}
  for (const lib of libraries.value) {
    if (!lib.default) drafts[lib.id] = lib.name
  }
  libDrafts.value = drafts
  libManager.value = true
}

async function createLib() {
  const name = newLibName.value.trim()
  if (!name) {
    libError.value = '子库名称不能为空'
    return
  }
  try {
    const res = await createLibrary(name)
    newLibName.value = ''
    await load()
    activeLibrary.value = res.id
    libDrafts.value[res.id] = res.name
    libError.value = ''
  } catch (e) {
    libError.value = e.message
  }
}

async function renameLib(lib) {
  const name = (libDrafts.value[lib.id] || '').trim()
  if (!name) {
    libError.value = '子库名称不能为空'
    return
  }
  try {
    await updateLibrary(lib.id, name)
    await load()
    libError.value = ''
  } catch (e) {
    libError.value = e.message
  }
}

async function removeLib(lib) {
  const ok = await confirmDialog({
    title: '删除子库',
    message: `确定删除子库「${lib.name}」吗？其中的文档会移回默认库。`,
    confirmText: '删除',
  })
  if (!ok) return
  try {
    await deleteLibrary(lib.id)
    if (activeLibrary.value === lib.id) activeLibrary.value = ALL
    await load()
    delete libDrafts.value[lib.id]
  } catch (e) {
    libError.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <section class="page">
    <div class="page-toolbar">
      <div class="doc-actions">
        <input v-model="query" class="doc-search" placeholder="搜索标题、字段或任务描述" />
        <button type="button" class="btn ghost" @click="openLibraryManager">子库管理</button>
        <button type="button" class="btn ghost" @click="openCreate">新建文档</button>
        <button type="button" class="btn ghost" :disabled="loading" @click="load">刷新</button>
      </div>
    </div>

    <div v-if="error" class="alert error">{{ error }}</div>

    <div class="lib-layout">
      <aside class="lib-side">
        <div class="lib-head">
          <span>存储库</span>
          <span class="lib-count">{{ libraries.length }}</span>
        </div>
        <ul class="lib-list">
          <li
            class="lib-item"
            :class="{ active: activeLibrary === ALL }"
            @click="activeLibrary = ALL"
          >
            <span class="lib-name">全部</span>
            <span class="lib-count">{{ docs.length }}</span>
          </li>
          <li
            v-for="lib in libraries"
            :key="lib.id || 'default'"
            class="lib-item"
            :class="{ active: activeLibrary === lib.id }"
            @click="activeLibrary = lib.id"
          >
            <span class="lib-name" :title="lib.name">{{ lib.name }}</span>
            <span class="lib-count">{{ lib.doc_count }}</span>
          </li>
        </ul>
        <p class="lib-tip">新建子库后，可在采集批次右侧选择归属。</p>
      </aside>

      <div class="lib-main">
        <div class="lib-summary">
          <span>{{ totalDocs }} 条记录 · {{ batches.length }} 个批次</span>
          <span class="lib-filter">{{ activeLibName }}</span>
        </div>

        <div v-if="loading" class="card doc-empty"><span class="spinner" /> 加载中……</div>
        <div v-else-if="!batches.length" class="card doc-empty">
          <p class="empty">
            {{
              query
                ? '没有匹配的文档，换个关键词试试。'
                : activeLibrary !== ALL
                  ? '这个子库还没有文档，可在批次右侧的「归属」中把采集结果移入。'
                  : '这里还没有文档，完成一次采集后会自动归档。'
            }}
          </p>
        </div>

        <div v-else class="batch-list">
          <section v-for="batch in batches" :key="batch.key" class="batch">
            <header class="batch-head" @click="toggle(batch.key)">
              <div class="batch-main">
                <span class="batch-caret" :class="{ open: isExpanded(batch.key) }">▶</span>
                <div class="batch-title">
                  <h3 :title="batch.title">{{ batch.title }}</h3>
                  <p class="batch-sub">
                    <span>{{ batch.docs.length }} 条</span>
                    <span>{{ batch.library_name }}</span>
                    <span v-if="batch.created_at">{{ formatTime(batch.created_at) }}</span>
                    <span v-if="batch.task_id" class="mono">{{ batch.task_id.slice(0, 8) }}</span>
                    <span v-else>手动文档</span>
                  </p>
                </div>
              </div>
              <div class="batch-actions" @click.stop>
                <span v-if="batch.task_id" class="batch-lib-label">归属</span>
                <select
                  v-if="batch.task_id"
                  :value="batch.library_id"
                  title="移动到子库"
                  @change="moveBatch(batch, $event)"
                >
                  <option v-for="lib in libraries" :key="lib.id || 'default'" :value="lib.id">
                    {{ lib.name }}
                  </option>
                </select>
                <a
                  v-if="batch.task_id"
                  class="btn ghost small"
                  :href="exportUrl(batch.task_id, 'csv')"
                >
                  CSV
                </a>
                <a
                  v-if="batch.task_id"
                  class="btn ghost small"
                  :href="exportUrl(batch.task_id, 'json')"
                >
                  JSON
                </a>
                <button type="button" class="btn ghost small danger" @click="removeBatch(batch)">
                  删除
                </button>
              </div>
            </header>

            <div v-if="isExpanded(batch.key)" class="batch-body">
              <div class="table-wrap">
                <table class="table batch-table">
                  <thead>
                    <tr>
                      <th class="idx">#</th>
                      <th v-for="col in batch.cols" :key="col">{{ col }}</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(doc, i) in batch.docs" :key="doc.id" @click="openDetail(doc)">
                      <td class="idx">{{ i + 1 }}</td>
                      <td
                        v-for="col in batch.cols"
                        :key="col"
                        :title="cellTitle(doc.fields[col])"
                        :class="{ muted: display(doc.fields[col]) === '—' }"
                      >
                        {{ display(doc.fields[col]) }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p class="batch-hint">表格列由采集字段自动生成，点击任意行查看详情。</p>
            </div>
          </section>
        </div>
      </div>
    </div>

    <!-- 文档详情 -->
    <div v-if="selected" class="modal-backdrop" @click.self="selected = null">
      <div class="modal narrow">
        <div class="modal-head">
          <h3 :title="selected.title">{{ selected.title }}</h3>
          <button type="button" class="modal-close" @click="selected = null">×</button>
        </div>
        <div class="modal-body">
          <div class="doc-meta">
            <span>归属 {{ selected.library_name }}</span>
            <span v-if="selected.task_desc">采集：{{ selected.task_desc }}</span>
            <span>{{ formatTime(selected.created_at) }}</span>
          </div>
          <a
            v-if="selected.source_url"
            class="doc-link"
            :href="selected.source_url"
            target="_blank"
            rel="noopener"
          >
            {{ selected.source_url }}
          </a>
          <div class="doc-fields">
            <div v-for="(value, key) in selected.fields" :key="key" class="doc-field">
              <span class="doc-key">{{ key }}</span>
              <span class="doc-value">{{ display(value) }}</span>
            </div>
          </div>
        </div>
        <div class="modal-foot">
          <button type="button" class="btn ghost" @click="openEdit(selected)">编辑</button>
          <button type="button" class="btn ghost danger" @click="removeDocument(selected)">
            删除
          </button>
          <a
            v-if="selected.task_id"
            class="btn ghost"
            :href="exportUrl(selected.task_id, 'csv')"
          >
            导出批次
          </a>
          <button type="button" class="btn primary" @click="selected = null">关闭</button>
        </div>
      </div>
    </div>

    <!-- 新建 / 编辑文档 -->
    <div v-if="editor" class="modal-backdrop" @click.self="editor = null">
      <div class="modal">
        <div class="modal-head">
          <h3>{{ editor.mode === 'create' ? '新建文档' : '编辑文档' }}</h3>
          <button type="button" class="modal-close" @click="editor = null">×</button>
        </div>
        <div class="modal-body">
          <label class="field">
            <span>标题</span>
            <input v-model="editor.title" placeholder="留空则自动从字段中取标题" />
          </label>
          <label class="field">
            <span>归属子库</span>
            <select v-model="editor.library_id">
              <option v-for="lib in libraries" :key="lib.id || 'default'" :value="lib.id">
                {{ lib.name }}
              </option>
            </select>
          </label>
          <label class="field">
            <span>来源网址</span>
            <input v-model="editor.source_url" placeholder="https://example.com/item/1" />
          </label>
          <label class="field">
            <span>字段（JSON 对象）</span>
            <textarea v-model="editor.fields_json" rows="10" spellcheck="false" />
          </label>
          <div v-if="editor.error" class="alert error">{{ editor.error }}</div>
        </div>
        <div class="modal-foot">
          <button type="button" class="btn ghost" @click="editor = null">取消</button>
          <button type="button" class="btn primary" @click="saveEditor">保存</button>
        </div>
      </div>
    </div>

    <!-- 子库管理 -->
    <div v-if="libManager" class="modal-backdrop" @click.self="libManager = false">
      <div class="modal narrow">
        <div class="modal-head">
          <h3>子库管理</h3>
          <button type="button" class="modal-close" @click="libManager = false">×</button>
        </div>
        <div class="modal-body">
          <div class="lib-manage-add">
            <input v-model="newLibName" placeholder="输入新子库名称" @keyup.enter="createLib" />
            <button type="button" class="btn primary" @click="createLib">新建</button>
          </div>
          <p class="lib-manage-tip">删除子库时，其中的文档会自动移回默认库。</p>
          <div v-if="libError" class="alert error">{{ libError }}</div>
          <div class="lib-manage-list">
            <div v-for="lib in libraries" :key="lib.id || 'default'" class="lib-manage-row">
              <template v-if="lib.default">
                <span class="lib-manage-name">{{ lib.name }}</span>
                <span class="lib-count">{{ lib.doc_count }}</span>
                <span class="lib-manage-fixed">固定</span>
              </template>
              <template v-else>
                <input v-model="libDrafts[lib.id]" class="lib-manage-input" />
                <span class="lib-count">{{ lib.doc_count }}</span>
                <button type="button" class="btn ghost small" @click="renameLib(lib)">保存</button>
                <button type="button" class="btn ghost small danger" @click="removeLib(lib)">
                  删除
                </button>
              </template>
            </div>
          </div>
        </div>
        <div class="modal-foot">
          <button type="button" class="btn primary" @click="libManager = false">完成</button>
        </div>
      </div>
    </div>
  </section>
</template>
