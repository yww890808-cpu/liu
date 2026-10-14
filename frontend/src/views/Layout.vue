<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="brand">Ozon 管理后台</div>
      <el-menu mode="horizontal" :default-active="route.name" router :ellipsis="false" class="nav">
        <el-menu-item index="orders" :route="{ name: 'orders' }">订单</el-menu-item>
        <el-menu-item index="stock" :route="{ name: 'stock' }">库存</el-menu-item>
        <el-menu-item index="shops" :route="{ name: 'shops' }">店铺</el-menu-item>
        <el-menu-item index="logs" :route="{ name: 'logs' }">日志</el-menu-item>
      </el-menu>
      <div class="user">
        <span class="muted">{{ username }}</span>
        <el-button link @click="logout">退出</el-button>
      </div>
    </el-header>
    <el-alert v-if="dryRun" type="warning" :closable="false" show-icon class="dry-run"
              title="试运行模式：备货、库存推送只记录不提交到 Ozon。确认无误后在 .env 中设置 DRY_RUN=false 并重启服务。" />
    <el-main class="main">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import http, { auth } from '../api'
import { loadShops } from '../shops'

const route = useRoute()
const router = useRouter()
const dryRun = ref(false)
const username = ref('')

onMounted(async () => {
  const [health, me] = await Promise.all([http.get('/health'), http.get('/api/auth/me')])
  dryRun.value = health.data.dry_run
  username.value = me.data.username
  loadShops()
})

function logout() {
  auth.token = null
  router.push({ name: 'login' })
}
</script>

<style scoped>
.layout { min-height: 100%; }
.header { display: flex; align-items: center; gap: 16px; background: #fff; border-bottom: 1px solid #e4e7ed; padding: 0 16px; }
.brand { font-weight: 600; font-size: 16px; white-space: nowrap; }
.nav { flex: 1; border-bottom: none; min-width: 0; }
.user { display: flex; align-items: center; gap: 8px; white-space: nowrap; }
.dry-run { border-radius: 0; }
.main { padding: 16px; }
@media (max-width: 640px) {
  .brand { display: none; }
  .header { padding: 0 4px; gap: 4px; }
}
</style>
