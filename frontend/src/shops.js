// 店铺列表在多个页面共用，缓存一份
import { ref } from 'vue'
import http from './api'

export const shops = ref([])

export async function loadShops() {
  shops.value = (await http.get('/api/shops')).data
  return shops.value
}

export const shopName = (id) => shops.value.find((s) => s.id === id)?.name || `店铺 ${id}`
