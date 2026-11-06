import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './style.css'
import { getSettings } from './api'
import { applyTheme, hasThemePreference, initTheme } from './theme'

initTheme()

// 本机还没有选择过主题时，沿用服务端保存的偏好（例如换了一台设备/浏览器）
if (!hasThemePreference()) {
  getSettings()
    .then((res) => {
      const pref = res && res.ui && res.ui.theme
      if (pref === 'dark' || pref === 'light') applyTheme(pref)
    })
    .catch(() => {})
}

createApp(App).use(router).mount('#app')
