<template>
  <div>
    <h1 class="page-title">订单</h1>

    <div class="card">
      <div class="toolbar">
        <el-select v-model="filters.shop_id" placeholder="全部店铺" clearable style="width: 140px" @change="search">
          <el-option v-for="s in shops" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-select v-model="filters.status" placeholder="全部状态" multiple collapse-tags clearable style="width: 200px" @change="search">
          <el-option v-for="(n, s) in statusCounts" :key="s" :label="`${statusText(s)}（${n}）`" :value="s" />
        </el-select>
        <el-date-picker v-model="filters.range" type="daterange" start-placeholder="下单开始" end-placeholder="下单结束"
                        style="width: 240px" @change="search" />
        <el-input v-model="filters.q" placeholder="货件号 / 货号 / 商品名" clearable style="width: 200px"
                  @keyup.enter="search" @clear="search" />
        <el-button type="primary" @click="search">查询</el-button>
        <span class="spacer" />
        <el-button :disabled="!selected.length" @click="batchShip">批量备货（{{ selected.length }}）</el-button>
        <el-button :disabled="!selected.length" @click="batchLabels">批量打印面单</el-button>
      </div>

      <!-- 手机：卡片列表 -->
      <div v-if="isMobile" v-loading="loading">
        <div v-for="row in rows" :key="row.id" class="order-card" @click="openDetail(row)">
          <div class="order-card-head">
            <el-checkbox :model-value="selected.includes(row)" @click.stop
                         @change="(v) => (selected = v ? [...selected, row] : selected.filter((o) => o !== row))" />
            <span class="mono">{{ row.posting_number }}</span>
            <el-tag size="small" :type="STATUS_TAG[row.status] || 'info'">{{ statusText(row.status) }}</el-tag>
          </div>
          <div v-for="(it, i) in row.items" :key="i">{{ it.name }} <b>×{{ it.quantity }}</b></div>
          <div class="muted">{{ shopName(row.shop_id) }} · {{ row.warehouse_name || '-' }} · 最晚发货 {{ fmtTime(row.shipment_date) }}</div>
          <div v-if="row.tracking_number" class="muted">物流单号 <span class="mono">{{ row.tracking_number }}</span></div>
          <div style="margin-top: 6px">
            <el-button v-if="row.status === 'awaiting_packaging'" size="small" type="primary" @click.stop="openShip(row)">备货</el-button>
            <el-button v-else size="small" @click.stop="downloadLabel(row)">面单</el-button>
          </div>
        </div>
        <el-empty v-if="!rows.length && !loading" description="没有订单" />
      </div>

      <el-table v-else :data="rows" v-loading="loading" @selection-change="(v) => (selected = v)" @row-click="openDetail" style="cursor: pointer">
        <el-table-column type="selection" width="40" />
        <el-table-column label="货件号" min-width="150">
          <template #default="{ row }">
            <div class="mono">{{ row.posting_number }}</div>
            <div v-if="row.parent_posting_number" class="muted">拆自 {{ row.parent_posting_number }}</div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="STATUS_TAG[row.status] || 'info'">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="商品" min-width="200">
          <template #default="{ row }">
            <div v-for="(it, i) in row.items" :key="i">{{ it.name }} <b>×{{ it.quantity }}</b></div>
          </template>
        </el-table-column>
        <el-table-column label="店铺 / 仓库" min-width="120">
          <template #default="{ row }">
            <div>{{ shopName(row.shop_id) }}</div>
            <div class="muted">{{ row.warehouse_name || '-' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="下单时间" width="140">
          <template #default="{ row }">{{ fmtTime(row.ozon_created_at) }}</template>
        </el-table-column>
        <el-table-column label="最晚发货" width="140">
          <template #default="{ row }">{{ fmtTime(row.shipment_date) }}</template>
        </el-table-column>
        <el-table-column label="物流单号" min-width="120">
          <template #default="{ row }"><span class="mono">{{ row.tracking_number || '-' }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status === 'awaiting_packaging'" size="small" type="primary" @click.stop="openShip(row)">备货</el-button>
            <el-button v-if="row.status !== 'awaiting_packaging'" size="small" @click.stop="downloadLabel(row)">面单</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination v-model:current-page="page" :page-size="size" :total="total" layout="total, prev, pager, next" @current-change="load" />
      </div>
    </div>

    <el-drawer v-model="detail.visible" :title="detail.order?.posting_number" :size="isMobile ? '100%' : '560px'">
      <template v-if="detail.order">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="状态">
            <el-tag :type="STATUS_TAG[detail.order.status] || 'info'">{{ statusText(detail.order.status) }}</el-tag>
            <span class="muted" style="margin-left: 8px">{{ detail.order.substatus }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="店铺">{{ shopName(detail.order.shop_id) }}</el-descriptions-item>
          <el-descriptions-item label="仓库">{{ detail.order.warehouse_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="下单时间">{{ fmtTime(detail.order.ozon_created_at) }}</el-descriptions-item>
          <el-descriptions-item label="最晚发货">{{ fmtTime(detail.order.shipment_date) }}</el-descriptions-item>
          <el-descriptions-item label="物流单号"><span class="mono">{{ detail.order.tracking_number || '-' }}</span></el-descriptions-item>
          <el-descriptions-item label="备货时间">{{ fmtTime(detail.order.shipment?.shipped_at) }}</el-descriptions-item>
          <el-descriptions-item label="面单">
            <span v-if="detail.order.shipment?.label_path">已获取</span>
            <span v-else-if="detail.order.shipment?.label_error" style="color: #f56c6c">{{ detail.order.shipment.label_error }}</span>
            <span v-else>-</span>
          </el-descriptions-item>
        </el-descriptions>

        <h4>商品</h4>
        <el-table :data="detail.order.items" size="small" border>
          <el-table-column prop="name" label="名称" min-width="140" />
          <el-table-column prop="offer_id" label="货号" />
          <el-table-column prop="sku" label="SKU" />
          <el-table-column prop="quantity" label="数量" width="60" />
          <el-table-column prop="price" label="单价" width="80" />
        </el-table>

        <div style="margin: 16px 0; display: flex; gap: 8px; flex-wrap: wrap">
          <el-button v-if="detail.order.status === 'awaiting_packaging'" type="primary" @click="openShip(detail.order)">备货</el-button>
          <el-button @click="downloadLabel(detail.order)">下载面单</el-button>
          <el-button :loading="detail.refreshing" @click="refreshOrder">从 Ozon 刷新</el-button>
        </div>

        <el-collapse>
          <el-collapse-item title="Ozon 原始数据（排查问题用）">
            <pre style="font-size: 12px; overflow: auto">{{ JSON.stringify(detail.order.raw_json, null, 2) }}</pre>
          </el-collapse-item>
        </el-collapse>
      </template>
    </el-drawer>

    <ShipDialog v-model="ship.visible" :order="ship.order" @done="afterShip" />
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { download, errorText, escapeHtml, showError } from '../api'
import ShipDialog from '../components/ShipDialog.vue'
import { STATUS_TAG, fmtTime, statusText } from '../constants'
import { useMobile } from '../mobile'
import { shopName, shops } from '../shops'

const isMobile = useMobile()
const filters = reactive({ shop_id: null, status: ['awaiting_packaging'], range: null, q: '' })
const rows = ref([])
const total = ref(0)
const page = ref(1)
const size = 50
const loading = ref(false)
const selected = ref([])
const statusCounts = ref({})
const detail = reactive({ visible: false, order: null, refreshing: false })
const ship = reactive({ visible: false, order: null })

function params() {
  const p = new URLSearchParams({ page: page.value, size })
  if (filters.shop_id) p.append('shop_id', filters.shop_id)
  filters.status.forEach((s) => p.append('status', s))
  if (filters.q) p.append('q', filters.q)
  if (filters.range) {
    p.append('created_from', filters.range[0].toISOString())
    const end = new Date(filters.range[1]); end.setHours(23, 59, 59)
    p.append('created_to', end.toISOString())
  }
  return p
}

async function load() {
  loading.value = true
  selected.value = []
  try {
    const [list, counts] = await Promise.all([
      http.get('/api/orders', { params: params() }),
      http.get('/api/orders/statuses', { params: filters.shop_id ? { shop_id: filters.shop_id } : {} }),
    ])
    rows.value = list.data.items
    total.value = list.data.total
    // 保证当前选中的状态在下拉框里一直可见
    statusCounts.value = Object.fromEntries([...filters.status.map((s) => [s, 0]), ...Object.entries(counts.data)])
  } catch (e) {
    showError(e)
  } finally {
    loading.value = false
  }
}

function search() { page.value = 1; load() }
onMounted(load)

async function openDetail(row) {
  try {
    detail.order = (await http.get(`/api/orders/${row.id}`)).data
    detail.visible = true
  } catch (e) { showError(e) }
}

async function refreshOrder() {
  detail.refreshing = true
  try {
    detail.order = (await http.post(`/api/orders/${detail.order.id}/refresh`)).data
    ElMessage.success('已刷新')
    load()
  } catch (e) { showError(e) } finally { detail.refreshing = false }
}

async function openShip(row) {
  ship.order = row.items ? row : (await http.get(`/api/orders/${row.id}`)).data
  ship.visible = true
}

function afterShip() {
  load()
  if (detail.visible) openDetail(detail.order)
}

async function downloadLabel(row) {
  try {
    await download('get', `/api/orders/${row.id}/label`, null, `${row.posting_number}.pdf`)
  } catch (e) { showError(e) }
}

async function batchShip() {
  const ids = selected.value.filter((o) => o.status === 'awaiting_packaging').map((o) => o.id)
  if (!ids.length) return ElMessage.warning('所选订单中没有“待备货”的订单')
  try {
    await ElMessageBox.confirm(`将对 ${ids.length} 个待备货订单逐单备货（每单一个包裹）。需要拆包的订单请单独操作。`, '批量备货',
      { confirmButtonText: '确认备货', cancelButtonText: '取消' })
  } catch { return }
  try {
    const { data } = await http.post('/api/orders/ship-batch', { order_ids: ids })
    const failed = data.filter((r) => !r.ok)
    const dry = data.some((r) => r.dry_run)
    const lines = failed.map((r) => escapeHtml(`${r.posting_number}：${r.error}`)).join('<br>')
    ElMessageBox.alert(
      `${dry ? '试运行模式，未提交到 Ozon。<br>' : ''}成功 ${data.length - failed.length} 单，失败 ${failed.length} 单${lines ? '<br><br>' + lines : ''}`,
      '批量备货结果', { dangerouslyUseHTMLString: true })
    load()
  } catch (e) { showError(e) }
}

async function batchLabels() {
  const list = selected.value
  if (new Set(list.map((o) => o.shop_id)).size > 1) return ElMessage.warning('批量面单只能选择同一店铺的订单')
  if (list.length > 20) return ElMessage.warning('一次最多 20 单')
  try {
    await download('post', '/api/orders/labels', { order_ids: list.map((o) => o.id) }, 'labels.pdf')
  } catch (e) {
    ElMessage.error(await errorText(e))
  }
}
</script>

<style scoped>
.order-card { border-bottom: 1px solid #ebeef5; padding: 10px 0; font-size: 14px; line-height: 1.6; }
.order-card-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
</style>
