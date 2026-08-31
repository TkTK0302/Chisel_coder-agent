let state = { projects: [], currentProject: null, conversations: [], currentConv: null, messages: [], loading: false };

// ===== API Bridge =====
async function api(name, ...args) {
  try {
    if (typeof eel !== 'undefined') {
      const result = await eel[name](...args)();
      return typeof result === 'string' ? JSON.parse(result) : result;
    }
    if (typeof window.api !== 'undefined' && window.api[name]) {
      return await window.api[name](...args);
    }
    // Fallback: direct HTTP (FastAPI mode)
    const apiMap = {
      'list_projects': ['GET', '/api/projects'],
      'create_project': ['POST', '/api/projects', (n) => ({name: n})],
      'delete_project': ['DELETE', '/api/projects/{0}'],
      'list_conversations': ['GET', '/api/projects/{0}/conversations'],
      'create_conversation': ['POST', '/api/projects/{0}/conversations', (p, t) => ({title: t})],
      'delete_conversation': ['DELETE', '/api/conversations/{0}'],
      'list_messages': ['GET', '/api/conversations/{0}/messages'],
      'send_message': ['POST', '/api/conversations/{0}/messages', (c, msg) => ({content: msg})],
      'list_files': ['GET', '/api/projects/{0}/files'],
      'upload_file': ['POST', '/api/projects/{0}/files'],
      'delete_file': ['DELETE', '/api/projects/files/{0}'],
      'update_workspace': ['POST', '/api/projects/{0}/workspace', (id, ws) => ({workspace: ws})],
      'list_workspace': ['GET', '/api/projects/{0}/workspace'],
      'read_workspace_file': ['GET', '/api/projects/{0}/workspace/{1}'],
    };
    const [method, path, bodyFn] = apiMap[name] || [];
    if (!path) throw new Error('Unknown API: ' + name);
    const url = 'http://127.0.0.1:8765' + path.replace('{0}', args[0]);
    const opts = { method, headers: {} };
    if (bodyFn) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(bodyFn(...args)); }
    const resp = await fetch(url, opts);
    return await resp.json();
  } catch (e) {
    toast('API Error: ' + e.message);
    throw e;
  }
}

// ===== Init =====
async function init() {
  try {
    await loadProjects();
    if (state.projects.length > 0) selectProject(state.projects[0].id);
  } catch (e) {
    // Retry after a delay (Eel might not be ready yet)
    setTimeout(async () => {
      try {
        await loadProjects();
        if (state.projects.length > 0) selectProject(state.projects[0].id);
      } catch (e2) {
        toast('Failed to connect to backend. Make sure the server is running.');
      }
    }, 1000);
  }
}

// ===== Projects =====
async function loadProjects() {
  state.projects = await api('list_projects');
  renderProjects();
}

function renderProjects() {
  const el = document.getElementById('projectList');
  const search = (document.getElementById('projectSearch').value || '').toLowerCase();
  const filtered = state.projects.filter(p => p.name.toLowerCase().includes(search));
  el.innerHTML = filtered.map(p => `
    <div class="project-item ${state.currentProject?.id === p.id ? 'active' : ''}" onclick="selectProject('${p.id}')">
      <div class="name">${esc(p.name)}</div>
      <div class="meta">${new Date(p.created_at).toLocaleDateString()}</div>
      <button class="del-btn" onclick="event.stopPropagation(); deleteProject('${p.id}')">✕</button>
    </div>
  `).join('');
}

async function selectProject(id) {
  state.currentProject = state.projects.find(p => p.id === id);
  if (!state.currentProject) return;
  state.currentConv = null; state.messages = [];
  document.getElementById('emptyState').classList.add('hidden');
  document.getElementById('chatView').classList.remove('hidden');
  document.getElementById('chatProjectName').textContent = state.currentProject.name + ' · ' + state.currentProject.workspace;
  renderProjects();
  await loadConversations();
  if (state.conversations.length > 0) selectConversation(state.conversations[0].id);
  else await newConversation();
}

async function changeWorkspace() {
  if (!state.currentProject) { toast('No project selected'); return; }
  toast('Opening folder picker...');
  try {
    const folder = await eel.select_folder()();
    if (!folder) { toast('No folder selected'); return; }
    await api('update_workspace', state.currentProject.id, folder);
    await loadProjects();
    state.currentProject = state.projects.find(p => p.id === state.currentProject.id);
    if (state.currentProject) {
      document.getElementById('chatProjectName').textContent = state.currentProject.name + ' · ' + state.currentProject.workspace;
    }
    toast('Workspace updated to: ' + folder);
  } catch (e) { toast('Error: ' + e.message); }
}

async function createProject() {
  showModal('New Project', 'Project name...', async (name) => {
    if (!name || !name.trim()) { toast('Please enter a project name'); return; }
    try {
      const folder = document.getElementById('modalFolder').value;
      await api('create_project', name.trim(), folder);
      await loadProjects();
      if (state.projects.length > 0) selectProject(state.projects[0].id);
      toast('Project created: ' + name.trim());
    } catch (e) {
      toast('Failed to create project');
    }
  });
  document.getElementById('modalFolder').value = '';
}

