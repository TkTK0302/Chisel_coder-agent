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
      'rename_conversation': ['POST', '/api/conversations/{0}/rename', (id, title) => ({title: title})],
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
    div.onclick = () => selectConversation(c.id);
    div.innerHTML = `<span class="conv-title">${esc(c.title)}</span><button class="conv-del-btn" onclick="event.stopPropagation(); deleteConversation('${c.id}')">✕</button>`;
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

async function deleteConversation(convId) {
  if (!confirm('Delete this chat and all its messages?')) return;
  try {
    await api('delete_conversation', convId);
    if (state.currentConv && state.currentConv.id === convId) {
      state.currentConv = null;
      state.messages = [];
      document.getElementById('chatMessages').innerHTML = '';
      document.getElementById('chatTitle').textContent = 'Chat';
    }
    await loadConversations();
    if (state.conversations.length === 0) {
      await newConversation();
    } else if (!state.currentConv) {
      selectConversation(state.conversations[0].id);
    }
    toast('Chat deleted');
  } catch (e) { toast('Failed to delete chat', 'error'); }
}

async function newConversation() {
  if (!state.currentProject) return;
  try {
    // 默认名为 "Chat N"，N = 当前 project 下已有 chat 数 + 1
    const existing = await api('list_conversations', state.currentProject.id);
    const title = 'Chat ' + (existing.length + 1);
    const c = await api('create_conversation', state.currentProject.id, title);
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
      <div class="file-item"><span class="name">${esc(f.filename)}</span><span class="size">${(f.size/1024).toFixed(1)} KB</span><button class="file-del-btn" onclick="deleteFile(${f.id}, '${esc(f.filename)}')">✕</button></div>
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

async function deleteFile(fileId, filename) {
  if (!confirm('Delete ' + filename + '?')) return;
  try {
    await api('delete_file', fileId);
    await refreshFiles();
    toast('File deleted');
  } catch(e) { toast('Failed to delete file'); }
}

// ===== Utils =====
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function toast(msg, type) { const el = document.getElementById('toast'); el.textContent = msg; el.className = 'toast' + (type ? ' ' + type : ''); el.classList.remove('hidden'); setTimeout(() => el.classList.add('hidden'), 2500); }

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
    // Build tree from flat list — items are in depth-first order from os.walk
    let html = '';
    items.forEach(item => {
      const depth = item.path.split('/').length - 1;
      if (item.type === 'dir') {
        html += `<div class="item dir collapsed" style="padding-left:${12 + depth*16}px" onclick="toggleDir(this)" data-depth="${depth}">${item.path.split('/').pop()}</div>`;
      } else {
        html += `<div class="item file tree-hidden" style="padding-left:${12 + depth*16}px" onclick="openFile('${item.path}')" data-depth="${depth}">📄 ${item.path.split('/').pop()} <span class="size">${item.size > 1024 ? (item.size/1024).toFixed(1)+'KB' : item.size+'B'}</span></div>`;
      }
    });
    tree.innerHTML = html || '<div class="item" style="color:var(--text2);padding:12px">Empty workspace</div>';
    toast('Workspace refreshed');
  } catch(e) { toast('Failed to load workspace', 'error'); }
}

function toggleDir(el) {
  const tree = document.getElementById('fileTree');
  const items = tree.children;
  const idx = Array.from(items).indexOf(el);
  const dirDepth = parseInt(el.dataset.depth) || 0;
  const isCollapsed = el.classList.contains('collapsed');

  if (isCollapsed) {
    // 展开：显示直接子节点（depth + 1）
    el.classList.remove('collapsed');
    for (let i = idx + 1; i < items.length; i++) {
      const childDepth = parseInt(items[i].dataset.depth) || 0;
      if (childDepth <= dirDepth) break;           // 回到同级或上级，停止
      if (childDepth === dirDepth + 1) {
        items[i].classList.remove('tree-hidden');   // 直接子节点显示
      }
    }
  } else {
    // 折叠：隐藏所有后代节点
    el.classList.add('collapsed');
    for (let i = idx + 1; i < items.length; i++) {
      const childDepth = parseInt(items[i].dataset.depth) || 0;
      if (childDepth <= dirDepth) break;
      items[i].classList.add('tree-hidden');
      // 子文件夹也标记为 collapsed
      if (items[i].classList.contains('dir')) {
        items[i].classList.add('collapsed');
      }
    }
  }
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

// ===== Chat Renaming =====
async function renameChat() {
  if (!state.currentConv) return;
  const newName = prompt('Rename chat:', state.currentConv.title);
  if (!newName || !newName.trim()) return;
  try {
    await api('rename_conversation', state.currentConv.id, newName.trim());
    state.currentConv.title = newName.trim();
    document.getElementById('chatTitle').textContent = newName.trim();
    await loadConversations();
    toast('Chat renamed');
  } catch(e) { toast('Failed to rename'); }
}

async function autoRenameChat(convId, message) {
  const name = message.length > 30 ? message.substring(0, 30) + '...' : message;
  try {
    await api('rename_conversation', convId, name);
    if (state.currentConv && state.currentConv.id === convId) {
      state.currentConv.title = name;
      document.getElementById('chatTitle').textContent = name;
    }
    await loadConversations();
  } catch(e) {}
}

// ===== Task History =====
async function toggleTaskHistory() {
  const panel = document.getElementById('taskPanel');
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden') && state.currentConv) {
    await refreshTaskHistory();
  }
}

