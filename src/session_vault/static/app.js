'use strict';
const $ = id => document.getElementById(id);
let token = '', items = [], currentJob = null, connected = false, driveConnected = false;
let language = navigator.language.startsWith('zh') ? 'zh' : 'en';
const zh = {
  eyebrow:'让好想法，有处安放', headline:'留住每一段好对话。',
  intro:'将 ChatGPT 会话存成清晰好读的 PDF。\n保存在本地，也可以带去 Google Drive。',
  connectTitle:'连接你的 ChatGPT', cookieHelp:'从已登录 ChatGPT 的浏览器中，复制请求头里的 Cookie。',
  proxy:'代理地址（可选）',cookieLabel:'ChatGPT Cookie',advanced:'高级选项',workspace:'工作空间 ID（可选）',connect:'连接 ChatGPT →',
  connected:'ChatGPT 已连接',disconnect:'断开并清除凭据',privacy:'Cookie 仅保存在这台电脑的内存中，不写入磁盘、浏览器存储或第三方服务器。',
  howCookie:'在哪里找到 Cookie？',cookie1:'打开 chatgpt.com 并登录。',
  cookie2:'打开开发者工具 → Network（网络），刷新页面。',cookie3:'选择发往 chatgpt.com 的请求 → Headers → Request Headers → Cookie，复制它的值。',
  driveTitle:'在 Google Drive 留一份',driveHelp:'可选。使用你自己的 Google OAuth 桌面应用客户端 JSON 连接。',
  driveConnect:'连接 Google Drive',setupGuide:'查看配置指南 ↗',libraryTitle:'你的会话',libraryHelp:'选出值得保留的那些对话。',
  recent:'最近',archived:'仅已归档',load:'加载会话',emptyTitle:'给好想法，留一点空间。',
  emptyHelp:'连接 ChatGPT，找到最近的会话。\n你的下一份备份，从这里开始。',selectAll:'全选',sorted:'按最近更新时间排序 · UTC',
  formats:'每个会话一份 PDF，附 Markdown 和 JSON',export:'创建备份 ↓',footer:'为那些你想留下的对话而做。'
};
const english = {};
document.querySelectorAll('[data-i18n]').forEach(el => { english[el.dataset.i18n] = el.innerText; });
function t(en, chinese) { return language === 'zh' ? chinese : en; }
function translate() {
  document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
  document.querySelectorAll('[data-i18n]').forEach(el => {
    el.innerText = (language === 'zh' ? zh : english)[el.dataset.i18n];
  });
  $('lang').textContent = language === 'zh' ? 'EN' : '中文';
  if (driveConnected) $('drive-label').textContent = t('Google Drive connected ✓','Google Drive 已连接 ✓');
  updateSelection();
}
function notify(message) { $('notice').textContent = message; $('notice').hidden = false; }
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {
    'X-Vault-Token':token, 'Content-Type':'application/json', ...(options.headers || {})
  }});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || body.detail || `HTTP ${response.status}`);
  return body;
}
function connectionState(value) {
  connected = value;
  $('connect-form').hidden = value;
  $('connected-panel').hidden = !value;
  $('load').disabled = !value;
}
function selectedIds() { return [...document.querySelectorAll('.chat-check:checked')].map(el => el.value); }
function updateSelection() {
  const count = selectedIds().length;
  $('selected').textContent = t(`${count} selected`, `已选择 ${count} 个会话`);
  $('count').textContent = t(`${items.length} chats`, `${items.length} 个会话`);
  $('export').disabled = !count || Boolean(currentJob && currentJob.status === 'running');
  $('select-all').checked = count > 0 && count === items.length;
  $('select-all').indeterminate = count > 0 && count < items.length;
}
function showItems() {
  $('rows').replaceChildren();
  for (const item of items) {
    const row = document.createElement('label'); row.className = 'conversation-row';
    const check = document.createElement('input'); check.type = 'checkbox'; check.className = 'chat-check';
    check.value = item.id; check.checked = true; check.addEventListener('change',updateSelection);
    const icon = document.createElement('span'); icon.className = 'row-icon'; icon.textContent = '▤';
    const main = document.createElement('div'); main.className = 'row-main';
    const title = document.createElement('div'); title.className = 'row-title'; title.textContent = item.title;
    const date = document.createElement('div'); date.className = 'row-date';
    date.textContent = item.updated_at ? item.updated_at.replace('T',' ').slice(0,16) + ' UTC' : '—';
    main.append(title,date); row.append(check,icon,main); $('rows').append(row);
  }
  $('empty').hidden = items.length > 0; $('conversation-list').hidden = !items.length; updateSelection();
}
async function loadChats() {
  const limit = Number($('limit').value);
  if (!Number.isInteger(limit) || limit < 1 || limit > 500) throw new Error(t('Choose 1–500 chats.','请选择 1–500 个会话。'));
  $('load').disabled = true;
  try { const data = await api(`/api/conversations?limit=${limit}&archived=${$('archived').checked}`); items=data.items;showItems(); }
  finally { $('load').disabled = !connected; }
}
async function download(name) {
  try {
    const response = await fetch(`/api/download/${currentJob.id}/${encodeURIComponent(name)}`, {headers:{'X-Vault-Token':token}});
    if (!response.ok) throw new Error(t('Download failed.','下载失败。'));
    const url = URL.createObjectURL(await response.blob());
    const a = document.createElement('a'); a.href=url;a.download=name;a.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  } catch (error) { notify(error.message); }
}
function button(text, action, className='button subtle') {
  const el=document.createElement('button');el.type='button';el.className=className;el.textContent=text;
  el.addEventListener('click',action);return el;
}
function showResult(job) {
  $('results').replaceChildren();
  for (const item of job.manifest.conversations) {
    const row=document.createElement('div');row.className='result-row';
    const title=document.createElement('span');title.textContent=item.title;
    row.append(title,button('PDF ↓',()=>download(item.pdf),''));$('results').append(row);
    for (const warning of item.warnings) {const p=document.createElement('p');p.className='warning';p.textContent=warning;$('results').append(p);}
  }
  for (const failure of job.manifest.failures) {
    const p=document.createElement('p');p.className='warning';p.textContent=`${failure.id}: ${failure.error}`;$('results').append(p);
  }
  const actions=document.createElement('div');actions.className='download-line';
  actions.append(button(t('Download ZIP ↓','下载完整 ZIP ↓'),()=>download('session-archives.zip')));
  const upload=button(t('Upload PDFs to Drive ↗','上传 PDF 到 Drive ↗'),async()=>{
    if (!driveConnected) {notify(t('Connect Google Drive in the left panel first.','请先在左侧连接 Google Drive。'));return;}
    try {await api(`/api/jobs/${job.id}/upload`,{method:'POST'});upload.disabled=true;pollUpload(job.id,upload);}catch(e){notify(e.message);}
  });
  actions.append(upload);$('results').append(actions);
}
async function pollJob(id) {
  try {
    const job=await api(`/api/jobs/${id}`);currentJob=job;
    $('progress-title').textContent=job.status==='done'?t('Your archive is ready.','备份已就绪。'):t(job.message,'正在导出会话…');
    $('progress-count').textContent=`${job.done} / ${job.total}`;
    $('progress-meter').max=job.total;$('progress-meter').value=job.done;
    if(job.status==='running') setTimeout(()=>pollJob(id),1000);
    else if(job.status==='failed') {$('progress-title').textContent=t('Export stopped','导出已停止');notify(job.error);}
    else showResult(job);
    updateSelection();
  } catch(error){notify(error.message);currentJob=null;updateSelection();}
}
async function pollUpload(id,el) {
  try {
    const job=await api(`/api/jobs/${id}`);
    if(job.upload_status==='running') {el.textContent=t('Uploading…','正在上传…');setTimeout(()=>pollUpload(id,el),1500);}
    else if(job.upload_status==='done') {el.textContent=t('Saved to Drive ✓','已保存到 Drive ✓');}
    else {el.disabled=false;notify(job.upload_error || 'Upload failed');}
  }catch(error){el.disabled=false;notify(error.message);}
}
$('lang').addEventListener('click',()=>{language=language==='zh'?'en':'zh';translate();});
$('connect-form').addEventListener('submit',async event=>{
  event.preventDefault();$('connect').disabled=true;$('notice').hidden=true;
  try {
    await api('/api/connect',{method:'POST',body:JSON.stringify({cookie:$('cookie').value,user_agent:$('user-agent').value,account_id:$('account-id').value,proxy:$('proxy').value})});
    $('cookie').value='';connectionState(true);await loadChats();
  }catch(error){notify(error.message);}finally{$('connect').disabled=false;}
});
$('disconnect').addEventListener('click',async()=>{
  try{await api('/api/disconnect',{method:'POST'});connectionState(false);driveConnected=false;items=[];showItems();translate();}catch(error){notify(error.message);}
});
$('load').addEventListener('click',()=>loadChats().catch(e=>notify(e.message)));
$('select-all').addEventListener('change',()=>{document.querySelectorAll('.chat-check').forEach(el=>{el.checked=$('select-all').checked;});updateSelection();});
$('export').addEventListener('click',async()=>{
  $('export').disabled=true;
  try {
    const job=await api('/api/export',{method:'POST',body:JSON.stringify({ids:selectedIds()})});
    currentJob={id:job.id,status:'running'};$('results').replaceChildren();$('progress').hidden=false;
    pollJob(job.id);
  }catch(error){notify(error.message);updateSelection();}
});
$('google-client').addEventListener('change',async event=>{
  const file=event.target.files[0];if(!file)return;
  try {
    if(file.size>100000)throw new Error('OAuth client JSON must be smaller than 100 KB.');
    const config=JSON.parse(await file.text());
    await api('/api/drive/connect',{method:'POST',body:JSON.stringify({client_config:config})});
    $('drive-label').textContent=t('Authorize in your browser…','请在浏览器中授权…');
    async function poll(){
      try{const status=await api('/api/drive/status');
        if(status.pending){setTimeout(poll,1500);return;}
        driveConnected=status.connected;translate();
        if(status.error)notify(status.error);
      }catch(error){notify(error.message);}
    }poll();
  }catch(error){notify(error.message);}finally{event.target.value='';}
});
translate();
fetch('/api/bootstrap').then(r=>r.json()).then(data=>{token=data.token;connectionState(data.connected);driveConnected=data.drive_connected;translate();}).catch(()=>notify('Could not connect to the local Vault server. Reload the page.'));
