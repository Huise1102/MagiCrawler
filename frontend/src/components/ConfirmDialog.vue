<script setup>
import { confirmState, resolveConfirm } from '../composables/useConfirm'
</script>

<template>
  <Teleport to="body">
    <div v-if="confirmState.visible" class="modal-backdrop" @click.self="resolveConfirm(false)">
      <div class="modal narrow confirm-modal" role="alertdialog" aria-modal="true">
        <div class="modal-head">
          <h2>{{ confirmState.title }}</h2>
          <button type="button" class="modal-close" aria-label="关闭" @click="resolveConfirm(false)">
            ×
          </button>
        </div>
        <div class="modal-body">
          <p class="confirm-message">{{ confirmState.message }}</p>
        </div>
        <div class="modal-foot">
          <button type="button" class="btn ghost" @click="resolveConfirm(false)">
            {{ confirmState.cancelText }}
          </button>
          <button
            type="button"
            class="btn primary"
            :class="{ danger: confirmState.danger }"
            @click="resolveConfirm(true)"
          >
            {{ confirmState.confirmText }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
