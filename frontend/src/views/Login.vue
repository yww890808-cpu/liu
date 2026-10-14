<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2>Ozon 管理后台</h2>
      <el-form :model="form" @submit.prevent="submit" label-position="top">
        <el-form-item label="用户名">
          <el-input v-model="form.username" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password autocomplete="current-password" />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="loading" style="width: 100%">登录</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import http, { auth, showError } from '../api'

const router = useRouter()
const route = useRoute()
const form = reactive({ username: '', password: '' })
const loading = ref(false)

async function submit() {
  loading.value = true
  try {
    const body = new URLSearchParams(form)
    const { data } = await http.post('/api/auth/login', body)
    auth.token = data.access_token
    router.replace(route.query.redirect || '/')
  } catch (e) {
    showError(e)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap { min-height: 100%; display: flex; align-items: center; justify-content: center; padding: 16px; box-sizing: border-box; }
.login-card { width: 360px; max-width: 100%; }
h2 { text-align: center; margin: 0 0 20px; }
</style>
