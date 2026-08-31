const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

let mainWindow;
let backendProcess;
const BACKEND_PORT = 8765;

function startBackend() {
  const python = process.platform === 'win32' ? 'python' : 'python3';
  const serverPath = path.join(__dirname, 'backend', 'server.py');
  backendProcess = spawn(python, [serverPath, String(BACKEND_PORT)], {
    cwd: path.join(__dirname, '..'), stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
  });
  backendProcess.stdout.on('data', (d) => console.log(`[backend] ${d}`));
  backendProcess.stderr.on('data', (d) => console.log(`[backend] ${d}`));
  backendProcess.on('error', (e) => console.error('Backend error:', e));
  backendProcess.on('exit', (code) => console.log(`Backend exited: ${code}`));
}

function waitForBackend(retries = 30) {
  return new Promise((resolve, reject) => {
    const check = () => {
      const req = http.get(`http://127.0.0.1:${BACKEND_PORT}/api/projects`, (res) => {
        if (res.statusCode === 200) resolve();
        else if (retries > 0) setTimeout(check, 500);
        else reject(new Error('Backend not ready'));
      });
      req.on('error', () => {
        if (retries > 0) setTimeout(check, 500);
        else reject(new Error('Backend not ready'));
      });
      req.end();
    };
    check();
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400, height: 900, minWidth: 900, minHeight: 600,
    frame: true, backgroundColor: '#0a0a0f',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false },
  });
  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  mainWindow.on('closed', () => { mainWindow = null; });
}

app.whenReady().then(async () => {
  startBackend();
  try { await waitForBackend(); console.log('Backend ready'); }
  catch (e) { console.error('Backend failed to start:', e); }
  createWindow();
});

app.on('window-all-closed', () => {
  if (backendProcess) backendProcess.kill();
  if (process.platform !== 'darwin') app.quit();
});

ipcMain.handle('minimize', () => mainWindow?.minimize());
ipcMain.handle('maximize', () => { if (mainWindow?.isMaximized()) mainWindow.unmaximize(); else mainWindow?.maximize(); });
ipcMain.handle('close', () => mainWindow?.close());
ipcMain.handle('select-file', async () => {
  const result = await dialog.showOpenDialog({ properties: ['openFile', 'multiSelections'] });
  return result.canceled ? null : result.filePaths;
});