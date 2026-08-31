const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('electron', {
  minimize: () => ipcRenderer.invoke('minimize'),
  maximize: () => ipcRenderer.invoke('maximize'),
  close: () => ipcRenderer.invoke('close'),
  selectFile: () => ipcRenderer.invoke('select-file'),
});
const API = 'http://127.0.0.1:8765/api';
contextBridge.exposeInMainWorld('api', {
  listProjects: () => fetch(`${API}/projects`).then(r => r.json()),
  createProject: (name) => fetch(`${API}/projects`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name}) }).then(r => r.json()),
  deleteProject: (id) => fetch(`${API}/projects/${id}`, { method:'DELETE' }).then(r => r.json()),
  listConversations: (pid) => fetch(`${API}/projects/${pid}/conversations`).then(r => r.json()),
  createConversation: (pid, title) => fetch(`${API}/projects/${pid}/conversations`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title}) }).then(r => r.json()),
  deleteConversation: (cid) => fetch(`${API}/conversations/${cid}`, { method:'DELETE' }).then(r => r.json()),
  listMessages: (cid) => fetch(`${API}/conversations/${cid}/messages`).then(r => r.json()),
  sendMessage: (cid, content) => fetch(`${API}/conversations/${cid}/messages`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({content}) }).then(r => r.json()),
  listFiles: (pid) => fetch(`${API}/projects/${pid}/files`).then(r => r.json()),
  uploadFile: (pid, file) => { const fd = new FormData(); fd.append('file', file); return fetch(`${API}/projects/${pid}/files`, { method:'POST', body:fd }).then(r => r.json()); },
  deleteFile: (fid) => fetch(`${API}/projects/files/${fid}`, { method:'DELETE' }).then(r => r.json()),
});