async function pickFolder() {
  try {
    const folder = await eel.select_folder()();
    if (folder) {
      document.getElementById('modalFolder').value = folder;
    }
  } catch (e) {
    toast('Failed to pick folder');
  }
}

async function deleteProject(id) {
  if (!confirm('Delete this project and all its data?')) return;
  try {
    await api('delete_project', id);
    state.currentProject = null; state.currentConv = null; state.messages = [];
    document.getElementById('chatView').classList.add('hidden');
    document.getElementById('emptyState').classList.remove('hidden');
    await loadProjects();
    toast('Project deleted');
  } catch (e) { toast('Failed to delete project'); }
}

// ===== Conversations =====
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
  if (!state.currentConv) return;
  document.getElementById('chatTitle').textContent = state.currentConv.title;
  state.messages = await api('list_messages', id);
  renderMessages();
  renderConversations();
}

async function newConversation() {
  if (!state.currentProject) return;
  try {
    const c = await api('create_conversation', state.currentProject.id, 'New Chat');
    await loadConversations();
    selectConversation(c.id);
  } catch (e) { toast('Failed to create conversation'); }
}

// ===== Messages =====
function renderMessages() {
  const el = document.getElementById('chatMessages');
  el.innerHTML = state.messages.map(m => `
    <div class="message ${m.role}">
      <div class="avatar">${m.role === 'user' ? 'You' : '◇'}</div>
      <div class="bubble">${formatContent(m.content)}</div>
    </div>
  `).join('');
  el.scrollTop = el.scrollHeight;
}

function formatContent(text) {
  // First escape HTML to prevent XSS
  const d = document.createElement('div');
  d.textContent = text;
  let html = d.innerHTML;

  // Bold: **text** → <strong>text</strong> (must be before escaping other chars)
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

  // Headers: ## Title → <h3>Title</h3>, ### Title → <h4>Title</h4>
  html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>');
  html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^# (.+)$/gm, '<h2>$1</h2>');

  // Inline code: `code` → <code>code</code>
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Code blocks: ```lang\ncode``` → <pre><code>code</code></pre>
  html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre><code>$2</code></pre>');

  // Horizontal rules: --- on its own line → <hr>
  html = html.replace(/^-{3,}$/gm, '<hr>');

  // Tables: simple markdown table to HTML
  html = html.replace(/^\|(.+)\|$/gm, function(match) {
    const cells = match.split('|').filter(c => c.trim()).map(c => c.trim());
    if (cells.every(c => /^[-]+$/.test(c))) return '<hr>'; // separator row
    const tag = match.includes('---') ? 'th' : 'td';
    return '<tr>' + cells.map(c => `<${tag}>${c}</${tag}>`).join('') + '</tr>';
  });
  html = html.replace(/<tr>.*?<\/tr>/g, function(match) {
    if (!match.includes('<th>')) return match;
    return '<thead>' + match + '</thead>';
  });
  html = html.replace(/(<thead>.*?<\/thead>)/g, '<table>$1</table>');
  html = html.replace(/<\/table>\s*<tr>/g, '</table><table><tr>');
  html = html.replace(/<tr>.*?<\/tr>(?!\s*<tr>)/g, function(match) {
    if (!match.includes('<table>')) return '<table>' + match + '</table>';
    return match;
  });

  // Newlines → <br>
  html = html.replace(/\n/g, '<br>');

  return html;
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
    const result = await api('send_message', state.currentConv.id, text);
    // Add placeholder message (will be updated by streaming)
    if (result && result.id) {
      state.messages.push({ id: result.id, role: 'assistant', content: '⏳ Thinking...' });
      renderMessages();
    }
  } catch (e) { toast('Error: ' + e.message); }
  state.loading = false; document.getElementById('sendBtn').disabled = false;
}

function handleKeydown(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } }
function autoResize(el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 200) + 'px'; }

// ===== Files =====
async function showFiles() {
  const panel = document.getElementById('filesPanel');
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden')) await refreshFiles();
}
function toggleFiles() { document.getElementById('filesPanel').classList.add('hidden'); }

async function refreshFiles() {
  if (!state.currentProject) return;
  try {
    const files = await api('list_files', state.currentProject.id);
    document.getElementById('filesList').innerHTML = files.map(f => `
      <div class="file-item"><span class="name">${esc(f.filename)}</span><span class="size">${(f.size/1024).toFixed(1)} KB</span></div>
    `).join('');
  } catch (e) { toast('Failed to load files'); }
}

