import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('oaa', {
  wsUrl: 'ws://127.0.0.1:9765',
  platform: process.platform,
  dialog: {
    openDirectory: () => ipcRenderer.invoke('dialog:openDirectory'),
    saveFile: (name: string) => ipcRenderer.invoke('dialog:saveFile', name),
  },
  fs: {
    readDir: (dir: string) => ipcRenderer.invoke('fs:readDir', dir),
  },
  config: {
    save: (data: string) => ipcRenderer.invoke('config:save', data),
    load: () => ipcRenderer.invoke('config:load'),
  },
})
