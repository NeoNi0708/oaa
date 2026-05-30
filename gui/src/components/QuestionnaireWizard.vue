<template>
  <div class="qnr-container">
    <!-- Header -->
    <div class="qnr-header">
      <h3 class="qnr-title">{{ questionnaire.title }}</h3>
      <p v-if="questionnaire.description" class="qnr-desc">{{ questionnaire.description }}</p>
    </div>

    <!-- Step Indicator -->
    <div class="qnr-steps">
      <div
        v-for="(sec, idx) in visibleSections"
        :key="sec.id"
        class="qnr-step"
        :class="{
          'qnr-step--active': idx === currentStep,
          'qnr-step--done': submittedSections.has(sec.id),
        }"
      >
        <span class="qnr-step-num">{{ idx + 1 }}</span>
        <span class="qnr-step-label">{{ sec.title }}</span>
      </div>
    </div>

    <!-- Current Section Questions -->
    <div class="qnr-section" :class="{ 'qnr-section--disabled': formDisabled }">
      <h4 class="qnr-section-title">{{ currentSection.title }}</h4>
      <p v-if="currentSection.description" class="qnr-section-desc">{{ currentSection.description }}</p>

      <div v-for="q in visibleQuestions" :key="q.id" class="qnr-question">
        <label class="qnr-q-label">{{ q.label }}</label>

        <!-- single -->
        <div v-if="q.type === 'single'" class="qnr-options" :class="{ 'qnr-options--disabled': formDisabled }">
          <label v-for="opt in q.options" :key="opt" class="qnr-option-row">
            <input type="radio" :name="sectionAnswersKey(currentSection.id) + '_' + q.id"
              :value="opt"
              :checked="getAnswer(currentSection.id, q.id) === opt"
              @change="setAnswer(currentSection.id, q.id, opt)"
              :disabled="formDisabled" class="qnr-radio" />
            <span class="qnr-option-text">{{ opt }}</span>
          </label>
        </div>

        <!-- multiple -->
        <div v-else-if="q.type === 'multiple'" class="qnr-options" :class="{ 'qnr-options--disabled': formDisabled }">
          <label v-for="opt in q.options" :key="opt" class="qnr-option-row">
            <input type="checkbox" :value="opt"
              :checked="getAnswer(currentSection.id, q.id)?.includes(opt)"
              @change="toggleMulti(currentSection.id, q.id, opt)"
              :disabled="formDisabled" class="qnr-checkbox" />
            <span class="qnr-option-text">{{ opt }}</span>
          </label>
        </div>

        <!-- text -->
        <input v-else-if="q.type === 'text'" v-model="textInputs[currentSection.id + '_' + q.id]"
          type="text" class="qnr-text-input" :class="{ 'qnr-text-input--disabled': formDisabled }"
          placeholder="请输入..." :disabled="formDisabled"
          @input="setAnswer(currentSection.id, q.id, textInputs[currentSection.id + '_' + q.id])" />
      </div>
    </div>

    <!-- Error message -->
    <div v-if="errorMsg" class="qnr-error">{{ errorMsg }}</div>

    <!-- Completed banner -->
    <div v-if="done" class="qnr-done-banner">问卷已提交</div>

    <!-- Navigation -->
    <div v-if="!done" class="qnr-nav">
      <button v-if="!isFirstStep" class="qnr-nav-btn qnr-nav-btn--prev" @click="prevPage" :disabled="submitting">
        ← 上一步
      </button>
      <span class="qnr-nav-spacer"></span>
      <button v-if="!isLastStep" class="qnr-nav-btn qnr-nav-btn--next" @click="nextPage" :disabled="submitting">
        {{ submitting ? '提交中...' : '下一步 →' }}
      </button>
      <button v-if="isLastStep" class="qnr-nav-btn qnr-nav-btn--submit" @click="submitAll" :disabled="submitting">
        {{ submitting ? '提交中...' : '提交全部' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed } from 'vue'

const props = defineProps<{
  questionnaire: any
  sendRequest: Function
}>()

const emit = defineEmits<{ (e: 'completed'): void }>()

// ── Types ──
interface Condition {
  depends_on?: string
  equals?: any
  in?: any[]
  and?: Condition[]
  or?: Condition[]
}

// ── State ──
const currentStep = ref(0)
const answers = reactive<Record<string, Record<string, any>>>({})
const textInputs = reactive<Record<string, string>>({})
const submitting = ref(false)
const done = ref(false)
const errorMsg = ref('')
const submittedSections = ref<Set<string>>(new Set())

const formDisabled = computed(() => submitting.value || done.value)

// ── Condition Engine ──
function evaluateCondition(cond: Condition | null | undefined, ctxAnswers: Record<string, any>): boolean {
  if (!cond) return true
  if (cond.and) return cond.and.every(c => evaluateCondition(c, ctxAnswers))
  if (cond.or) return cond.or.some(c => evaluateCondition(c, ctxAnswers))
  if (cond.depends_on) {
    const val = ctxAnswers[cond.depends_on]
    if (cond.equals !== undefined) return val === cond.equals
    if (cond.in) return cond.in.includes(val)
  }
  return true
}

// ── Computed ──
function buildContextAnswers(): Record<string, any> {
  const ctx: Record<string, any> = {}
  for (const sec of props.questionnaire.sections) {
    const secAnswers = answers[sec.id]
    if (secAnswers) {
      Object.assign(ctx, secAnswers)
    }
  }
  return ctx
}

const visibleSections = computed(() => {
  const ctx = buildContextAnswers()
  return props.questionnaire.sections.filter((sec: any) =>
    evaluateCondition(sec.condition || null, ctx)
  )
})

const currentSection = computed(() => visibleSections.value[currentStep.value])

const visibleQuestions = computed(() => {
  if (!currentSection.value) return []
  const ctx = buildContextAnswers()
  return (currentSection.value.questions || []).filter((q: any) =>
    evaluateCondition(q.condition || null, ctx)
  )
})

const isFirstStep = computed(() => currentStep.value === 0)
const isLastStep = computed(() => currentStep.value === visibleSections.value.length - 1)

// ── Answer helpers ──
function sectionAnswersKey(sectionId: string): string {
  return sectionId
}

function getAnswer(sectionId: string, qId: string): any {
  return answers[sectionId]?.[qId]
}

function setAnswer(sectionId: string, qId: string, value: any) {
  if (!answers[sectionId]) answers[sectionId] = {}
  answers[sectionId][qId] = value
  errorMsg.value = ''
}

function toggleMulti(sectionId: string, qId: string, opt: string) {
  if (!answers[sectionId]) answers[sectionId] = {}
  if (!answers[sectionId][qId]) answers[sectionId][qId] = []
  const arr = answers[sectionId][qId] as string[]
  const idx = arr.indexOf(opt)
  if (idx >= 0) arr.splice(idx, 1)
  else arr.push(opt)
  errorMsg.value = ''
}

// ── Validation ──
function validateCurrentSection(): boolean {
  if (!currentSection.value) return false
  for (const q of visibleQuestions.value) {
    const val = getAnswer(currentSection.value.id, q.id)
    if (q.type === 'text') {
      const key = currentSection.value.id + '_' + q.id
      if (!textInputs[key] || !textInputs[key].trim()) {
        errorMsg.value = `请填写"${q.label}"`
        return false
      }
      // Sync text to answers
      setAnswer(currentSection.value.id, q.id, textInputs[key].trim())
    } else if (!val || (Array.isArray(val) && val.length === 0)) {
      errorMsg.value = `请选择"${q.label}"`
      return false
    }
  }
  return true
}

// ── Sync text answers before any operation ──
function syncTextAnswers() {
  if (!currentSection.value) return
  for (const q of currentSection.value.questions || []) {
    if (q.type === 'text') {
      const key = currentSection.value.id + '_' + q.id
      if (textInputs[key]?.trim()) {
        setAnswer(currentSection.value.id, q.id, textInputs[key].trim())
      }
    }
  }
}

// ── Navigation ──
async function submitCurrentSection() {
  const sec = currentSection.value
  if (!sec) return
  const secAnswers = answers[sec.id]
  if (!secAnswers) return
  try {
    await props.sendRequest('submit_section', {
      questionnaire_id: props.questionnaire.id,
      section_id: sec.id,
      answers: { ...secAnswers },
    })
    submittedSections.value.add(sec.id)
  } catch {
    // Cache failure is non-fatal — answers still in frontend state
  }
}

async function nextPage() {
  if (!validateCurrentSection()) return
  syncTextAnswers()
  submitting.value = true
  errorMsg.value = ''
  await submitCurrentSection()
  // Advance to next visible section
  if (currentStep.value < visibleSections.value.length - 1) {
    currentStep.value++
  }
  submitting.value = false
}

function prevPage() {
  if (currentStep.value > 0) {
    currentStep.value--
    errorMsg.value = ''
  }
}

async function submitAll() {
  if (!validateCurrentSection()) return
  syncTextAnswers()
  submitting.value = true
  errorMsg.value = ''
  // Save last section
  await submitCurrentSection()
  // Submit full questionnaire
  try {
    const resp = await props.sendRequest('submit_questionnaire', {
      questionnaire_id: props.questionnaire.id,
    })
    if (resp.ok) {
      done.value = true
      emit('completed')
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
.qnr-container {
  background: var(--oaa-bg-elevated, #334155);
  border: 1px solid var(--oaa-border-default, rgba(255,255,255,0.10));
  border-radius: 10px;
  padding: 20px;
  margin: 12px 0;
  box-shadow: var(--oaa-shadow-card, 0 2px 8px rgba(0,0,0,0.3));
}

.qnr-header { margin-bottom: 16px; }
.qnr-title { margin: 0 0 6px; font-size: 16px; font-weight: 600; color: var(--oaa-color-primary, #f8fafc); }
.qnr-desc { margin: 0; font-size: 13px; color: var(--oaa-color-muted, #94a3b8); }

/* Step indicator */
.qnr-steps {
  display: flex;
  gap: 6px;
  margin-bottom: 18px;
  overflow-x: auto;
  padding-bottom: 4px;
}
.qnr-step {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border-radius: 6px;
  font-size: 12px;
  background: var(--oaa-bg-surface, #1e293b);
  border: 1px solid var(--oaa-border-subtle, rgba(255,255,255,0.06));
  color: var(--oaa-color-muted, #94a3b8);
  white-space: nowrap;
  flex-shrink: 0;
}
.qnr-step--active {
  border-color: var(--oaa-primary, #3b82f6);
  color: var(--oaa-color-primary, #f8fafc);
  background: var(--oaa-primary-light, rgba(59,130,246,0.12));
}
.qnr-step--done .qnr-step-num {
  background: var(--oaa-primary, #3b82f6);
  color: #fff;
}
.qnr-step-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 600;
  background: var(--oaa-bg-surface, #1e293b);
  border: 1px solid var(--oaa-border-default, rgba(255,255,255,0.10));
}
.qnr-step-label { font-size: 12px; }

/* Section */
.qnr-section { margin-bottom: 16px; }
.qnr-section-title { margin: 0 0 4px; font-size: 15px; font-weight: 500; color: var(--oaa-color-primary, #f8fafc); }
.qnr-section-desc { margin: 0 0 12px; font-size: 13px; color: var(--oaa-color-muted, #94a3b8); }

/* Question */
.qnr-question { margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid var(--oaa-border-subtle, rgba(255,255,255,0.06)); }
.qnr-question:last-child { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }
.qnr-q-label { display: block; font-size: 14px; font-weight: 500; margin-bottom: 8px; color: var(--oaa-color-primary, #f8fafc); }

.qnr-options { display: flex; flex-direction: column; gap: 4px; }
.qnr-option-row {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 12px; border-radius: 8px; cursor: pointer;
  font-size: 14px; background: var(--oaa-bg-surface, #1e293b);
  border: 1px solid var(--oaa-border-subtle, rgba(255,255,255,0.06));
  transition: all 0.15s ease; user-select: none;
}
.qnr-option-row:hover { background: var(--oaa-bg-surface-hover, #334155); border-color: var(--oaa-border-strong, rgba(255,255,255,0.15)); }
.qnr-option-row:has(input:checked) { background: var(--oaa-primary-light, rgba(59,130,246,0.12)); border-color: var(--oaa-primary, #3b82f6); }
.qnr-radio, .qnr-checkbox { width: 18px; height: 18px; accent-color: var(--oaa-primary, #3b82f6); cursor: pointer; flex-shrink: 0; }
.qnr-option-text { color: var(--oaa-color-primary, #f8fafc); line-height: 1.4; }

.qnr-text-input {
  width: 100%; padding: 10px 12px; border-radius: 8px;
  border: 1px solid var(--oaa-border-default, rgba(255,255,255,0.10));
  background: var(--oaa-bg-input, #172033); color: var(--oaa-color-primary, #f8fafc);
  font-size: 14px; font-family: inherit; outline: none; transition: border-color 0.15s ease; box-sizing: border-box;
}
.qnr-text-input::placeholder { color: var(--oaa-color-disabled, #475569); }
.qnr-text-input:focus { border-color: var(--oaa-primary, #3b82f6); box-shadow: 0 0 0 2px var(--oaa-primary-light, rgba(59,130,246,0.15)); }

/* Error */
.qnr-error { color: var(--oaa-error, #ef4444); font-size: 13px; font-weight: 500; margin-bottom: 10px; }

/* Navigation */
.qnr-nav { display: flex; align-items: center; gap: 12px; margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--oaa-border-subtle, rgba(255,255,255,0.06)); }
.qnr-nav-spacer { flex: 1; }
.qnr-nav-btn {
  padding: 10px 24px; border-radius: 8px; border: none;
  font-size: 14px; font-weight: 500; cursor: pointer; transition: all 0.15s ease;
}
.qnr-nav-btn--prev { background: var(--oaa-bg-surface, #1e293b); color: var(--oaa-color-primary, #f8fafc); border: 1px solid var(--oaa-border-default, rgba(255,255,255,0.10)); }
.qnr-nav-btn--prev:hover { background: var(--oaa-bg-surface-hover, #334155); }
.qnr-nav-btn--next { background: var(--oaa-primary, #3b82f6); color: #fff; }
.qnr-nav-btn--next:hover { background: var(--oaa-primary-hover, #2563eb); }
.qnr-nav-btn--submit { background: var(--oaa-primary, #3b82f6); color: #fff; }
.qnr-nav-btn--submit:hover { background: var(--oaa-primary-hover, #2563eb); }
.qnr-nav-btn:disabled { opacity: 0.6; cursor: not-allowed; }

/* Disabled / done state */
.qnr-section--disabled { opacity: 0.55; pointer-events: none; }
.qnr-options--disabled { opacity: 0.6; }
.qnr-text-input--disabled { opacity: 0.5; }
.qnr-done-banner {
  text-align: center;
  padding: 12px;
  margin-top: 12px;
  border-radius: 8px;
  background: var(--oaa-primary-light, rgba(59,130,246,0.12));
  border: 1px solid var(--oaa-primary, #3b82f6);
  color: var(--oaa-color-primary, #f8fafc);
  font-size: 14px;
  font-weight: 500;
}
</style>
