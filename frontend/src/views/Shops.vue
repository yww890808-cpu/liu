<template>
  <div>
    <div class="toolbar">
      <h1 class="page-title" style="margin: 0">店铺</h1>
      <span class="spacer" />
      <el-button type="primary" @click="openAdd">添加店铺</el-button>
    </div>

    <div class="card">
      <el-table :data="shops" v-loading="loading" row-key="id" @expand-change="loadWarehouses">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div style="padding: 0 16px">
              <div class="muted" style="margin-bottom: 8px">仓库（关闭后不向该仓库推送库存）</div>
              <el-table :data="warehouses[row.id] || []" size="small">
                <el-table-column prop="name" label="仓库" />
                <el-table-column prop="ozon_warehouse_id" label="Ozon 仓库 ID" />
                <el-table-column prop="status" label="Ozon 状态" />
                <el-table-column label="推送库存" width="100">
                  <template #default="{ row: wh }">
                    <el-switch v-model="wh.is_active" @change="(v) => toggleWarehouse(row.id, wh, v)" />
                  </template>
                </el-table-column>
              </el-table>
              <div v-if="!(warehouses[row.id] || []).length" class="muted">暂无仓库，请先点“同步商品”。</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="店铺名称" min-width="120" />
        <el-table-column prop="client_id" label="Client-Id" min-width="100" />
        <el-table-column prop="api_key_masked" label="Api-Key" width="110" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="上次同步商品" width="150">
          <template #default="{ row }">{{ fmtTime(row.last_sync_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" min-width="330">
          <template #default="{ row }">
            <el-button size="small" @click="testConn(row)" :loading="busy[`test${row.id}`]">测试连接</el-button>
            <el-button size="small" @click="trigger(row, 'sync', '商品与仓库同步')">同步商品</el-button>
            <el-button size="small" @click="trigger(row, 'sync-orders', '订单拉取')">拉取订单</el-button>
            <el-button size="small" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialog.visible" :title="dialog.id ? '编辑店铺' : '添加店铺'" width="460px">
      <el-form :model="dialog.form" label-width="90px">
        <el-form-item label="店铺名称" required>
          <el-input v-model="dialog.form.name" />
        </el-form-item>
        <el-form-item label="Client-Id" required>
          <el-input v-model="dialog.form.client_id" :disabled="!!dialog.id" />
        </el-form-item>
        <el-form-item label="Api-Key" :required="!dialog.id">
          <el-input v-model="dialog.form.api_key" type="password" show-password
                    :placeholder="dialog.id ? '不修改请留空' : '在 Ozon 卖家后台 设置 → API 密钥 中生成'" />
        </el-form-item>
        <el-form-item v-if="dialog.id" label="启用">
          <el-switch v-model="dialog.form.is_active" />
          <span class="muted" style="margin-left: 8px">停用后不再自动拉单、推送库存</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { showError } from '../api'
import { fmtTime } from '../constants'
import { loadShops, shops } from '../shops'

const loading = ref(false)
const busy = reactive({})
const warehouses = reactive({})
const dialog = reactive({ visible: false, id: null, saving: false, form: {} })

async function refresh() {
  loading.value = true
  try { await loadShops() } catch (e) { showError(e) } finally { loading.value = false }
}

onMounted(refresh)

function openAdd() {
  Object.assign(dialog, { visible: true, id: null, form: { name: '', client_id: '', api_key: '' } })
}

function openEdit(row) {
  Object.assign(dialog, { visible: true, id: row.id, form: { name: row.name, client_id: row.client_id, api_key: '', is_active: row.is_active } })
}

async function save() {
  const f = dialog.form
  if (!f.name || !f.client_id || (!dialog.id && !f.api_key)) return ElMessage.warning('请填写完整')
  dialog.saving = true
  try {
    if (dialog.id) {
      const body = { name: f.name, is_active: f.is_active }
      if (f.api_key) body.api_key = f.api_key
      await http.patch(`/api/shops/${dialog.id}`, body)
    } else {
      await http.post('/api/shops', { name: f.name, client_id: f.client_id, api_key: f.api_key })
    }
    dialog.visible = false
    ElMessage.success('已保存')
    refresh()
  } catch (e) {
    showError(e)
  } finally {
    dialog.saving = false
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`删除店铺“${row.name}”会同时删除它的商品、仓库、订单和库存数据，不影响 Ozon 后台。确定删除？`,
      '删除店铺', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  try {
    await http.delete(`/api/shops/${row.id}`)
    ElMessage.success('已删除')
    refresh()
  } catch (e) { showError(e) }
}

async function testConn(row) {
  busy[`test${row.id}`] = true
  try {
    const { data } = await http.post(`/api/shops/${row.id}/test`)
    if (data.ok) ElMessage.success(`连接成功，共 ${data.warehouses} 个仓库`)
    else ElMessageBox.alert(data.error, '连接失败', { type: 'error' })
  } catch (e) { showError(e) } finally { busy[`test${row.id}`] = false }
}

async function trigger(row, action, label) {
  try {
    await http.post(`/api/shops/${row.id}/${action}`)
    ElMessage.success(`${label}已开始，在后台执行，结果可在“日志”页查看`)
  } catch (e) { showError(e) }
}

async function loadWarehouses(row) {
  try {
    warehouses[row.id] = (await http.get(`/api/shops/${row.id}/warehouses`)).data
  } catch (e) { showError(e) }
}

async function toggleWarehouse(shopId, wh, value) {
  try {
    await http.patch(`/api/shops/${shopId}/warehouses/${wh.id}`, { is_active: value })
  } catch (e) {
    wh.is_active = !value
    showError(e)
  }
}
</script>
