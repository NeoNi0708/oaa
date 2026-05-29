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
              :checked="answers[q.id] === opt"
              @change="setAnswer(q.id, opt)" class="option-radio" />
            <span class="option-text">{{ opt }}</span>
          </label>
        </div>

        <!-- Multiple choice (checkboxes) -->
        <div v-else-if="q.type === 'multiple'" class="question-options">
          <label v-for="opt in q.options" :key="opt" class="option-row">
            <input type="checkbox" :value="opt"
              :checked="answers[q.id]?.includes(opt)"
              @change="toggleMulti(q.id, opt)" class="option-checkbox" />
            <span class="option-text">{{ opt }}</span>
          </label>
        </div>

        <!-- Text input -->
        <div v-else-if="q.type === 'text'" class="question-options">
          <input v-model="textAnswers[q.id]" type="text" class="survey-text-input"
            :placeholder="'请输入...'" @input="setAnswer(q.id, textAnswers[q.id])" />
        </div>
      </div>
    </div>

    <div class="survey-actions">
      <span v-if="errorMsg" class="survey-error">{{ errorMsg }}</span>
      <button class="survey-submit-btn" @click="submitSurvey" :disabled="submitting">
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
  background: var(--oaa-bg-elevated, #334155);
  border: 1px solid var(--oaa-border-default, rgba(255,255,255,0.10));
  border-radius: 10px;
  padding: 20px;
  margin: 12px 0;
  box-shadow: var(--oaa-shadow-card, 0 2px 8px rgba(0,0,0,0.3));
}

.survey-header { margin-bottom: 16px; }
.survey-title { margin: 0 0 6px; font-size: 16px; font-weight: 600; color: var(--oaa-color-primary, #f8fafc); }
.survey-desc { margin: 0; font-size: 13px; color: var(--oaa-color-muted, #94a3b8); }

.survey-question {
  margin-bottom: 18px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--oaa-border-subtle, rgba(255,255,255,0.06));
}
.survey-question:last-child { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }

.question-label {
  display: block;
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 10px;
  color: var(--oaa-color-primary, #f8fafc);
}

.question-options {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* Option row — clearly clickable */
.option-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  background: var(--oaa-bg-surface, #1e293b);
  border: 1px solid var(--oaa-border-subtle, rgba(255,255,255,0.06));
  transition: all 0.15s ease;
  user-select: none;
}
.option-row:hover {
  background: var(--oaa-bg-surface-hover, #334155);
  border-color: var(--oaa-border-strong, rgba(255,255,255,0.15));
}
.option-row:has(input:checked) {
  background: var(--oaa-primary-light, rgba(59,130,246,0.12));
  border-color: var(--oaa-primary, #3b82f6);
}

/* Radio buttons & checkboxes — clearly visible */
.option-radio,
.option-checkbox {
  width: 18px;
  height: 18px;
  accent-color: var(--oaa-primary, #3b82f6);
  cursor: pointer;
  flex-shrink: 0;
}

.option-text {
  color: var(--oaa-color-primary, #f8fafc);
  line-height: 1.4;
}

/* Text input */
.survey-text-input {
  width: 100%;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid var(--oaa-border-default, rgba(255,255,255,0.10));
  background: var(--oaa-bg-input, #172033);
  color: var(--oaa-color-primary, #f8fafc);
  font-size: 14px;
  font-family: inherit;
  outline: none;
  transition: border-color 0.15s ease;
  box-sizing: border-box;
}
.survey-text-input::placeholder { color: var(--oaa-color-disabled, #475569); }
.survey-text-input:focus {
  border-color: var(--oaa-primary, #3b82f6);
  box-shadow: 0 0 0 2px var(--oaa-primary-light, rgba(59,130,246,0.15));
}

/* Actions */
.survey-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--oaa-border-subtle, rgba(255,255,255,0.06));
}

.survey-submit-btn {
  padding: 10px 24px;
  border-radius: 8px;
  border: none;
  background: var(--oaa-primary, #3b82f6);
  color: #fff;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}
.survey-submit-btn:hover { background: var(--oaa-primary-hover, #2563eb); }
.survey-submit-btn:active { background: var(--oaa-primary-active, #1d4ed8); }
.survey-submit-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.survey-error {
  color: var(--oaa-error, #ef4444);
  font-size: 13px;
  font-weight: 500;
}
</style>