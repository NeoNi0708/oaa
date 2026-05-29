<template>
  <div class="survey-container">
    <div class="survey-header">
      <h3 class="survey-title">{{ title }}</h3>
      <p v-if="description" class="survey-desc">{{ description }}</p>
    </div>

    <div class="survey-questions">
      <div v-for="q in questions" :key="q.id" class="survey-question">
        <label class="question-label">{{ q.label }}</label>

        <!-- Single choice (radio) -->
        <div v-if="q.type === 'single'" class="question-options">
          <label v-for="opt in q.options" :key="opt" class="option-row">
            <input type="radio" :name="q.id" :value="opt"
              @change="setAnswer(q.id, opt)" class="option-radio" />
            <span class="option-text">{{ opt }}</span>
          </label>
        </div>

        <!-- Multiple choice (checkboxes) -->
        <div v-else-if="q.type === 'multiple'" class="question-options">
          <label v-for="opt in q.options" :key="opt" class="option-row">
            <input type="checkbox" :value="opt"
              @change="toggleMulti(q.id, opt)" class="option-radio" />
            <span class="option-text">{{ opt }}</span>
          </label>
        </div>

        <!-- Text input -->
        <div v-else-if="q.type === 'text'" class="question-options">
          <input v-model="textAnswers[q.id]" type="text" class="oaa-input survey-text-input"
            :placeholder="'请输入'" @input="setAnswer(q.id, textAnswers[q.id])" />
        </div>
      </div>
    </div>

    <div class="survey-actions">
      <span v-if="errorMsg" class="survey-error">{{ errorMsg }}</span>
      <button class="oaa-btn oaa-btn--primary" @click="submitSurvey"
        :disabled="submitting">
        {{ submitting ? '提交中...' : '提交问卷' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'

const props = defineProps<{
  surveyId: string
  title: string
  description: string
  questions: any[]
  sendRequest: Function
}>()

const emit = defineEmits(['submitted'])

const answers = reactive<Record<string, any>>({})
const textAnswers = reactive<Record<string, string>>({})
const submitting = ref(false)
const errorMsg = ref('')

function setAnswer(qId: string, value: any) {
  answers[qId] = value
  errorMsg.value = ''
}

function toggleMulti(qId: string, opt: string) {
  if (!answers[qId]) answers[qId] = []
  const arr = answers[qId] as string[]
  const idx = arr.indexOf(opt)
  if (idx >= 0) arr.splice(idx, 1)
  else arr.push(opt)
  errorMsg.value = ''
}

async function submitSurvey() {
  // Validate all questions answered
  for (const q of props.questions) {
    const val = answers[q.id]
    if (q.type === 'text' && (!textAnswers[q.id] || !textAnswers[q.id].trim())) {
      errorMsg.value = `请填写"${q.label}"`
      return
    }
    if (q.type !== 'text' && (!val || (Array.isArray(val) && val.length === 0))) {
      errorMsg.value = `请选择"${q.label}"`
      return
    }
  }

  // For text questions, ensure they're in answers
  for (const q of props.questions) {
    if (q.type === 'text' && textAnswers[q.id]) {
      answers[q.id] = textAnswers[q.id].trim()
    }
  }

  submitting.value = true
  errorMsg.value = ''
  try {
    const resp = await props.sendRequest('submit_survey', {
      survey_id: props.surveyId,
      answers: { ...answers },
    })
    if (resp.ok) {
      emit('submitted', { ...answers })
    } else {
      errorMsg.value = resp.error || '提交失败'
    }
  } catch (e: any) {
    errorMsg.value = e.message || '网络错误'
  }
  submitting.value = false
}
</script>

<style scoped>
.survey-container {
  background: var(--oaa-bg-card, #fff);
  border: 1px solid var(--oaa-border, #e0e0e0);
  border-radius: 8px;
  padding: 16px;
  margin: 8px 0;
}
.survey-header { margin-bottom: 16px; }
.survey-title { margin: 0 0 4px; font-size: 15px; font-weight: 600; }
.survey-desc { margin: 0; font-size: 13px; color: var(--oaa-color-muted, #888); }
.survey-question { margin-bottom: 14px; }
.question-label { display: block; font-size: 13px; font-weight: 500; margin-bottom: 6px; }
.question-options { display: flex; flex-direction: column; gap: 4px; }
.option-row {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 8px; border-radius: 4px; cursor: pointer;
  font-size: 13px;
}
.option-row:hover { background: var(--oaa-bg-hover, #f5f5f5); }
.option-radio { accent-color: var(--oaa-primary, #4a6cf7); }
.option-text { }
.survey-text-input { width: 100%; box-sizing: border-box; margin-top: 4px; }
.survey-actions { display: flex; align-items: center; gap: 12px; margin-top: 8px; }
.survey-error { color: var(--oaa-red-400, #e74c3c); font-size: 12px; }
</style>
