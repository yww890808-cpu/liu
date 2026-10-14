// 窄屏（手机）判断
import { onBeforeUnmount, onMounted, ref } from 'vue'

export function useMobile(width = 768) {
  const isMobile = ref(window.innerWidth < width)
  const update = () => { isMobile.value = window.innerWidth < width }
  onMounted(() => window.addEventListener('resize', update))
  onBeforeUnmount(() => window.removeEventListener('resize', update))
  return isMobile
}
