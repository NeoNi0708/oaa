<template>
  <div class="choices-card">
    <p class="choices-question">{{ question }}</p>
    <div class="choices-options">
      <button v-for="opt in options" :key="opt.value"
        class="oaa-btn oaa-btn--sm" :class="selected === opt.value ? 'oaa-btn--primary' : 'oaa-btn--secondary'"
        @click="select(opt.value)" :disabled="submitting">
        {{ opt.label }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  question: string
  options: { label: string; value: string }[]
  sendRequest: Function
}>()

const emit = defineEmits<{ (e: 'selected', value: string, question: string): void }>()
const selected = ref('')
const submitting = ref(false)

async function select(value: string) {
  selected.value = value
  submitting.value = true
  try {
    await props.sendRequest('submit_choice', { choice: value, question: props.question })
    emit('selected', value, props.question)
  } catch {}
  submitting.value = false
}
</script>

<style scoped>
.choices-card {
  background: var(--oaa-bg-card, #fff);
  border: 1px solid var(--oaa-border, #e0e0e0);
  border-radius: 8px;
  padding: 12px 16px;
  margin: 8px 0;
}
.choices-question { margin: 0 0 10px; font-size: 13px; font-weight: 500; }
.choices-options { display: flex; flex-wrap: wrap; gap: 8px; }
</style>
