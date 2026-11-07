import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5170,
    strictPort: true,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
  build: {
    // echarts 体积较大，单独成块后只在可视化页按需加载
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('echarts') || id.includes('zrender')) return 'vendor-echarts'
          if (id.includes('vue-router') || id.includes('@vue') || id.includes('/vue/')) {
            return 'vendor-vue'
          }
          return 'vendor'
        },
      },
    },
  },
})
