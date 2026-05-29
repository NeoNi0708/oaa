<template>
  <SetupView v-if="needsSetup" @setup-complete="onSetupComplete" />
  <div v-else class="app-layout">
    <Sidebar :active-tab="activeTab" @navigate="activeTab = $event" />
    <main class="main-content">
      <KeepAlive>
        <component :is="activeComponent" />
      </KeepAlive>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import Sidebar from './components/Sidebar.vue'
import ChatView from './views/ChatView.vue'
import SkillView from './views/SkillView.vue'
import ConnectionsView from './views/ConnectionsView.vue'
import TaskView from './views/TaskView.vue'
import FileView from './views/FileView.vue'
import SettingsView from './views/SettingsView.vue'
import EvolutionView from './views/EvolutionView.vue'
import PatchView from './views/PatchView.vue'
import SetupView from './views/SetupView.vue'
import { useWebSocket } from './composables/useWebSocket'

const { connected, sendRequest } = useWebSocket()

const activeTab = ref('chat')
const needsSetup = ref(false)  // TEMP: set to false for testing

const tabComponents: Record<string, any> = {
  chat: ChatView,
  skills: SkillView,
  connections: ConnectionsView,
  tasks: TaskView,
  files: FileView,
  settings: SettingsView,
  evolution: EvolutionView,
  patches: PatchView,
}

const activeComponent = computed(() => tabComponents[activeTab.value])

function onSetupComplete() {
  needsSetup.value = false
}

onMounted(() => {
  // Watch for WebSocket connection, then check if config is needed
  const stop = watch(connected, async (val) => {
    if (val) {
      try {
        const resp = await sendRequest('get_status', {})
        if (resp.ok && resp.needs_config === false) {
          needsSetup.value = false
        }
      } catch {
        // If request fails, keep needsSetup=true so the user sees the setup page
      }
      stop()
    }
  })
})
</script>

<style scoped>
.app-layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

.main-content {
  flex: 1;
  overflow-y: auto;
  background: var(--oaa-bg-app);
  min-width: 0;
  position: relative;
  z-index: 0;
}
</style>