async function refreshTaskHistory() {
  if (!state.currentConv) return;
  try {
    const messages = await api('list_messages', state.currentConv.id);
    const el = document.getElementById('taskList');
    const tasks = [];
    let currentRequest = '';
    for (const m of messages) {
      if (m.role === 'user') {
        currentRequest = m.content;
      } else if (m.role === 'assistant' && currentRequest) {
        // Extract structured actions from agent output
        const actions = [];
        let result = 'Success';
        let resultDetail = '';
        const lines = (m.content || '').split('\n');
        let workspacePath = '';
        // Try to find workspace path from the last line
        for (const line of lines) {
          const s = line.trim();
          if (s.startsWith('> 工作目录：')) {
            workspacePath = s.replace('> 工作目录：', '').trim();
          }
        }
        for (const line of lines) {
          const s = line.trim();
          // File operations - convert to natural language
          if (s.startsWith('Edited ')) {
            const file = s.replace('Edited ', '').split(' ')[0];
            const strategy = s.includes('strategy:') ? s.match(/strategy: ([^)]+)/)?.[1] : '';
            let desc = '修改了 ' + file;
            if (strategy) desc += '（' + strategy + '匹配）';
            if (workspacePath) desc += '，位于 ' + workspacePath;
            actions.push(desc);
          } else if (s.startsWith('Written ') || s.startsWith('Created ')) {
            const file = s.replace('Written ', '').replace('Created ', '').split(' ')[0];
            let desc = '创建了 ' + file;
            if (workspacePath) desc += '，位于 ' + workspacePath;
            actions.push(desc);
          } else if (s.startsWith('Read file:')) {
            const file = s.replace('Read file:', '').trim().split(' ')[0];
            actions.push('读取了 ' + file);
          } else if (s.startsWith('File not found')) {
            const file = s.replace('File not found:', '').trim();
            actions.push('查找文件 ' + file + '（未找到）');
          } else if (s.startsWith('Lint:')) {
            actions.push('语法检查：' + s.substring(5, 80));
          } else if (s.startsWith('run_command') || s.includes('python ') || s.includes('pytest ') || s.includes('node ')) {
            actions.push('执行命令：' + s.substring(0, 60));
          } else if (s.startsWith('⚠️') || s.startsWith('Error:')) {
            result = 'Failed';
            resultDetail = s.substring(0, 80);
          } else if (s.includes('tests passed') || s.includes('OK') || s.includes('全部通过')) {
            resultDetail = '测试全部通过';
          }
        }
        // Summarize the user request
        const summary = summarizeRequest(currentRequest);
        if (actions.length > 0 || currentRequest) {
          tasks.push({
            summary: summary,
            actions: actions.slice(0, 6),
            result: result,
            resultDetail: resultDetail,
            request: currentRequest.substring(0, 60) + (currentRequest.length > 60 ? '...' : '')
          });
        }
        currentRequest = '';
      }
    }
    if (tasks.length === 0) {
      el.innerHTML = '<div class="task-item" style="color:var(--text2);padding:12px">No tasks recorded yet.</div>';
    } else {
      el.innerHTML = tasks.slice(-10).reverse().map(t => `
        <div class="task-item">
          <div class="task-request">📋 ${esc(t.summary)}</div>
          <div class="task-meta" style="color:var(--text2);font-size:11px;margin-top:2px">${esc(t.request)}</div>
          <div class="task-actions" style="margin-top:4px">${t.actions.map(a => '<span style="display:block;font-size:12px;color:var(--text);padding:1px 0">• ' + esc(a) + '</span>').join('')}</div>
          <div class="task-result" style="font-size:12px;margin-top:4px;color:${t.result === 'Success' ? 'var(--success)' : 'var(--danger)'}">${t.result === 'Success' ? '✅ 成功' : '❌ 失败'}${t.resultDetail ? ' - ' + esc(t.resultDetail) : ''}</div>
        </div>
      `).join('');
    }
  } catch(e) { toast('Failed to load tasks'); }
}

function summarizeRequest(text) {
  // Simple heuristic to extract core action
  let s = text.trim();
  // Remove common prefixes
  s = s.replace(/^(请|帮我|我需要你|麻烦你|帮我|给我)\s*/i, '');
  // Truncate to reasonable length
  if (s.length > 50) s = s.substring(0, 50) + '...';
  return s;
}

// ===== Status Bar =====
function updateStatusBar() {
  const turnCount = state.messages.filter(m => m.role === 'user').length;
  document.getElementById('statusTurns').textContent = 'Turns: ' + turnCount;
  const total = state.messages.reduce((sum, m) => sum + (m.content || '').length, 0);
  document.getElementById('statusContext').textContent = 'Context: ~' + Math.round(total / 1000) + 'K / 60K';
}

