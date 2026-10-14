<template>
  <el-dialog :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)"
             :title="`备货确认：${order?.posting_number || ''}`" width="640px" class="ship-dialog">
    <template v-if="order">
      <div class="muted" style="margin-bottom: 8px">
        默认整单一个包裹。商品放不进一个包裹时，点“添加包裹”拆分，所有包裹的数量合计必须等于订单数量。
      </div>
      <el-table :data="order.items" size="small" border>
        <el-table-column label="商品" min-width="160">
          <template #default="{ row }">
            <div>{{ row.name }}</div>
            <div class="muted">{{ row.offer_id }} · SKU {{ row.sku }}</div>
          </template>
        </el-table-column>
        <el-table-column label="订单数量" width="80" align="center" prop="quantity" />
        <el-table-column v-for="(pkg, i) in packages" :key="i" :label="`包裹 ${i + 1}`" width="110" align="center">
          <template #header>
            包裹 {{ i + 1 }}
            <el-button v-if="packages.length > 1" link type="danger" size="small" @click="packages.splice(i, 1)">删除</el-button>
          </template>
          <template #default="{ row }">
            <el-input-number v-model="pkg[row.sku]" :min="0" :max="row.quantity" size="small" controls-position="right" style="width: 90px" />
          </template>
        </el-table-column>
        <el-table-column label="未分配" width="70" align="center">
          <template #default="{ row }">
            <span :style="{ color: remaining(row) === 0 ? '#67c23a' : '#f56c6c' }">{{ remaining(row) }}</span>
          </template>
        </el-table-column>
      </el-table>
      <el-button style="margin-top: 8px" @click="addPackage">添加包裹</el-button>
    </template>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="!valid" @click="submit">确认备货</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { escapeHtml, showError } from '../api'

const props = defineProps({ modelValue: Boolean, order: Object })
const emit = defineEmits(['update:modelValue', 'done'])
const packages = ref([])
const saving = ref(false)

// 每次打开时重置为“整单一个包裹”
watch(() => [props.modelValue, props.order], () => {
  if (props.modelValue && props.order) {
    packages.value = [Object.fromEntries(props.order.items.map((i) => [i.sku, i.quantity]))]
  }
})

function addPackage() {
  packages.value.push(Object.fromEntries(props.order.items.map((i) => [i.sku, 0])))
}

const remaining = (item) => item.quantity - packages.value.reduce((s, p) => s + (p[item.sku] || 0), 0)

const valid = computed(() => props.order && props.order.items.every((i) => remaining(i) === 0)
  && packages.value.every((p) => Object.values(p).some((q) => q > 0)))

async function submit() {
  const body = {
    packages: packages.value.map((p) => Object.entries(p)
      .filter(([, q]) => q > 0)
      .map(([sku, quantity]) => ({ sku: Number(sku), quantity }))),
  }
  saving.value = true
  try {
    const { data } = await http.post(`/api/orders/${props.order.id}/ship`, body)
    emit('update:modelValue', false)
    if (data.dry_run) {
      ElMessageBox.alert(
        `<p>试运行模式，未提交到 Ozon。将要提交的内容：</p><pre style="max-height:300px;overflow:auto;font-size:12px">${escapeHtml(JSON.stringify(data.payload, null, 2))}</pre>`,
        '试运行', { dangerouslyUseHTMLString: true })
    } else {
      ElMessage.success(`备货成功，货件号：${data.posting_numbers.join('、')}。面单约 1 分钟后自动获取`)
    }
    emit('done')
  } catch (e) {
    showError(e)
  } finally {
    saving.value = false
  }
}
</script>
