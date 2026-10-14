<template>
  <div>
    <h1 class="page-title">日志</h1>
    <div class="card">
      <el-tabs v-model="tab" @tab-change="search">
        <el-tab-pane label="任务记录" name="tasks" />
        <el-tab-pane label="Ozon 接口调用" name="api" />
      </el-tabs>

      <div class="toolbar">
        <el-select v-model="shopId" placeholder="全部店铺" clearable style="width: 140px" @change="search">
          <el-option v-for="s in shops" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-checkbox v-model="onlyErrors" @change="search">只看失败</el-checkbox>
        <el-button @click="load">刷新</el-button>
      </div>

      <el-table v-if="tab === 'tasks'" :data="rows" v-loading="loading" size="small">
        <el-table-column label="时间" width="140"><template #default="{ row }">{{ fmtTime(row.started_at) }}</template></el-table-column>
        <el-table-column label="任务" width="140"><template #default="{ row }">{{ TASK_NAME[row.task_name] || row.task_name }}</template></el-table-column>
        <el-table-column label="店铺" width="110"><template #default="{ row }">{{ row.shop_id ? shopName(row.shop_id) : '-' }}</template></el-table-column>
        <el-table-column label="结果" width="80">
          <template #default="{ row }">
            <el-tag :type="{ success: 'success', failed: 'danger' }[row.status] || 'info'" size="small">
              {{ { success: '成功', failed: '失败', running: '进行中' }[row.status] || row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="说明" min-width="300">
          <template #default="{ row }"><div class="msg">{{ row.message }}</div></template>
        </el-table-column>
      </el-table>

      <el-table v-else :data="rows" v-loading="loading" size="small">
        <el-table-column label="时间" width="140"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="店铺" width="110"><template #default="{ row }">{{ row.shop_id ? shopName(row.shop_id) : '-' }}</template></el-table-column>
        <el-table-column label="接口" min-width="200"><template #default="{ row }"><span class="mono">{{ row.endpoint }}</span></template></el-table-column>
        <el-table-column label="状态码" width="80"><template #default="{ row }">{{ row.status_code ?? '网络错误' }}</template></el-table-column>
        <el-table-column label="耗时" width="80"><template #default="{ row }">{{ row.duration_ms }} ms</template></el-table-column>
        <el-table-column label="错误" min-width="260">
          <template #default="{ row }"><div class="msg" style="color: #f56c6c">{{ row.error }}</div></template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination v-model:current-page="page" :page-size="size" :total="total" layout="total, prev, pager, next" @current-change="load" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import http, { showError } from '../api'
import { TASK_NAME, fmtTime } from '../constants'
import { shopName, shops } from '../shops'

const tab = ref('tasks')
const shopId = ref(null)
const onlyErrors = ref(false)
const rows = ref([])
const total = ref(0)
const page = ref(1)
const size = 50
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const params = { page: page.value, size, shop_id: shopId.value || undefined }
    if (tab.value === 'tasks' && onlyErrors.value) params.status = 'failed'
    if (tab.value === 'api') params.only_errors = onlyErrors.value
    const { data } = await http.get(`/api/logs/${tab.value}`, { params })
    rows.value = data.items
    total.value = data.total
  } catch (e) {
    showError(e)
  } finally {
    loading.value = false
  }
}

function search() { page.value = 1; load() }
onMounted(load)
</script>

<style scoped>
.msg { white-space: pre-wrap; word-break: break-all; font-size: 12px; }
</style>
