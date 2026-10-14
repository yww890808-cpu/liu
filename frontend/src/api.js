// 接口封装：自动带登录凭证，登录失效时跳回登录页
import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from './router'

const TOKEN_KEY = 'ozon_token'

export const auth = {
  get token() { return localStorage.getItem(TOKEN_KEY) },
  set token(v) { v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY) },
}

const http = axios.create({ timeout: 60000 })

http.interceptors.request.use((config) => {
  if (auth.token) config.headers.Authorization = `Bearer ${auth.token}`
  return config
})

http.interceptors.response.use(
  (resp) => resp,
  async (error) => {
    const status = error.response?.status
    if (status === 401 && router.currentRoute.value.name !== 'login') {
      auth.token = null
      router.push({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
      ElMessage.warning('登录已失效，请重新登录')
    }
    return Promise.reject(error)
  },
)

// 从错误响应中取出可读的错误信息（下载文件时响应体是 Blob）
export async function errorText(error) {
  let data = error.response?.data
  if (data instanceof Blob) {
    try { data = JSON.parse(await data.text()) } catch { data = null }
  }
  const detail = data?.detail
  if (Array.isArray(detail)) return detail.map((d) => d.msg).join('；')
  return detail || error.message || '请求失败'
}

export async function showError(error) {
  if (error.response?.status === 401) return
  ElMessage.error(await errorText(error))
}

// 下载 PDF 等文件：带上登录凭证后保存为本地文件
export async function download(method, url, data, filename) {
  const resp = await http.request({ method, url, data, responseType: 'blob' })
  const href = URL.createObjectURL(resp.data)
  const a = document.createElement('a')
  a.href = href
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(href), 10000)
}

export default http

// 拼接到 HTML 弹窗中的外部文本（Ozon 报错、货号等）必须转义
export const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
