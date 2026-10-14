// Ozon FBS 货件状态中文名
export const ORDER_STATUS = {
  awaiting_registration: '等待注册',
  acceptance_in_progress: '验收中',
  awaiting_approve: '等待确认',
  awaiting_packaging: '待备货',
  awaiting_deliver: '待发运',
  arbitration: '仲裁中',
  client_arbitration: '客户仲裁',
  delivering: '运输中',
  driver_pickup: '司机取件',
  delivered: '已签收',
  cancelled: '已取消',
  not_accepted: '未被接收',
  sent_by_seller: '卖家已发出',
}

export const STATUS_TAG = {
  awaiting_packaging: 'warning',
  awaiting_deliver: 'primary',
  delivering: 'primary',
  delivered: 'success',
  cancelled: 'info',
  not_accepted: 'danger',
  arbitration: 'danger',
  client_arbitration: 'danger',
}

export const TASK_NAME = {
  sync_catalog: '同步商品/仓库',
  sync_orders: '拉取订单',
  fetch_label: '获取面单',
  refresh_stock: '读取 Ozon 库存',
  push_stock: '推送库存',
  ship_dry_run: '备货（试运行）',
  stock_push_dry_run: '推送库存（试运行）',
}

export const statusText = (s) => ORDER_STATUS[s] || s

export function fmtTime(v) {
  if (!v) return '-'
  const d = new Date(v)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}
