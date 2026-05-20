<template>
  <div class="app-layout">
    <Sidebar :active-tab="activeTab" @navigate="activeTab = $event" />
    <main class="main-content">
      <KeepAlive>
        <component :is="activeComponent" />
      </KeepAlive>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import Sidebar from './components/Sidebar.vue'
import ChatView from './views/ChatView.vue'
import SkillView from './views/SkillView.vue'
import ConnectionsView from './views/ConnectionsView.vue'
import TaskView from './views/TaskView.vue'
import FileView from './views/FileView.vue'
import SettingsView from './views/SettingsView.vue'

const activeTab = ref('chat')

const tabComponents: Record<string, any> = {
  chat: ChatView,
  skills: SkillView,
  connections: ConnectionsView,
  tasks: TaskView,
  files: FileView,
  settings: SettingsView,
}

const activeComponent = computed(() => tabComponents[activeTab.value])
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
