async function request(url, options = {}) {
  const res = await fetch(url, options)
  if (!res.ok) {
    let detail = `请求失败（${res.status}）`
    try {
      const data = await res.json()
      if (data && data.detail) detail = data.detail
    } catch {
      /* 忽略非 JSON 响应 */
    }
    throw new Error(detail)
  }
  return res.json()
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export function createTask(payload) {
  return request('/api/tasks', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
}

export function listTasks(status = '') {
  const qs = new URLSearchParams({ limit: '100' })
  if (status) qs.set('status', status)
  return request(`/api/tasks?${qs.toString()}`)
}

export function getTask(id) {
  return request(`/api/tasks/${id}`)
}

export function getTaskDiff(id) {
  return request(`/api/tasks/${id}/diff`)
}

export function searchResults(q, limit = 30) {
  const qs = new URLSearchParams({ q, limit: String(limit) })
  return request(`/api/results/search?${qs.toString()}`)
}

export function generateSchema(payload) {
  return request('/api/schema/generate', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
}

export function deleteTask(id) {
  return request(`/api/tasks/${id}`, { method: 'DELETE' })
}

export function exportUrl(id, fmt) {
  return `/api/tasks/${id}/export?fmt=${fmt}`
}

export function visualizeTask(id, hint = '') {
  return request(`/api/tasks/${id}/visualize`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ hint }),
  })
}

export function listDocuments() {
  return request('/api/documents')
}

export function createDocument(payload) {
  return request('/api/documents', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
}

export function updateDocument(id, payload) {
  return request(`/api/documents/${id}`, {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
}

export function deleteDocument(id) {
  return request(`/api/documents/${id}`, { method: 'DELETE' })
}

export function listLibraries() {
  return request('/api/libraries')
}

export function createLibrary(name) {
  return request('/api/libraries', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ name }),
  })
}

export function updateLibrary(id, name) {
  return request(`/api/libraries/${id}`, {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify({ name }),
  })
}

export function deleteLibrary(id) {
  return request(`/api/libraries/${id}`, { method: 'DELETE' })
}

/** 把一次采集批次的全部文档移入指定子库。 */
export function moveTaskLibrary(taskId, libraryId) {
  return request(`/api/tasks/${taskId}/library`, {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify({ library_id: libraryId }),
  })
}

export function deleteDocumentBatch(taskId) {
  return request(`/api/document-batches/${taskId}`, { method: 'DELETE' })
}

export function listLogs(level = '', q = '', limit = 300) {
  const qs = new URLSearchParams({ limit: String(limit) })
  if (level) qs.set('level', level)
  if (q) qs.set('q', q)
  return request(`/api/logs?${qs.toString()}`)
}

export function clearLogs() {
  return request('/api/logs', { method: 'DELETE' })
}

export function listSchedules() {
  return request('/api/schedules')
}

export function createSchedule(payload) {
  return request('/api/schedules', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
}

export function updateSchedule(id, payload) {
  return request(`/api/schedules/${id}`, {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
}

export function deleteSchedule(id) {
  return request(`/api/schedules/${id}`, { method: 'DELETE' })
}

export function toggleSchedule(id, enabled) {
  return request(`/api/schedules/${id}/toggle`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ enabled }),
  })
}

export function runSchedule(id) {
  return request(`/api/schedules/${id}/run`, { method: 'POST' })
}

export function getSettings() {
  return request('/api/settings')
}

export function saveTheme(theme) {
  return request('/api/settings/theme', {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify({ theme }),
  })
}

export function saveNotify(payload) {
  return request('/api/settings/notify', {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
}

export function testNotify(payload) {
  return request('/api/settings/notify/test', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
}

export function saveRetention(payload) {
  return request('/api/settings/retention', {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
}

export function getRetentionStats() {
  return request('/api/settings/retention/stats')
}

export function cleanupRetention() {
  return request('/api/settings/retention/cleanup', { method: 'POST' })
}

export function saveSettings(payload) {
  return request('/api/settings', {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
}

export function listModels(payload) {
  return request('/api/settings/models', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
}

export function testSettings(payload) {
  return request('/api/settings/test', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
}

export function listSites() {
  return request('/api/sites')
}

export function createSite(payload) {
  return request('/api/sites', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
}

export function updateSite(id, payload) {
  return request(`/api/sites/${id}`, {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
}

export function deleteSite(id) {
  return request(`/api/sites/${id}`, { method: 'DELETE' })
}

/** 打开该站点的登录窗口（登录态保存在本地 profile）。 */
export function loginSite(id) {
  return request(`/api/sites/${id}/login`, { method: 'POST' })
}
