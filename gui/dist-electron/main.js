"use strict";
const electron = require("electron");
const path = require("path");
const fs = require("fs");
const child_process = require("child_process");
const os = require("os");
let mainWindow = null;
let tray = null;
let isQuitting = false;
let pythonProcess = null;
const CONFIG_PATH = path.join(os.homedir(), "OAA", "config.json");
function getAppIcon(size = 32) {
  const iconPath = path.join(__dirname, "../public/icon.ico");
  try {
    if (fs.existsSync(iconPath)) {
      return electron.nativeImage.createFromPath(iconPath).resize({ width: size, height: size });
    }
  } catch {
  }
  const buf = Buffer.alloc(size * size * 4);
  for (let i = 0; i < size * size; i++) {
    buf[i * 4] = 59;
    buf[i * 4 + 1] = 130;
    buf[i * 4 + 2] = 246;
    buf[i * 4 + 3] = 255;
  }
  return electron.nativeImage.createFromBuffer(buf, { width: size, height: size });
}
function createWindow() {
  mainWindow = new electron.BrowserWindow({
    width: 1100,
    height: 750,
    minWidth: 800,
    minHeight: 600,
    icon: getAppIcon(),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      nodeIntegration: false,
      contextIsolation: true
    },
    frame: true,
    title: "OPC AI Assistant"
  });
  if (process.env.NODE_ENV === "development") {
    mainWindow.loadURL("http://localhost:5173");
  } else {
    mainWindow.loadFile(path.join(__dirname, "../dist/index.html"));
  }
  mainWindow.on("close", (event) => {
    if (!isQuitting && tray) {
      event.preventDefault();
      mainWindow == null ? void 0 : mainWindow.hide();
    }
  });
}
function createTray() {
  tray = new electron.Tray(getAppIcon(16));
  tray.setToolTip("OPC AI 助手");
  const contextMenu = electron.Menu.buildFromTemplate([
    { label: "打开 OAA", click: () => {
      mainWindow == null ? void 0 : mainWindow.show();
      mainWindow == null ? void 0 : mainWindow.focus();
    } },
    { type: "separator" },
    { label: "模型状态", click: () => {
      new electron.Notification({ title: "OAA", body: "模型已就绪" }).show();
    } },
    { type: "separator" },
    {
      label: "开机自启",
      type: "checkbox",
      checked: electron.app.getLoginItemSettings().openAtLogin,
      click: (menuItem) => {
        electron.app.setLoginItemSettings({ openAtLogin: menuItem.checked });
      }
    },
    { type: "separator" },
    { label: "退出", click: () => {
      isQuitting = true;
      tray == null ? void 0 : tray.destroy();
      electron.app.quit();
    } }
  ]);
  tray.setContextMenu(contextMenu);
  tray.on("double-click", () => mainWindow == null ? void 0 : mainWindow.show());
}
function startPythonBackend() {
  var _a, _b;
  try {
    pythonProcess = child_process.spawn("python", ["-m", "oaa", "--config", CONFIG_PATH], {
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true
    });
    (_a = pythonProcess.stdout) == null ? void 0 : _a.on("data", (data) => {
      console.log(`[python] ${data.toString().trim()}`);
    });
    (_b = pythonProcess.stderr) == null ? void 0 : _b.on("data", (data) => {
      console.error(`[python:err] ${data.toString().trim()}`);
    });
    pythonProcess.on("error", (err) => {
      console.error("Failed to start Python backend:", err.message);
      pythonProcess = null;
    });
    pythonProcess.on("exit", (code) => {
      console.log(`Python backend exited with code ${code}`);
      pythonProcess = null;
    });
  } catch (err) {
    console.error("Failed to spawn Python backend:", err);
  }
}
function stopPythonBackend() {
  if (pythonProcess) {
    try {
      if (process.platform === "win32") {
        child_process.spawn("taskkill", ["/pid", String(pythonProcess.pid), "/f", "/t"]);
      } else {
        pythonProcess.kill("SIGTERM");
      }
    } catch {
      pythonProcess.kill();
    }
    pythonProcess = null;
  }
}
electron.ipcMain.handle("dialog:openDirectory", async () => {
  const result = await electron.dialog.showOpenDialog({
    properties: ["openDirectory"]
  });
  return result.canceled ? null : result.filePaths[0];
});
electron.ipcMain.handle("dialog:saveFile", async (_event, defaultName) => {
  const result = await electron.dialog.showSaveDialog({
    defaultPath: defaultName
  });
  return result.canceled ? null : result.filePath;
});
electron.ipcMain.handle("fs:readDir", async (_event, dirPath) => {
  try {
    const entries = fs.readdirSync(dirPath, { withFileTypes: true });
    return entries.map((e) => ({
      name: e.name,
      isDir: e.isDirectory(),
      size: e.isFile() ? fs.statSync(path.join(dirPath, e.name)).size : 0,
      modified: fs.statSync(path.join(dirPath, e.name)).mtime.toISOString()
    }));
  } catch {
    return null;
  }
});
electron.ipcMain.handle("config:save", async (_event, data) => {
  try {
    fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
    fs.writeFileSync(CONFIG_PATH, data, "utf-8");
    return true;
  } catch {
    return false;
  }
});
electron.ipcMain.handle("config:load", async () => {
  try {
    if (!fs.existsSync(CONFIG_PATH)) return null;
    return fs.readFileSync(CONFIG_PATH, "utf-8");
  } catch {
    return null;
  }
});
electron.app.whenReady().then(() => {
  createWindow();
  createTray();
  startPythonBackend();
});
electron.app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    stopPythonBackend();
    electron.app.quit();
  }
});
electron.app.on("before-quit", () => {
  isQuitting = true;
  stopPythonBackend();
});
