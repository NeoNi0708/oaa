"use strict";
const electron = require("electron");
electron.contextBridge.exposeInMainWorld("oaa", {
  wsUrl: "ws://127.0.0.1:9765",
  platform: process.platform,
  dialog: {
    openDirectory: () => electron.ipcRenderer.invoke("dialog:openDirectory"),
    saveFile: (name) => electron.ipcRenderer.invoke("dialog:saveFile", name)
  },
  fs: {
    readDir: (dir) => electron.ipcRenderer.invoke("fs:readDir", dir)
  },
  config: {
    save: (data) => electron.ipcRenderer.invoke("config:save", data),
    load: () => electron.ipcRenderer.invoke("config:load")
  }
});
