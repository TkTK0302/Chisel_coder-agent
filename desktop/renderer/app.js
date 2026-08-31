let state = { projects: [], currentProject: null, conversations: [], currentConv: null, messages: [], loading: false };

function api(name, ...args) {
  if (typeof eel !== 'undefined') {
    return eel[name](...args)().then(r => JSON.parse(r));
  }
  return window.api[name](...args);
}

async function init() { await loadProjects(); if (state.projects.length > 0) selectProject(state.projects[0].id); }

async function loadProjects() {
  state.projects = await api('list_projects');
  renderProjects();
}

function renderProjects() {
  const el = document.getElementById('projectList');
  const search = (document.getElementById('projectSearch').value || '').toLowerCase();
  const filtered = state.projects.filter(p => p.name.toLowerCase().includes(search));
  el.innerHTML = filtered.map(p => `<div class="project-item ${state.currentProject?.id === p.id ? 'active' : ''}" onclick="selectProject('${p.id}')"><div class="name">${esc(p.name)}</div><div class="meta">${new Date(p.created_at).toLocaleDateString()}</div><button class="del-btn" onclick="event.stopPropagation(); deleteProject('${p.id}')">✕</button></div>`).join('');
}

async function selectProject(id) {
  state.currentProject = state.projects.find(p => p.id === id);
  state.currentConv = null; state.messages = [];
  document.getElementById('emptyState').classList.add('hidden');
  document.getElementById('chatView').classList.remove('hidden');
  document.getElementById('chatProjectName').textContent = state.currentProject.name;
  renderProjects();
  await loadConversations();
  if (state.conversations.length > 0) selectConversation(state.conversations[0].id);
  else await newConversation();
}

async function createProject() {
  showModal('New Project', 'Project name...', async (name) => {
    if (!name || !name.trim()) return;
    await api('create_project', name.trim());
    await loadProjects();
    if (state.projects[0]) selectProject(state.projects[0].id);
    toast('Project created');
  });
}

async function deleteProject(id) {
  if (!confirm('Delete this project and all its data?')) return;
  await api('delete_project', id);
  state.currentProject = null; state.currentConv = null; state.messages = [];
  document.getElementById('chatView').classList.add('hidden');
  document.getElementById('emptyState').classList.remove('hidden');
  await loadProjects();
  toast('Project deleted');
}

async function loadConversations() {
  if (!state.currentProject) return;
  state.conversations = await api('list_conversations', state.currentProject.id);
  renderConversations();
}

function renderConversations() {
  document.querySelectorAll('.conv-item').forEach(el => el.remove());
  if (!state.currentProject) return;
  const el = document.getElementById('projectList');
  state.conversations.forEach(c => {
    const div = document.createElement('div');
    div.className = `conv-item ${state.currentConv?.id === c.id ? 'active' : ''}`;
    div.textContent = c.title;
    div.onclick = () => selectConversation(c.id);
    el.appendChild(div);
  });
}

async function selectConversation(id) {
  state.currentConv = state.conversations.find(c => c.id === id);
  document.getElementById('chatTitle').textContent = state.currentConv?.title || 'Chat';
  state.messages = await api('list_messages', id);
  renderMessages();
  renderConversations();
}

async function newConversation() {
  if (!state.currentProject) return;
  const c = await api('create_conversation', state.currentProject.id, 'New Chat');
  await loadConversations();
  selectConversation(c.id);
}

function renderMessages() {
  const el = document.getElementById('chatMessages');
  el.innerHTML = state.messages.map(m => `<div class="message ${m.role}"><div class="avatar">${m.role === 'user' ? 'You' : '◇'}</div><div class="bubble">${formatContent(m.content)}</div></div>`).join('');
  el.scrollTop = el.scrollHeight;
}

function formatContent(text) {
  return esc(text).replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>').replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\n/g, '<br>');
}

async function sendMessage() {
  const input = document.getElementById('messageInput');
  const text = input.value.trim();
  if (!text || state.loading || !state.currentConv) return;
  input.value = ''; autoResize(input);
  state.messages.push({ role: 'user', content: text });
  renderMessages();
  state.loading = true; document.getElementById('sendBtn').disabled = true;
  try {
    await api('send_message', state.currentConv.id, text);
    state.messages = await api('list_messages', state.currentConv.id);
    renderMessages();
  } catch (e) { toast('Error: ' + e.message); }
  state.loading = false; document.getElementById('sendBtn').disabled = false;
}

function handleKeydown(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } }
function autoResize(el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 200) + 'px'; }

async function showFiles() {
  const panel = document.getElementById('filesPanel');
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden')) await refreshFiles();
}
function toggleFiles() { document.getElementById('filesPanel').classList.add('hidden'); }

async function refreshFiles() {
  if (!state.currentProject) return;
  const files = await api('list_files', state.currentProject.id);
  document.getElementById('filesList').innerHTML = files.map(f => `<div class="file-item"><span class="name">${esc(f.filename)}</span><span class="size">${(f.size/1024).toFixed(1)} KB</span></div>`).join('');
}

async function uploadFile() {
  if (!state.currentProject) return;
  if (typeof window.electron !== 'undefined') {
    const paths = await window.electron.selectFile();
    if (!paths) return;
    for (const p of paths) {
      const name = p.split(/[/\\]/).pop();
      await api('upload_file', state.currentProject.id, p, name);
    }
  } else {
    document.getElementById('fileInput').click();
  }
  await refreshFiles();
  toast('Files uploaded');
}

async function uploadFilesToProject(event) {
  if (!state.currentProject) return;
  for (const file of event.target.files) {
    await api('upload_file', state.currentProject.id, file.path, file.name);
  }
  await refreshFiles();
  toast('Files uploaded');
}

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function toast(msg) { const el = document.getElementById('toast'); el.textContent = msg; el.classList.remove('hidden'); setTimeout(() => el.classList.add('hidden'), 2500); }

let modalCallback = null;
function showModal(title, placeholder, callback) { document.getElementById('modalTitle').textContent = title; document.getElementById('modalInput').placeholder = placeholder || ''; document.getElementById('modalInput').value = ''; document.getElementById('modal').classList.remove('hidden'); modalCallback = callback; setTimeout(() => document.getElementById('modalInput').focus(), 100); }
function closeModal() { document.getElementById('modal').classList.add('hidden'); modalCallback = null; }
function confirmModal() { const val = document.getElementById('modalInput').value; closeModal(); if (modalCallback) modalCallback(val); }

document.addEventListener('DOMContentLoaded', init);