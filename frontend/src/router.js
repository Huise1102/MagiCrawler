import { createRouter, createWebHistory } from 'vue-router'
import HomeView from './views/HomeView.vue'
import SchedulesView from './views/SchedulesView.vue'
import VisualizeView from './views/VisualizeView.vue'
import DocumentsView from './views/DocumentsView.vue'
import HistoryView from './views/HistoryView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/schedules', name: 'schedules', component: SchedulesView },
    { path: '/visualize', name: 'visualize', component: VisualizeView },
    { path: '/documents', name: 'documents', component: DocumentsView },
    { path: '/history', name: 'history', component: HistoryView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.afterEach((to) => {
  const titles = {
    home: '首页（智能体爬虫）',
    schedules: '定时任务',
    visualize: '可视化',
    documents: '文档库',
    history: '历史任务',
  }
  document.title = `${titles[to.name] || '智能体爬虫'} · 智能体爬虫`
})

export default router
