<template>
  <div>
    <h1 class="page-title">选品</h1>

    <el-alert v-if="status && !status.configured" type="warning" :closable="false" show-icon style="margin-bottom: 12px"
              title="尚未配置 MPStats 数据服务">
      在 MPStats（mpstats.io）开通订阅后，到个人中心“API token”处复制 token，填入服务器 .env 文件的 MPSTATS_TOKEN=，
      执行 docker compose up -d 后生效。未配置前可以先添加关注类目（需手动填写类目路径）。
    </el-alert>
    <div v-else-if="status?.limit" class="muted" style="margin-bottom: 8px">
      MPStats 调用额度：已用 {{ status.limit.use }} / {{ status.limit.available }}
    </div>
    <el-alert v-else-if="status?.error" type="error" :closable="false" :title="`MPStats 连接失败：${status.error}`" style="margin-bottom: 12px" />

    <el-tabs v-model="tab" class="card">
      <!-- ================= 商品排行 ================= -->
      <el-tab-pane label="商品排行" name="rank">
        <div class="toolbar">
          <el-select v-model="f.category_id" placeholder="全部类目" clearable style="width: 160px" @change="search">
            <el-option v-for="c in categories" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
          <el-input v-model="f.q" placeholder="商品名 / 品牌 / 卖家" clearable style="width: 180px" @keyup.enter="search" @clear="search" />
          <el-input-number v-model="f.price_min" :min="0" placeholder="最低价 ₽" controls-position="right" style="width: 120px" />
          <el-input-number v-model="f.price_max" :min="0" placeholder="最高价 ₽" controls-position="right" style="width: 120px" />
          <el-input-number v-model="f.min_sales_30d" :min="0" placeholder="30天销量≥" controls-position="right" style="width: 130px" />
          <el-input-number v-model="f.min_growth" placeholder="周增长%≥" controls-position="right" style="width: 130px" />
          <el-checkbox v-model="f.favorites" @change="search">只看候选</el-checkbox>
          <el-button type="primary" @click="search">查询</el-button>
          <span class="spacer" />
          <span class="muted" v-if="asOf">数据截至 {{ asOf }}</span>
        </div>

        <el-table :data="rows" v-loading="loading" @sort-change="onSort" @row-click="openProduct" style="cursor: pointer"
                  :default-sort="{ prop: 'revenue_30d', order: 'descending' }">
          <el-table-column width="64">
            <template #default="{ row }">
              <el-image v-if="row.thumb" :src="row.thumb" fit="cover" style="width: 48px; height: 48px; border-radius: 4px" lazy />
            </template>
          </el-table-column>
          <el-table-column label="商品" min-width="240">
            <template #default="{ row }">
              <div class="pname">{{ row.name }}</div>
              <div class="muted">{{ row.brand || '-' }} · {{ row.seller || '-' }} · {{ row.category_name || '-' }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="price" label="价格 ₽" width="90" sortable="custom" align="right">
            <template #default="{ row }">{{ num(row.price) }}</template>
          </el-table-column>
          <el-table-column prop="sales_7d" label="7天销量" width="100" sortable="custom" align="right">
            <template #default="{ row }">{{ num(row.sales_7d) }}</template>
          </el-table-column>
          <el-table-column prop="growth_pct" label="周增长" width="95" sortable="custom" align="right">
            <template #default="{ row }">
              <span v-if="row.growth_pct == null" class="muted">新</span>
              <span v-else>{{ row.growth_pct > 0 ? '↑' : row.growth_pct < 0 ? '↓' : '' }}{{ Math.abs(row.growth_pct) }}%</span>
            </template>
          </el-table-column>
          <el-table-column prop="sales_30d" label="30天销量" width="105" sortable="custom" align="right">
            <template #default="{ row }">{{ num(row.sales_30d) }}</template>
          </el-table-column>
          <el-table-column prop="revenue_30d" label="30天销售额 ₽" width="130" sortable="custom" align="right">
            <template #default="{ row }">{{ num(row.revenue_30d) }}</template>
          </el-table-column>
          <el-table-column prop="reviews" label="评论" width="95" sortable="custom" align="right">
            <template #default="{ row }">
              {{ num(row.reviews) }}<div v-if="row.reviews_growth" class="muted">+{{ row.reviews_growth }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="rating" label="评分" width="75" sortable="custom" align="right" />
          <el-table-column prop="days_with_sales" label="有销天数" width="100" sortable="custom" align="right" />
          <el-table-column label="候选" width="60" align="center">
            <template #default="{ row }">
              <el-button link :type="row.is_favorite ? 'warning' : 'info'" @click.stop="toggleFav(row)">
                {{ row.is_favorite ? '★' : '☆' }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pager">
          <el-pagination v-model:current-page="page" :page-size="size" :total="total" layout="total, prev, pager, next" @current-change="load" />
        </div>
        <el-empty v-if="!loading && !rows.length" description="还没有数据：先在“关注类目”里添加类目，系统会自动抓取最近 14 天的数据" />
      </el-tab-pane>

      <!-- ================= 关注类目 ================= -->
      <el-tab-pane label="关注类目" name="categories">
        <div class="toolbar">
          <span class="muted">每天自动抓取每个类目前一天销售额前 N 的商品；新增类目会回补最近 14 天。</span>
          <span class="spacer" />
          <el-button type="primary" @click="openAdd">添加类目</el-button>
        </div>
        <el-table :data="categories">
          <el-table-column prop="name" label="名称" min-width="120" />
          <el-table-column prop="path" label="类目路径" min-width="220" />
          <el-table-column label="每天抓取" width="140">
            <template #default="{ row }">
              <el-input-number v-model="row.top_n" :min="10" :max="5000" :step="100" size="small" controls-position="right"
                               style="width: 110px" @change="(v) => patchCat(row, { top_n: v })" />
            </template>
          </el-table-column>
          <el-table-column prop="products" label="商品数" width="80" align="right" />
          <el-table-column prop="last_fetched_date" label="数据截至" width="110" />
          <el-table-column label="启用" width="70">
            <template #default="{ row }"><el-switch v-model="row.is_active" @change="(v) => patchCat(row, { is_active: v })" /></template>
          </el-table-column>
          <el-table-column label="操作" width="210">
            <template #default="{ row }">
              <el-button size="small" @click="openCategory(row)">走势</el-button>
              <el-button size="small" @click="fetchNow(row)">立即抓取</el-button>
              <el-button size="small" link type="danger" @click="removeCat(row)">取消关注</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 添加类目 -->
    <el-dialog v-model="add.visible" title="添加关注类目" width="560px">
      <el-form label-width="90px">
        <el-form-item label="搜索类目">
          <el-select v-model="add.path" filterable remote :remote-method="searchCatalog" :loading="add.searching"
                     placeholder="输入俄语关键词，如 кружки、наушники" style="width: 100%" @change="onPick">
            <el-option v-for="c in add.options" :key="c.path" :label="c.path" :value="c.path" />
          </el-select>
        </el-form-item>
        <el-form-item label="或直接填写">
          <el-input v-model="add.path" placeholder="类目路径，如 Дом и сад/Посуда/Кружки" />
        </el-form-item>
        <el-form-item label="显示名称">
          <el-input v-model="add.name" placeholder="可写中文，如 马克杯" />
        </el-form-item>
        <el-form-item label="每天抓取">
          <el-input-number v-model="add.top_n" :min="10" :max="5000" :step="100" /> <span class="muted" style="margin-left: 8px">个销售额靠前的商品</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="add.visible = false">取消</el-button>
        <el-button type="primary" :disabled="!add.path" @click="saveCat">添加</el-button>
      </template>
    </el-dialog>

    <!-- 商品详情 -->
    <el-drawer v-model="product.visible" :size="isMobile ? '100%' : '640px'" :title="product.data?.name">
      <template v-if="product.data">
        <div style="display: flex; gap: 12px; margin-bottom: 12px">
          <el-image v-if="product.data.thumb" :src="product.data.thumb" style="width: 96px; height: 96px; border-radius: 6px" fit="cover" />
          <div>
            <div>{{ product.data.brand || '-' }} · {{ product.data.seller || '-' }}</div>
            <div class="muted">SKU {{ product.data.ozon_sku }} · 首次出现 {{ product.data.first_seen }}</div>
            <a :href="product.data.url" target="_blank" rel="noopener">在 Ozon 打开 ↗</a>
            <div style="margin-top: 6px">
              <el-button size="small" :type="product.data.is_favorite ? 'warning' : ''" @click="toggleFav(product.data)">
                {{ product.data.is_favorite ? '★ 已加入候选' : '☆ 加入候选' }}
              </el-button>
            </div>
          </div>
        </div>
        <el-input v-model="product.data.note" type="textarea" :rows="2" placeholder="备注：货源、利润估算、是否可做……" @change="saveNote" />
        <div style="margin-top: 16px">
          <LineChart title="日销量（件）" :labels="productLabels" :series="[{ name: '销量', color: C1, values: product.data.history.map((h) => h.sales) }]" />
          <LineChart title="价格（₽）" :zero="false" :labels="productLabels" :series="[{ name: '价格', color: C1, values: product.data.history.map((h) => num0(h.price)) }]" />
          <LineChart title="评论数" :zero="false" :labels="productLabels" :series="[{ name: '评论数', color: C1, values: product.data.history.map((h) => h.reviews) }]" />
        </div>
      </template>
    </el-drawer>

    <!-- 类目走势 -->
    <el-drawer v-model="cat.visible" :size="isMobile ? '100%' : '640px'" :title="`类目走势：${cat.row?.name || ''}`">
      <template v-if="cat.history">
        <div class="muted" style="margin-bottom: 8px">“跟踪商品合计”是本系统每天抓取的前 {{ cat.row.top_n }} 个商品之和；“全类目”来自数据服务的类目汇总（有数据时显示）。</div>
        <LineChart title="日销量（件）" :labels="catLabels" :series="catSeries('sales', 'tracked_sales')" />
        <LineChart title="日销售额（₽）" :labels="catLabels" :series="catSeries('revenue', 'tracked_revenue')" />
        <LineChart v-if="cat.history.some((h) => h.sellers != null)" title="有销量的卖家数" :labels="catLabels"
                   :series="[{ name: '卖家数', color: C1, values: cat.history.map((h) => h.sellers) }]" />
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { showError } from '../api'
import LineChart from '../components/LineChart.vue'
import { useMobile } from '../mobile'

// 分类色（已用校验脚本验证：浅色背景下色觉障碍可区分、对比度达标）
const C1 = '#2a78d6'
const C2 = '#eb6834'

const isMobile = useMobile()
const tab = ref('rank')
const status = ref(null)
const categories = ref([])
const f = reactive({ category_id: null, q: '', price_min: null, price_max: null, min_sales_30d: null, min_growth: null, favorites: false })
const sort = reactive({ prop: 'revenue_30d', desc: true })
const rows = ref([])
const total = ref(0)
const asOf = ref(null)
const page = ref(1)
const size = 50
const loading = ref(false)
const add = reactive({ visible: false, path: '', name: '', top_n: 500, options: [], searching: false })
const product = reactive({ visible: false, data: null })
const cat = reactive({ visible: false, row: null, history: null })

const num = (v) => (v == null ? '-' : Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 }))
const num0 = (v) => (v == null ? null : Number(v))
const short = (d) => String(d).slice(5)

onMounted(async () => {
  http.get('/api/market/status').then((r) => (status.value = r.data)).catch(() => {})
  await loadCategories()
  load()
})

async function loadCategories() {
  try { categories.value = (await http.get('/api/market/categories')).data } catch (e) { showError(e) }
}

async function load() {
  loading.value = true
  try {
    const params = { ...f, sort: sort.prop, desc: sort.desc, page: page.value, size }
    Object.keys(params).forEach((k) => (params[k] === null || params[k] === '') && delete params[k])
    const { data } = await http.get('/api/market/products', { params })
    rows.value = data.items
    total.value = data.total
    asOf.value = data.as_of
  } catch (e) { showError(e) } finally { loading.value = false }
}

function search() { page.value = 1; load() }

function onSort({ prop, order }) {
  sort.prop = order ? prop : 'revenue_30d'
  sort.desc = order !== 'ascending'
  search()
}

async function toggleFav(row) {
  try {
    const { data } = await http.patch(`/api/market/products/${row.id}`, { is_favorite: !row.is_favorite })
    row.is_favorite = data.is_favorite
    const listed = rows.value.find((r) => r.id === row.id)
    if (listed) listed.is_favorite = data.is_favorite
  } catch (e) { showError(e) }
}

async function openProduct(row) {
  try {
    product.data = (await http.get(`/api/market/products/${row.id}`)).data
    product.visible = true
  } catch (e) { showError(e) }
}

const productLabels = computed(() => (product.data?.history || []).map((h) => short(h.day)))

async function saveNote() {
  try {
    await http.patch(`/api/market/products/${product.data.id}`, { note: product.data.note })
    ElMessage.success('备注已保存')
  } catch (e) { showError(e) }
}

// ---------- 类目 ----------

function openAdd() { Object.assign(add, { visible: true, path: '', name: '', top_n: 500, options: [] }) }

async function searchCatalog(q) {
  if (!q) return
  add.searching = true
  try { add.options = (await http.get('/api/market/catalog', { params: { q } })).data } catch (e) { showError(e) } finally { add.searching = false }
}

function onPick(path) {
  const c = add.options.find((o) => o.path === path)
  if (c && !add.name) add.name = c.name
}

async function saveCat() {
  try {
    await http.post('/api/market/categories', { path: add.path, name: add.name || null, top_n: add.top_n })
    add.visible = false
    ElMessage.success('已添加，正在后台回补最近 14 天数据，稍后刷新查看')
    loadCategories()
  } catch (e) { showError(e) }
}

async function patchCat(row, body) {
  try { await http.patch(`/api/market/categories/${row.id}`, body) } catch (e) { showError(e); loadCategories() }
}

async function fetchNow(row) {
  try {
    await http.post(`/api/market/categories/${row.id}/fetch`)
    ElMessage.success('已开始抓取，结果见“日志”页')
  } catch (e) { showError(e) }
}

async function removeCat(row) {
  try {
    await ElMessageBox.confirm(`取消关注“${row.name}”后不再每天抓取，已有的商品和历史数据会保留。`, '取消关注',
      { type: 'warning', confirmButtonText: '取消关注', cancelButtonText: '返回' })
  } catch { return }
  try { await http.delete(`/api/market/categories/${row.id}`); loadCategories() } catch (e) { showError(e) }
}

async function openCategory(row) {
  try {
    cat.history = (await http.get(`/api/market/categories/${row.id}/history`)).data
    cat.row = row
    cat.visible = true
  } catch (e) { showError(e) }
}

const catLabels = computed(() => (cat.history || []).map((h) => short(h.day)))

// 全类目汇总缺失时只画一条线（单条线不需要图例）
function catSeries(totalKey, trackedKey) {
  const h = cat.history
  const s = [{ name: '跟踪商品合计', color: C1, values: h.map((d) => num0(d[trackedKey])) }]
  if (h.some((d) => d[totalKey] != null)) s.push({ name: '全类目', color: C2, values: h.map((d) => num0(d[totalKey])) })
  return s
}
</script>

<style scoped>
.pname { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
</style>
