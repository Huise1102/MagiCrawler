import { createRouter, createWebHistory } from 'vue-router'
import HomeView from './views/HomeView.vue'
import CrawlView from './views/CrawlView.vue'
import SchedulesView from './views/SchedulesView.vue'
import VisualizeView from './views/VisualizeView.vue'
import DocumentsView from './views/DocumentsView.vue'
import LogsView from './views/LogsView.vue'
import HistoryView from './views/HistoryView.vue'
import HelpView from './views/HelpView.vue'
import SettingsView from './views/SettingsView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/crawl', name: 'crawl', component: CrawlView },
    { path: '/schedules', name: 'schedules', component: SchedulesView },
    { path: '/visualize', name: 'visualize', component: VisualizeView },
    { path: '/documents', name: 'documents', component: DocumentsView },
    { path: '/logs', name: 'logs', component: LogsView },
    { path: '/history', name: 'history', component: HistoryView },
    { path: '/help', name: 'help', component: HelpView },
    { path: '/settings', name: 'settings', component: SettingsView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.afterEach((to) => {
  const titles = {
    home: '首页',
    crawl: '智能采集',
    schedules: '定时任务',
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
