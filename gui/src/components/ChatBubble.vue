<template>
  <div class="chat-bubble">
    <template v-for="(seg, i) in segments" :key="i">
      <div v-if="seg.type === 'markdown'" class="bubble-markdown" v-html="seg.html"></div>
      <ActionButtons
        v-else-if="seg.type === 'actions'"
        :actions="seg.actions"
        :disabled="streaming"
        :sendRequest="sendRequest"
      />
      <ChartView v-else-if="seg.type === 'chart'" :option="seg.option" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { parseContent } from '../utils/contentParser'
import type { ChatMessage } from '../composables/useWebSocket'
import ActionButtons from './ActionButtons.vue'
import ChartView from './ChartView.vue'

const props = defineProps<{
  message: ChatMessage
  streaming: boolean
  sendRequest: (type: string, payload?: Record<string, unknown>, timeout?: number) => Promise<any>
}>()

const segments = computed(() => parseContent(props.message.content || ''))
</script>

<style scoped>
.chat-bubble {
  line-height: 1.6;
}
.bubble-markdown {
  /* All markdown styling handled by ChatView's global msg-bubble styles */
}
.bubble-markdown :deep(p) {
  margin: 0.4em 0;
}
.bubble-markdown :deep(p:first-child) {
  margin-top: 0;
}
.bubble-markdown :deep(p:last-child) {
  margin-bottom: 0;
}
</style>
