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

export function autoClassifyDocuments() {
  return request('/api/documents/auto-classify', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({}),
  })
}

export function listCategories() {
  return request('/api/categories')
}

export function createCategory(name) {
  return request('/api/categories', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ name }),
  })
}

export function updateCategory(id, name) {
  return request(`/api/categories/${id}`, {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify({ name }),
  })
}

export function deleteCategory(id) {
  return request(`/api/categories/${id}`, { method: 'DELETE' })
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
