import { app, BrowserWindow, Tray, Menu, nativeImage, Notification, ipcMain, dialog } from 'electron'
import path from 'path'
import fs from 'fs'
import { spawn, ChildProcess } from 'child_process'
import { homedir } from 'os'

let mainWindow: BrowserWindow | null = null
let tray: Tray | null = null
let isQuitting = false
let pythonProcess: ChildProcess | null = null

const CONFIG_PATH = path.join(homedir(), 'OAA', 'config.json')

function getAppIcon(size = 32) {
  const iconPath = path.join(__dirname, '../public/icon.ico')
  try {
    if (fs.existsSync(iconPath)) {
      return nativeImage.createFromPath(iconPath).resize({ width: size, height: size })
    }
  } catch {
    // fallback below
  }
  const buf = Buffer.alloc(size * size * 4)
  for (let i = 0; i < size * size; i++) {
    buf[i * 4] = 59; buf[i * 4 + 1] = 130
    buf[i * 4 + 2] = 246; buf[i * 4 + 3] = 255
  }
  return nativeImage.createFromBuffer(buf, { width: size, height: size })
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 750,
    minWidth: 800,
    minHeight: 600,
    icon: getAppIcon(),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
    frame: true,
    title: 'OPC AI Assistant',
  })

  if (process.env.NODE_ENV === 'development') {
    mainWindow.loadURL('http://localhost:5173')
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
  }

  mainWindow.on('close', (event) => {
    if (!isQuitting && tray) {
      event.preventDefault()
      mainWindow?.hide()
    }
  })
}

function createTray() {
  tray = new Tray(getAppIcon(16))
  tray.setToolTip('OPC AI 助手')

  const contextMenu = Menu.buildFromTemplate([
    { label: '打开 OAA', click: () => {
      mainWindow?.show()
      mainWindow?.focus()
    }},
    { type: 'separator' },
    { label: '模型状态', click: () => {
      new Notification({ title: 'OAA', body: '模型已就绪' }).show()
    }},
    { type: 'separator' },
    {
      label: '开机自启',
      type: 'checkbox',
      checked: app.getLoginItemSettings().openAtLogin,
      click: (menuItem) => {
        app.setLoginItemSettings({ openAtLogin: menuItem.checked })
      },
    },
    { type: 'separator' },
    { label: '退出', click: () => {
      isQuitting = true
      tray?.destroy()
      app.quit()
    }},
  ])
  tray.setContextMenu(contextMenu)
  tray.on('double-click', () => mainWindow?.show())
}

function startPythonBackend() {
  try {
    pythonProcess = spawn('python', ['-m', 'oaa', '--config', CONFIG_PATH], {
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    })

    pythonProcess.stdout?.on('data', (data: Buffer) => {
      console.log(`[python] ${data.toString().trim()}`)
    })

    pythonProcess.stderr?.on('data', (data: Buffer) => {
      console.error(`[python:err] ${data.toString().trim()}`)
    })

    pythonProcess.on('error', (err) => {
      console.error('Failed to start Python backend:', err.message)
      pythonProcess = null
    })

    pythonProcess.on('exit', (code) => {
      console.log(`Python backend exited with code ${code}`)
      pythonProcess = null
    })
  } catch (err) {
    console.error('Failed to spawn Python backend:', err)
  }
}

function stopPythonBackend() {
  if (pythonProcess) {
    try {
      if (process.platform === 'win32') {
        spawn('taskkill', ['/pid', String(pythonProcess.pid), '/f', '/t'])
      } else {
        pythonProcess.kill('SIGTERM')
      }
    } catch {
      pythonProcess.kill()
    }
    pythonProcess = null
  }
}

// IPC handlers
ipcMain.handle('dialog:openDirectory', async () => {
  const result = await dialog.showOpenDialog({
    properties: ['openDirectory'],
  })
  return result.canceled ? null : result.filePaths[0]
})

ipcMain.handle('dialog:saveFile', async (_event, defaultName: string) => {
  const result = await dialog.showSaveDialog({
    defaultPath: defaultName,
  })
  return result.canceled ? null : result.filePath
})

ipcMain.handle('fs:readDir', async (_event, dirPath: string) => {
  try {
    const entries = fs.readdirSync(dirPath, { withFileTypes: true })
    return entries.map(e => ({
      name: e.name,
      isDir: e.isDirectory(),
      size: e.isFile() ? fs.statSync(path.join(dirPath, e.name)).size : 0,
      modified: fs.statSync(path.join(dirPath, e.name)).mtime.toISOString(),
    }))
  } catch {
    return null
  }
})

ipcMain.handle('config:save', async (_event, data: string) => {
  try {
    fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true })
    fs.writeFileSync(CONFIG_PATH, data, 'utf-8')
    return true
  } catch {
    return false
  }
})

ipcMain.handle('config:load', async () => {
  try {
    if (!fs.existsSync(CONFIG_PATH)) return null
    return fs.readFileSync(CONFIG_PATH, 'utf-8')
  } catch {
    return null
  }
})

app.whenReady().then(() => {
  createWindow()
  createTray()
  startPythonBackend()
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    stopPythonBackend()
    app.quit()
  }
})

app.on('before-quit', () => {
  isQuitting = true
  stopPythonBackend()
})
