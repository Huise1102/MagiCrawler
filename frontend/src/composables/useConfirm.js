import { reactive } from 'vue'

/** 全局确认对话框状态（由 ConfirmDialog 组件渲染）。 */
export const confirmState = reactive({
  visible: false,
  title: '确认操作',
  message: '',
  confirmText: '确定',
  cancelText: '取消',
  danger: false,
})

let resolver = null

/**
 * 弹出确认对话框，返回 Promise<boolean>（确定 true / 取消 false）。
 */
export function confirmDialog(options = {}) {
  if (resolver) resolveConfirm(false)
  confirmState.title = options.title || '确认操作'
  confirmState.message = options.message || ''
  confirmState.confirmText = options.confirmText || '确定'
  confirmState.cancelText = options.cancelText || '取消'
  confirmState.danger = Boolean(options.danger)
  confirmState.visible = true
  return new Promise((resolve) => {
    resolver = resolve
  })
}

export function resolveConfirm(value) {
  confirmState.visible = false
  const done = resolver
  resolver = null
  if (done) done(value)
}