// ===== Question Polling (for Eel interactive buttons) =====
let questionPollInterval = null;
let lastQuestionId = null;

function startQuestionPolling() {
  stopQuestionPolling();
  questionPollInterval = setInterval(async () => {
    if (!state.currentProject) return;
    try {
      const q = await api('check_pending_question', state.currentProject.workspace);
      if (q && q.question && q.id !== lastQuestionId) {
        lastQuestionId = q.id;
        stopQuestionPolling();
        showQuestionModal(q.question, q.options || ['是', '否']);
      }
    } catch(e) {}
  }, 1000);
}

function stopQuestionPolling() {
  if (questionPollInterval) {
    clearInterval(questionPollInterval);
    questionPollInterval = null;
  }
}

function showQuestionModal(question, options) {
  const modal = document.getElementById('questionModal');
  document.getElementById('questionText').textContent = question;
  const btns = document.getElementById('questionButtons');
  btns.innerHTML = options.map(opt =>
    `<button class="btn btn-primary btn-sm" onclick="answerQuestion('${esc(opt)}')">${esc(opt)}</button>`
  ).join(' ');
  modal.classList.remove('hidden');
}

async function answerQuestion(answer) {
  document.getElementById('questionModal').classList.add('hidden');
  if (state.currentProject) {
    await api('submit_answer', state.currentProject.workspace, answer);
  }
  // 等待 2 秒让 agent 处理完再恢复轮询
  setTimeout(() => startQuestionPolling(), 2000);
}

// Override sendMessage to start polling
const _origSend2 = sendMessage;
sendMessage = async function() {
  const input = document.getElementById('messageInput');
  const text = input.value.trim();
  if (!text || state.loading || !state.currentConv) return;
  input.value = ''; autoResize(input);
  state.messages.push({ role: 'user', content: text });
  renderMessages();
  state.loading = true; document.getElementById('sendBtn').disabled = true;
  try {
    const result = await api('send_message', state.currentConv.id, text);
    if (result && result.id) {
      state.messages.push({ id: result.id, role: 'assistant', content: '⏳ Thinking...' });
      renderMessages();
      startQuestionPolling();
    }
    const msgs = await api('list_messages', state.currentConv.id);
    const userMsgs = msgs.filter(m => m.role === 'user');
    const isDefaultName = state.currentConv && (
      state.currentConv.title === 'New Chat' || /^Chat \d+$/.test(state.currentConv.title)
    );
    if (userMsgs.length === 1 && isDefaultName) {
      await autoRenameChat(state.currentConv.id, text);
    }
    updateStatusBar();
  } catch (e) { toast('Error: ' + e.message); }
  state.loading = false; document.getElementById('sendBtn').disabled = false;
};

// ===== Resizable Panels =====
let resizeTarget = null;
let resizeStartX = 0;
let resizeStartWidth = 0;
let resizeStartY = 0;
let resizeStartHeight = 0;

function startResize(target, event) {
  resizeTarget = target;
  resizeStartX = event.clientX;
  resizeStartWidth = document.getElementById('rightPanel').offsetWidth;
  document.getElementById('rightHandle').classList.add('active');
  document.addEventListener('mousemove', doResize);
  document.addEventListener('mouseup', stopResize);
  event.preventDefault();
}

function startResizeH(event) {
  resizeTarget = 'tree';
  resizeStartY = event.clientY;
  resizeStartHeight = document.getElementById('fileTree').offsetHeight;
  document.getElementById('rpTreeHandle').classList.add('active');
  document.addEventListener('mousemove', doResize);
  document.addEventListener('mouseup', stopResize);
  event.preventDefault();
}

function doResize(event) {
  if (resizeTarget === 'right') {
    const diff = resizeStartX - event.clientX;
    const newWidth = Math.max(200, Math.min(600, resizeStartWidth + diff));
    document.getElementById('rightPanel').style.width = newWidth + 'px';
  } else if (resizeTarget === 'tree') {
    const diff = event.clientY - resizeStartY;
    const tree = document.getElementById('fileTree');
    const code = document.getElementById('fileCode');
    const total = tree.parentElement.offsetHeight - document.getElementById('rpTreeHandle').offsetHeight;
    const newTree = Math.max(80, Math.min(total - 80, resizeStartHeight + diff));
    tree.style.height = newTree + 'px';
    tree.style.flex = 'none';
    code.style.flex = '1';
  }
}

function stopResize() {
  resizeTarget = null;
  document.getElementById('rightHandle').classList.remove('active');
  document.getElementById('rpTreeHandle').classList.remove('active');
  document.removeEventListener('mousemove', doResize);
  document.removeEventListener('mouseup', stopResize);
}

// Update status bar after renderMessages
const _origRender = renderMessages;
renderMessages = function() {
  _origRender();
  updateStatusBar();
};

// ===== Start =====
document.addEventListener('DOMContentLoaded', init);