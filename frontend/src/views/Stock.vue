<template>
  <div>
    <h1 class="page-title">库存</h1>
    <el-alert type="info" :closable="false" style="margin-bottom: 12px"
              title="填写的是可售数量（不含已被订单预留的部分）。修改后点“保存修改”，下一次自动推送（每 30 分钟）时同步到 Ozon，也可以点“立即推送”。从未设置过的库存不会推送。" />

    <div class="card">
      <div class="toolbar">
        <el-select v-model="shopId" placeholder="选择店铺" style="width: 160px" @change="search">
          <el-option v-for="s in shops" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-input v-model="q" placeholder="货号 / 商品名" clearable style="width: 200px" @keyup.enter="search" @clear="search" />
        <el-checkbox v-model="onlyPending" @change="search">只看待推送</el-checkbox>
        <el-button @click="search">查询</el-button>
        <span class="spacer" />
        <el-button :disabled="!shopId" @click="trigger('refresh', '从 Ozon 读取库存')">从 Ozon 读取</el-button>
        <el-button type="primary" :disabled="!dirtyCount" :loading="saving" @click="save">保存修改（{{ dirtyCount }}）</el-button>
        <el-button type="warning" :disabled="!shopId" @click="trigger('push', '库存推送')">立即推送</el-button>
      </div>

      <el-empty v-if="!shopId" description="请先选择店铺" />
      <template v-else>
        <el-table :data="rows" v-loading="loading" border>
          <el-table-column label="商品" min-width="200" fixed>
            <template #default="{ row }">
              <div>{{ row.name }}</div>
              <div class="muted">{{ row.offer_id }} · SKU {{ row.sku ?? '-' }}</div>
            </template>
          </el-table-column>
          <el-table-column v-for="wh in warehouses" :key="wh.id" :label="wh.name" min-width="170" align="center">
            <template #header>
              {{ wh.name }}
              <el-tag v-if="!wh.is_active" size="small" type="info">不推送</el-tag>
            </template>
            <template #default="{ row }">
              <el-input-number v-model="edits[key(row, wh)]" :min="0" size="small" controls-position="right"
                               :placeholder="cell(row, wh)?.local_stock == null ? '未设置' : ''"
                               :class="{ edited: isEdited(row, wh) }" style="width: 120px" />
              <div class="muted cell-info">
                Ozon {{ cell(row, wh)?.ozon_stock ?? '-' }}<template v-if="cell(row, wh)?.ozon_reserved"> · 预留 {{ cell(row, wh).ozon_reserved }}</template>
                <el-tag v-if="cell(row, wh)?.pending" size="small" type="warning">待推送</el-tag>
              </div>
              <div v-if="cell(row, wh)?.push_error" class="cell-error">{{ cell(row, wh).push_error }}</div>
            </template>
          </el-table-column>
        </el-table>
        <div class="pager">
          <el-pagination v-model:current-page="page" :page-size="size" :total="total" layout="total, prev, pager, next" @current-change="load" />
        </div>
        <div v-if="!warehouses.length && !loading" class="muted">该店铺还没有仓库，请先到“店铺”页点“同步商品”。</div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onBeforeRouteLeave } from 'vue-router'
import http, { showError } from '../api'
import { loadShops, shops } from '../shops'

const shopId = ref(null)
const q = ref('')
const onlyPending = ref(false)
const rows = ref([])
const warehouses = ref([])
const total = ref(0)
const page = ref(1)
const size = 50
const loading = ref(false)
const saving = ref(false)
const edits = ref({})     // 输入框当前值
const original = ref({})  // 加载时的本地库存，用于判断是否修改过

const key = (row, wh) => `${row.product_id}:${wh.id}`
const cell = (row, wh) => row.stocks.find((s) => s.warehouse_id === wh.id)
const isEdited = (row, wh) => edits.value[key(row, wh)] !== original.value[key(row, wh)]
const dirtyKeys = computed(() => Object.keys(edits.value).filter(
  (k) => edits.value[k] !== original.value[k] && edits.value[k] != null))
const dirtyCount = computed(() => dirtyKeys.value.length)

onMounted(async () => {
  if (!shops.value.length) await loadShops()
  if (shops.value.length) { shopId.value = shops.value[0].id; load() }
})

async function confirmDiscard() {
  if (!dirtyCount.value) return true
  try {
    await ElMessageBox.confirm(`有 ${dirtyCount.value} 处修改未保存，确定放弃？`, '未保存', { type: 'warning' })
    return true
  } catch { return false }
}

onBeforeRouteLeave(confirmDiscard)

async function search() {
  if (!(await confirmDiscard())) return
  page.value = 1
  load()
}

async function load() {
  if (!shopId.value) return
  loading.value = true
  try {
    const { data } = await http.get(`/api/shops/${shopId.value}/stocks`, {
      params: { q: q.value || undefined, only_pending: onlyPending.value, page: page.value, size },
    })
    rows.value = data.items
    warehouses.value = data.warehouses
    total.value = data.total
    const values = {}
    for (const row of data.items) {
      for (const wh of data.warehouses) {
        values[key(row, wh)] = cell(row, wh)?.local_stock ?? null
      }
    }
    original.value = values
    edits.value = { ...values }
  } catch (e) {
    showError(e)
  } finally {
    loading.value = false
  }
}

async function save() {
  const changes = dirtyKeys.value.map((k) => {
    const [product_id, warehouse_id] = k.split(':').map(Number)
    return { product_id, warehouse_id, local_stock: edits.value[k] }
  })
  saving.value = true
  try {
    await http.put(`/api/shops/${shopId.value}/stocks`, changes)
    ElMessage.success(`已保存 ${changes.length} 处修改`)
    await load()
  } catch (e) {
    showError(e)
  } finally {
    saving.value = false
  }
}

async function trigger(action, label) {
  if (!(await confirmDiscard())) return
  try {
    await http.post(`/api/shops/${shopId.value}/stocks/${action}`)
    ElMessage.success(`${label}已开始，稍后刷新查看结果；执行记录见“日志”页`)
  } catch (e) { showError(e) }
}
</script>

<style scoped>
.cell-info { margin-top: 2px; }
.cell-error { color: #f56c6c; font-size: 12px; }
.edited :deep(.el-input__wrapper) { box-shadow: 0 0 0 1px #e6a23c inset; }
</style>
