import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时把接口请求转发到后端（uvicorn 默认 8000 端口）
export default defineConfig({
  plugins: [vue()],
  build: { chunkSizeWarningLimit: 1500 }, // 内部后台，整包加载 Element Plus 即可
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
