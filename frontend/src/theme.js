const THEME_KEY = 'ac.theme'

const systemQuery = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null

/** 本机是否已主动选择过主题（区别于首次打开）。 */
export function hasThemePreference() {
  const saved = localStorage.getItem(THEME_KEY)
  return saved === 'dark' || saved === 'light' || saved === 'system'
}

export function getThemePreference() {
  const saved = localStorage.getItem(THEME_KEY)
  return saved === 'dark' || saved === 'light' || saved === 'system' ? saved : 'system'
}

export function resolveTheme(preference) {
  if (preference === 'system') return systemQuery && systemQuery.matches ? 'dark' : 'light'
  return preference
}

/** 写入 data-theme 并记住偏好，立即生效。 */
export function applyTheme(preference) {
  const resolved = resolveTheme(preference)
  document.documentElement.dataset.theme = resolved
  document.documentElement.style.colorScheme = resolved
  localStorage.setItem(THEME_KEY, preference)
  return resolved
}

/** 应用启动时调用：恢复上次主题，并在跟随系统时响应系统切换。 */
export function initTheme() {
  applyTheme(getThemePreference())
  if (systemQuery && systemQuery.addEventListener) {
    systemQuery.addEventListener('change', () => {
      if (getThemePreference() === 'system') applyTheme('system')
    })
  }
}
