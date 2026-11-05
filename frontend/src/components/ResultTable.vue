<script setup>
import { computed } from 'vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
})

const columns = computed(() => {
  const cols = []
  for (const item of props.items) {
    for (const key of Object.keys(item || {})) {
      if (!cols.includes(key)) cols.push(key)
    }
  }
  return cols
})

function display(value) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
</script>

<template>
  <div class="table-wrap">
    <table class="table">
      <thead>
        <tr>
          <th class="idx">#</th>
          <th v-for="col in columns" :key="col">{{ col }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(item, i) in items" :key="i">
          <td class="idx">{{ i + 1 }}</td>
          <td v-for="col in columns" :key="col" :class="{ muted: display(item[col]) === '—' }">
            {{ display(item[col]) }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
