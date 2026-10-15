import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/Login.vue') },
    {
      path: '/',
      component: () => import('./views/Layout.vue'),
      redirect: '/orders',
      children: [
        { path: 'orders', name: 'orders', component: () => import('./views/Orders.vue'), meta: { title: '订单' } },
        { path: 'stock', name: 'stock', component: () => import('./views/Stock.vue'), meta: { title: '库存' } },
        { path: 'shops', name: 'shops', component: () => import('./views/Shops.vue'), meta: { title: '店铺' } },
        { path: 'market', name: 'market', component: () => import('./views/Market.vue'), meta: { title: '选品' } },
        { path: 'logs', name: 'logs', component: () => import('./views/Logs.vue'), meta: { title: '日志' } },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach((to) => {
  if (to.name !== 'login' && !localStorage.getItem('ozon_token')) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
})

export default router
