import { createRouter, createWebHistory } from 'vue-router'

// 路由按需加载：首屏只加载布局与首页，可视化页（含 echarts）等按需拉取
const routes = [
  { path: '/', name: 'home', component: () => import('./views/HomeView.vue') },
  { path: '/crawl', name: 'crawl', component: () => import('./views/CrawlView.vue') },
  { path: '/schedules', name: 'schedules', component: () => import('./views/SchedulesView.vue') },
  { path: '/sites', name: 'sites', component: () => import('./views/SitesView.vue') },
  { path: '/visualize', name: 'visualize', component: () => import('./views/VisualizeView.vue') },
  { path: '/documents', name: 'documents', component: () => import('./views/DocumentsView.vue') },
  { path: '/logs', name: 'logs', component: () => import('./views/LogsView.vue') },
  { path: '/history', name: 'history', component: () => import('./views/HistoryView.vue') },
  { path: '/help', name: 'help', component: () => import('./views/HelpView.vue') },
  { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  const titles = {
    home: '首页',
    crawl: '智能采集',
    schedules: '定时任务',
    sites: '网站记忆',
    visualize: '可视化',
    documents: '文档库',
    logs: '系统日志',
    history: '历史任务',
    help: '帮助中心',
    settings: '设置',
  }
  document.title = `${titles[to.name] || '智能体爬虫'} · 智能体爬虫`
})

export default router
