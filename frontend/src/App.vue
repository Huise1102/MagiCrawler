<script setup>
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <div class="app-shell">
    <header class="navbar">
      <div class="nav-inner">
        <RouterLink to="/" class="brand" exact-active-class="brand-active">
          <span class="brand-mark" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
              <circle cx="12" cy="4.5" r="2.2" />
              <circle cx="4.5" cy="19" r="2.2" />
              <circle cx="19.5" cy="19" r="2.2" />
              <path d="M12 6.7v5.3M12 12l-6.2 5.2M12 12l6.2 5.2" stroke-linecap="round" />
            </svg>
          </span>
          <span class="brand-name">智能体爬虫</span>
        </RouterLink>

        <nav class="nav-links">
          <RouterLink to="/" exact-active-class="active">首页</RouterLink>
          <RouterLink to="/schedules" active-class="active">定时任务</RouterLink>
          <RouterLink to="/visualize" active-class="active">可视化</RouterLink>
          <RouterLink to="/documents" active-class="active">文档库</RouterLink>
          <RouterLink to="/history" active-class="active">历史任务</RouterLink>
        </nav>
      </div>
    </header>

    <main class="main">
      <RouterView />
    </main>
  </div>
</template>
