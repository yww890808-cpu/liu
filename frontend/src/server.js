// 服务器配置（是否需要登录、是否试运行），启动时读取一次
import axios from 'axios'

let cached = null

export async function serverConfig() {
  if (!cached) {
    cached = axios.get('/health').then((r) => r.data).catch(() => {
      cached = null // 读取失败下次重试
      return { login_required: true, dry_run: true }
    })
  }
  return cached
}
