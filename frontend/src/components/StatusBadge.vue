<script setup>
const LABELS = {
  pending: '等待中',
  running: '运行中',
  success: '成功',
  failed: '失败',
  partial: '部分完成',
}

defineProps({
  status: { type: String, default: '' },
})
</script>

<template>
  <span class="badge" :class="`badge-${status || 'pending'}`">
    <span class="dot" />
    {{ LABELS[status] || status || '未知' }}
  </span>
</template>