async function uploadFile() {
  if (!state.currentProject) return;
  try {
    if (typeof window.electron !== 'undefined' && window.electron.selectFile) {
      const paths = await window.electron.selectFile();
      if (!paths) return;
      for (const p of paths) {
        const name = p.split(/[/\\]/).pop();
        const resp = await fetch('file://' + p);
        const blob = await resp.blob();
        const reader = new FileReader();
        reader.readAsDataURL(blob);
        await new Promise(resolve => { reader.onload = async () => {
          const b64 = reader.result.split(',')[1];
          await api('upload_file', state.currentProject.id, name, b64);
          resolve();
        };});
      }
    } else {
      document.getElementById('fileInput').click();
    }
    await refreshFiles();
    toast('Files uploaded');
  } catch (e) { toast('Failed to upload files: ' + e.message); }
}

async function uploadFilesToProject(event) {
  if (!state.currentProject) return;
  try {
    for (const file of event.target.files) {
      const reader = new FileReader();
      const b64 = await new Promise((resolve, reject) => {
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      await api('upload_file', state.currentProject.id, file.name, b64);
    }
    await refreshFiles();
    toast('Files uploaded');
  } catch (e) { toast('Failed to upload files: ' + e.message); }
}

// ===== Utils =====
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function toast(msg) { const el = document.getElementById('toast'); el.textContent = msg; el.classList.remove('hidden'); setTimeout(() => el.classList.add('hidden'), 2500); }

// ===== Modal =====
let modalCallback = null;
function showModal(title, placeholder, callback) {
  document.getElementById('modalTitle').textContent = title;
  document.getElementById('modalInput').placeholder = placeholder || '';
  document.getElementById('modalInput').value = '';
  document.getElementById('modal').classList.remove('hidden');
  modalCallback = callback;
  setTimeout(() => document.getElementById('modalInput').focus(), 100);
}
function closeModal() { document.getElementById('modal').classList.add('hidden'); modalCallback = null; }
function confirmModal() {
  const val = document.getElementById('modalInput').value;
  closeModal();
  if (modalCallback) modalCallback(val);
}

// ===== Error handling =====
window.onerror = function(msg, url, line) { toast('Error: ' + msg); };

// ===== File Explorer =====
async function toggleFileExplorer() {
  const panel = document.getElementById('rightPanel');
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden') && state.currentProject) {
    await refreshFileExplorer();
  }
}

async function refreshFileExplorer() {
  if (!state.currentProject) return;
  try {
    const items = await api('list_workspace', state.currentProject.id);
    const tree = document.getElementById('fileTree');
    // Build tree from flat list
    let html = '';
    let lastDir = '';
    items.forEach(item => {
      const depth = item.path.split('/').length - 1;
      const indent = '  '.repeat(depth);
      if (item.type === 'dir') {
        html += `<div class="item dir" style="padding-left:${12 + depth*16}px" onclick="toggleDir(this)">${indent}📁 ${item.path.split('/').pop()}</div>`;
      } else {
        const size = item.size > 1024 ? (item.size/1024).toFixed(1)+'KB' : item.size+'B';
        html += `<div class="item file" style="padding-left:${12 + depth*16}px" onclick="openFile('${item.path}')">${indent}📄 ${item.path.split('/').pop()} <span class="size">${size}</span></div>`;
      }
    });
    tree.innerHTML = html || '<div class="item" style="color:var(--text2);padding:12px">Empty workspace</div>';
  } catch(e) { toast('Failed to load workspace'); }
}

function toggleDir(el) {
  // Simple toggle: collapse/expand children
  const items = document.getElementById('fileTree').children;
  const idx = Array.from(items).indexOf(el);
  let i = idx + 1;
  const depth = el.style.paddingLeft ? parseInt(el.style.paddingLeft) / 16 : 1;
  while (i < items.length) {
    const childDepth = items[i].style.paddingLeft ? parseInt(items[i].style.paddingLeft) / 16 : 1;
    if (childDepth <= depth) break;
    items[i].style.display = items[i].style.display === 'none' ? '' : 'none';
    i++;
  }
  el.textContent = el.textContent.startsWith('📁') ? '📂' + el.textContent.slice(1) : '📁' + el.textContent.slice(1);
}

async function openFile(path) {
  if (!state.currentProject) return;
  try {
    const result = await api('read_workspace_file', state.currentProject.id, path);
    if (result.error) { toast(result.error); return; }
    document.getElementById('fileCodeHeader').textContent = '📄 ' + result.path;
    document.getElementById('fileCodeContent').innerHTML = '<code>' + esc(result.content) + '</code>';
  } catch(e) { toast('Failed to open file'); }
}

// Existing functions...
if (typeof eel !== 'undefined') {
  eel.expose(update_message, 'update_message');
  eel.expose(message_done, 'message_done');
}

function update_message(msgId, content) {
  // Update a message in the chat
  const idx = state.messages.findIndex(m => m.id === msgId);
  if (idx >= 0) {
    const cleaned = typeof content === 'string' ? content : content;
    state.messages[idx].content = cleaned;
    renderMessages();
  }
}

function message_done(msgId) {
  // Mark message as complete, reload to get final version
  setTimeout(async () => {
    try {
      state.messages = await api('list_messages', state.currentConv.id);
      renderMessages();
    } catch(e) {}
  }, 500);
}

// ===== Start =====
document.addEventListener('DOMContentLoaded', init);