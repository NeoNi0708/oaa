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
    <SurveyForm
      v-if="message.survey"
      :surveyId="message.survey.surveyId"
      :title="message.survey.title"
      :description="message.survey.description"
      :questions="message.survey.questions"
      :sendRequest="sendRequest"
      @submitted="onSurveySubmitted"
    />
    <FilePreview
      v-if="message.filePreview"
      :path="message.filePreview.path"
      :fileType="message.filePreview.fileType"
      :title="message.filePreview.title"
      :size="message.filePreview.size"
    />
    <TaskBoard
      v-if="message.taskboard"
      :items="message.taskboard.items"
    />
    <ChoicesForm
      v-if="message.choices"
      :question="message.choices.question"
      :options="message.choices.options"
      :sendRequest="sendRequest"
      @selected="onChoiceSelected"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { parseContent } from '../utils/contentParser'
import type { ChatMessage } from '../composables/useWebSocket'
import ActionButtons from './ActionButtons.vue'
import ChartView from './ChartView.vue'
import SurveyForm from './SurveyForm.vue'
import FilePreview from './FilePreview.vue'
import TaskBoard from './TaskBoard.vue'
import ChoicesForm from './ChoicesForm.vue'

const props = defineProps<{
  message: ChatMessage
  streaming: boolean
  sendRequest: (type: string, payload?: Record<string, unknown>, timeout?: number) => Promise<any>
}>()

const emit = defineEmits<{ (e: 'survey-submitted', surveyId: string, answers: any): void; (e: 'choice-selected', value: string, question: string): void }>()

const segments = computed(() => parseContent(props.message.content || ''))

function onSurveySubmitted(answers: any) {
  emit('survey-submitted', props.message.survey?.surveyId || '', answers)
}
function onChoiceSelected(value: string, question: string) {
  emit('choice-selected', value, question)
}
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
