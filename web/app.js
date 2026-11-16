/* JobAutomater dashboard (Alpine.js). One component, one page, hash-routed tabs. */

const api = async (path, opts = {}) => {
  const o = { headers: {}, ...opts };
  if (o.body && !(o.body instanceof FormData)) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(o.body); }
  const r = await fetch(path, o);
  if (!r.ok) { let d = ''; try { d = (await r.json()).detail } catch { } throw new Error(d || r.statusText); }
  const ct = r.headers.get('content-type') || '';
  return ct.includes('json') ? r.json() : r.text();
};

const STATUS = {
  queued: ['Queued', 'b-grey'], applying: ['Applying', 'b-blue'], review: ['Ready to submit', 'b-violet'],
  needs_you: ['Needs you', 'b-warn'], submitted: ['Submitted', 'b-blue'], confirmed: ['Company confirmed', 'b-good'],
  replied: ['Replied', 'b-good'], interview: ['Interview', 'b-good'], rejected: ['Not selected', 'b-bad'],
  failed: ['Failed', 'b-bad'], skipped: ['Skipped', 'b-grey'], dry_run: ['Dry run', 'b-grey'],
};
const CAT = { confirmed: ['Company confirmed', 'b-good'], assessment: ['Assessment', 'b-violet'], interview: ['Interview', 'b-good'],
  rejected: ['Not selected', 'b-bad'], other: ['Update', 'b-grey'] };

function ago(t) {
  if (!t) return '';
  const s = Date.now() / 1000 - t;
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  const d = Math.floor(s / 86400);
  return d === 1 ? 'yesterday' : d + 'd ago';
}
function opened(t) {
  if (!t) return '';
  const d = Math.floor((Date.now() / 1000 - t) / 86400);
  return d <= 0 ? 'Opened today' : d === 1 ? 'Opened yesterday' : `Opened ${d} days ago`;
}
function when(t) {
  if (!t) return '';
  const d = new Date(t * 1000);
  return d.toLocaleDateString('en-IN', { weekday: 'short' }) + ', ' + d.toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' });
}
function ringColor(s) { return s >= 75 ? '#2f7d4f' : s >= 55 ? '#c89a1a' : '#c0532b'; }

function app() {
  return {
    tab: 'today', state: {}, toastMsg: '', logs: [],
    // today
    td: { jobs: [], progress: {}, cap: 25, sent_today: 0, queued_today: 0 }, tdSel: {}, tdDays: 1, tdMode: 'auto',
    tdPhase: 'start', tdProgress: null, tdMin: 0,
    // jobs
    jobs: [], jobsTotal: 0, sources: [], jf: { q: '', sort: 'best', exp: '', employer: '', where: '', source: '', min_score: 0, status: 'new' },
    jobsPage: 0, openJob: null, jobDesc: {}, search: {}, applyTopN: 10, loadingJobs: false, titleInput: '',
    // applications
    apps: [], appCounts: {}, appStats: {}, af: { status: '', q: '' }, detail: null, lightbox: null, answersDraft: {}, showDesc: false,
    // tracker
    trackerView: 'inbox', mails: [], mailCounts: {}, mf: { category: '', q: '' }, mail: null, inboxConfigured: true, pipelineCols: {},
    // live
    sessions: [], liveBig: null, sockets: {},
    // resume
    resume: {}, resumeHtml: '', tpl: 'classic', notes: '', upgrade: null, upgrading: false, savingResume: false, dragOver: false,
    profileYaml: '', answerBank: [], draftHtml: '', uploading: false, pf: {}, pfDirty: false, pfQuick: [], infoTxt: {}, infoDirty: false,
    // settings
    settings: {}, llmPing: null,

    async init() {
      const h = location.hash.replace('#', '');
      if (h) this.go(h, false);
      window.addEventListener('hashchange', () => this.go(location.hash.replace('#', ''), false));
      await this.refreshState();
      this.connectEvents();
      this.loadTab();
      setInterval(() => this.refreshState(), 15000);
    },
    go(t, push = true) {
      const [tab, id] = t.split('/');
      this.tab = tab || 'today';
      if (tab === 'applications' && id) this.openApp(+id); else if (tab === 'applications') this.detail = null;
      if (push) location.hash = t;
      this.loadTab();
    },
    loadTab() {
      if (this.tab === 'today') this.loadToday();
      if (this.tab === 'jobs') { this.loadJobs(); this.loadSearch(); }
      if (this.tab === 'applications' && !this.detail) this.loadApps();
      if (this.tab === 'tracker') this.trackerView === 'inbox' ? this.loadMails() : this.loadPipeline();
      if (this.tab === 'live') this.loadSessions();
      if (this.tab === 'resume') this.loadResume();
      if (this.tab === 'profile') this.loadProfile();
      if (this.tab === 'settings') this.loadSettings();
    },
    toast(m) { this.toastMsg = m; clearTimeout(this._t); this._t = setTimeout(() => this.toastMsg = '', 3500); },
    async refreshState() {
      try {
        this.state = await api('/api/state'); this.sessions = this.state.sessions || [];
        const miss = this.state.profile_missing || [];
        if (miss.join('|') !== (this.pfQuick || []).map(m => m.label).join('|')) {
          const f = await api('/api/profile/form');
          this.pfQuick = f.missing.map(m => ({ ...m, value: '' }));
        }
      } catch (e) { }
    },
    async saveProfileForm() {
      const values = {};
      for (const g of this.pf.groups || []) for (const f of g.fields) values[f.path] = f.value;
      try {
        this.pf = await api('/api/profile/form', { method: 'PUT', body: { values } }); this.pfDirty = false;
        this.toast('Details saved'); this.profileYaml = (await api('/api/profile')).yaml; this.refreshState();
      } catch (e) { this.toast(e.message); }
    },
    async loadProfile() {
      this.infoTxt = await api('/api/info'); this.infoDirty = false;
      this.pf = await api('/api/profile/form'); this.pfDirty = false;
      this.profileYaml = (await api('/api/profile')).yaml;
    },
    async saveInfo() {
      try { this.infoTxt = await api('/api/info', { method: 'PUT', body: { text: this.infoTxt.text } }); this.infoDirty = false; this.toast('info.txt saved'); this.pf = await api('/api/profile/form'); this.refreshState(); } catch (e) { this.toast(e.message); }
    },
    async saveQuick() {
      const values = {};
      for (const m of this.pfQuick) if (String(m.value).trim()) values[m.path] = m.value;
      if (!Object.keys(values).length) return this.toast('Type at least one');
      await api('/api/profile/form', { method: 'PUT', body: { values } }); this.toast('Saved'); this.pfQuick = []; this.refreshState();
    },
    async fillFromResume() {
      const r = await api('/api/profile/from-resume', { method: 'POST', body: {} });
      this.pf = { groups: r.groups, missing: r.missing }; this.pfDirty = false;
      this.toast(r.changed.length ? `Filled ${r.changed.length} details from your resume` : 'Nothing new on the resume');
      this.profileYaml = (await api('/api/profile')).yaml; this.refreshState();
    },
    async signIn(url) {
      const r = await api('/api/browser/signin', { method: 'POST', body: url ? { url } : {} });
      if (r.ok) this.toast('A browser window opened. Sign in there, then close it.'); else this.toast(r.error);
      this.refreshState();
    },
    async signInUrl() { const u = prompt('Website to sign in to (e.g. a Workday careers page)'); if (u) this.signIn(u.startsWith('http') ? u : 'https://' + u); },
    get needsYou() { const c = this.state.app_counts || {}; return (c.needs_you || 0) + (c.review || 0); },

    connectEvents() {
      const ws = new WebSocket(`ws://${location.host}/ws/events`);
      ws.onmessage = (m) => {
        const e = JSON.parse(m.data);
        if (e.kind === 'log' && !this.logs.some(l => l.at === e.at && l.message === e.message)) { this.logs.push(e); if (this.logs.length > 300) this.logs.shift(); }
        if (e.kind === 'app_status' || e.kind === 'app_event') {
          if (this.tab === 'applications') { this.detail && this.detail.application.id === e.application_id ? this.openApp(e.application_id, true) : this.loadApps(); }
          if (e.kind === 'app_status' && e.status === 'needs_you') this.toast('An application needs you');
          if (e.kind === 'app_status' && e.status === 'submitted') this.toast('Application submitted');
          this.refreshState();
        }
        if (e.kind === 'jobs_ranked' && this.tab === 'jobs') this.loadJobs();
        if (e.kind === 'discovery' && e.state === 'running' && e.total) this.tdProgress = { done: e.progress, total: e.total };
        if (e.kind === 'discovery' && e.state === 'done' && this.tdPhase === 'searching') { this.tdPhase = 'pick'; this.tdProgress = null; this.loadToday(true); }
        if (e.kind === 'app_status' && this.tab === 'today') this.loadToday();
        if (e.kind === 'discovery') { this.state.discovering = e.state === 'running'; if (e.state === 'done') { this.toast(`Discovery done: ${e.summary.new} new jobs`); this.refreshState(); } }
        if (e.kind === 'session' || e.kind === 'session_closed') { this.loadSessions(); }
        if (e.kind === 'inbox' && this.tab === 'tracker') this.loadMails();
        if (e.kind === 'discovery_slow' && e.state === 'done' && e.new) { this.toast(`Workday boards added ${e.new} roles`); if (this.tab === 'today') this.loadToday(); if (this.tab === 'jobs') this.loadJobs(); }
      };
      ws.onclose = () => setTimeout(() => this.connectEvents(), 3000);
    },

    // ------------------------------------------------ today
    async loadToday(selectDefaults = false) {
      const r = await api(`/api/today?days=${this.tdDays}&min_score=${this.tdMin}`);
      this.td = r;
      if (this.tdPhase === 'start' && r.queued_today) this.tdPhase = 'applying';
      else if (this.tdPhase === 'start' && r.started_at) this.tdPhase = 'pick';
      if (selectDefaults || !Object.keys(this.tdSel).length) {
        this.tdSel = {};
        // tick the best roles that can be sent end to end; assisted ones (sign-in needed) stay unticked
        let pick = r.jobs.filter(j => j.auto && (j.score || 0) >= r.min_score_default);
        if (!pick.length) pick = r.jobs.filter(j => j.auto && (j.score || 0) >= r.min_score_default - 15);
        pick.slice(0, this.tdRoom).forEach(j => this.tdSel[j.id] = true);
      }
    },
    get tdRoom() { return Math.max(0, (this.td.cap || 0) - (this.td.sent_today || 0) - (this.td.queue || 0)); },
    get tdCount() { return Object.values(this.tdSel).filter(Boolean).length; },
    async todayStart(force = false) {
      this.tdPhase = 'searching'; this.tdProgress = null;
      const r = await api('/api/today/start', { method: 'POST', body: { force } });
      if (!r.searching) { this.tdPhase = 'pick'; await this.loadToday(true); this.toast('Using the search from ' + ago(r.fresh_from)); }
    },
    tdSelectAll(on) { this.tdSel = {}; if (on) this.td.jobs.slice(0, this.tdRoom).forEach(j => this.tdSel[j.id] = true); },
    async todayApply() {
      const ids = this.td.jobs.filter(j => this.tdSel[j.id]).map(j => j.id);
      if (!ids.length) return this.toast('Pick at least one job');
      const r = await api('/api/today/apply', { method: 'POST', body: { ids, mode: this.tdMode } });
      this.toast(`${r.queued} applications started` + (r.over_cap ? `; ${r.over_cap} wait for tomorrow (daily cap)` : ''));
      this.tdPhase = 'applying'; this.tdSel = {}; this.loadToday(); this.refreshState();
    },
    get tdDone() { const p = this.td.progress || {}; return this.td.queued_today > 0 && !(p.queued || p.applying); },

    // ------------------------------------------------ jobs
    async loadJobs(append = false) {
      this.loadingJobs = true;
      if (!append) this.jobsPage = 0;
      const p = new URLSearchParams({ ...this.jf, limit: 40, offset: this.jobsPage * 40 });
      try {
        const r = await api('/api/jobs?' + p);
        this.jobs = append ? this.jobs.concat(r.jobs) : r.jobs;
        this.jobsTotal = r.total; this.sources = r.sources;
      } finally { this.loadingJobs = false; }
    },
    more() { this.jobsPage++; this.loadJobs(true); },
    async loadSearch() { this.search = await api('/api/search-prefs'); },
    async saveSearch(patch) {
      Object.assign(this.search, patch);
      this.toast('Updating your matches…');
      await api('/api/search-prefs', { method: 'PUT', body: patch });
      this.loadJobs();
    },
    addTitle() {
      const t = this.titleInput.trim(); if (!t) return;
      const list = [...(this.search.target_titles || []), t];
      this.titleInput = ''; this.saveSearch({ target_titles: list });
    },
    removeTitle(t) { this.saveSearch({ target_titles: (this.search.target_titles || []).filter(x => x !== t) }); },
    async toggleJob(j) {
      this.openJob = this.openJob === j.id ? null : j.id;
      if (this.openJob && !this.jobDesc[j.id]) this.jobDesc[j.id] = (await api('/api/jobs/' + j.id)).description || '';
    },
    async apply(j, mode) {
      const r = await api(`/api/jobs/${j.id}/apply`, { method: 'POST', body: mode ? { mode } : {} });
      j.status = 'queued'; this.toast(`Queued: ${j.title} at ${j.company}`); this.refreshState();
      return r;
    },
    async dismiss(j) { await api(`/api/jobs/${j.id}/dismiss`, { method: 'POST' }); this.jobs = this.jobs.filter(x => x.id !== j.id); this.jobsTotal--; },
    async applyTop() {
      const r = await api('/api/jobs/apply-top', { method: 'POST', body: { n: +this.applyTopN, min_score: this.settings.min_score_to_apply || this.state.settings?.min_score_to_apply } });
      this.toast(`${r.queued} applications queued`); this.loadJobs(); this.refreshState();
    },
    async discover() { await api('/api/discover', { method: 'POST' }); this.state.discovering = true; this.toast('Searching every source…'); },
    ring(s) { const r = 19, c = 2 * Math.PI * r, v = Math.max(0, Math.min(100, s || 0)); return { c, off: c * (1 - v / 100), color: ringColor(v) }; },
    initials(n) { return (n || '?').trim().split(/\s+/).slice(0, 2).map(w => w[0]).join('').toUpperCase(); },
    tags(j) {
      const b = j.breakdown || {}, t = [];
      if (b.level_label === 'entry') t.push('Freshers welcome');
      if (b.years_req) t.push(`${b.years_req}+ years`);
      if (j.remote) t.push('Remote');
      if (j.salary && j.salary.trim() && !/None/.test(j.salary)) t.push(j.salary);
      return t;
    },
    credit(j) { return (j.extra && j.extra.credit) || j.source; },

    // ------------------------------------------------ applications
    async loadApps() {
      const r = await api('/api/applications?' + new URLSearchParams(this.af));
      this.apps = r.applications; this.appCounts = r.counts; this.appStats = r.stats;
    },
    get appTotal() { return Object.values(this.appCounts).reduce((a, b) => a + b, 0); },
    async openApp(id, quiet) {
      const d = await api('/api/applications/' + id);
      this.detail = d;
      if (!quiet) { this.answersDraft = {}; d.questions.forEach(q => this.answersDraft[q] = ''); window.scrollTo(0, 0); }
      if (location.hash !== '#applications/' + id) history.replaceState(null, '', '#applications/' + id);
    },
    closeApp() { this.detail = null; location.hash = 'applications'; this.loadApps(); },
    headline(d) {
      const a = d.application, c = d.job.company;
      const m = {
        confirmed: [`Sent to ${c}.`, 'They confirmed.'], submitted: [`Sent to ${c}.`, 'Waiting for their email.'],
        replied: [`${c} wrote back.`, 'Check the Tracker.'], interview: [`${c} wants to talk.`, 'Interview!'],
        rejected: [`${c} replied.`, 'Not this time.'], needs_you: [`${c} needs you.`, 'One step left.'],
        review: [`Filled for ${c}.`, 'Ready to submit.'], applying: [`Applying to ${c}.`, 'Watch it live.'],
        queued: [`${c} is in the queue.`, 'Starting soon.'], failed: [`${c} didn't go through.`, 'Retry?'],
        dry_run: [`Dry run for ${c}.`, 'Not submitted.'], skipped: [`${c} was skipped.`, ''],
      };
      return m[a.status] || [c, ''];
    },
    timeline(d) {
      const ev = s => d.events.find(e => e.step === s);
      const st = d.application.status;
      const steps = [
        { k: 'approved', w: 'You said yes', e: ev('approved') },
        { k: 'form_filled', w: 'Form filled', e: ev('form_filled') },
        { k: 'submitted', w: 'Submitted', e: ev('submitted') },
        { k: 'confirmed', w: 'Company confirmed', e: ev('confirmed') || (['confirmed', 'replied', 'interview'].includes(st) ? ev('submitted') : null) },
      ];
      let firstOpen = steps.findIndex(s => !s.e);
      return steps.map((s, i) => ({ ...s, done: !!s.e, cur: i === firstOpen && ['needs_you', 'review', 'applying'].includes(st) }));
    },
    shot(d, step) { const e = [...d.events].reverse().find(e => e.step === step && e.screenshot); return e ? '/shots/' + e.screenshot + '?t=' + e.at : null; },
    async appAction(id, action, body) {
      try {
        const r = await api(`/api/applications/${id}/${action}`, { method: 'POST', body: body || {} });
        this.toast({ submit: 'Submitting…', retry: 'Retrying', cancel: 'Cancelled', answers: 'Saved. Retrying with your answers.' }[action] || 'Done');
        setTimeout(() => this.openApp(id, true), 800); return r;
      } catch (e) { this.toast(e.message); }
    },
    async markStatus(id, status) { await api(`/api/applications/${id}/status`, { method: 'POST', body: { status } }); this.openApp(id, true); },
    statusBadge(s) { return STATUS[s] || [s, 'b-grey']; },
    liveFor(d) { return this.sessions.find(s => s.application_id === d.application.id); },

    // ------------------------------------------------ tracker
    async loadMails() {
      const r = await api('/api/inbox?' + new URLSearchParams(this.mf));
      this.mails = r.emails; this.mailCounts = r.counts; this.inboxConfigured = r.configured;
      if (!this.mail && this.mails.length) this.openMail(this.mails[0]);
    },
    async openMail(m) { this.mail = await api('/api/inbox/' + encodeURIComponent(m.id)); m.is_read = 1; },
    async refreshInbox() { this.toast('Checking your inbox…'); const r = await api('/api/inbox-refresh', { method: 'POST' }); this.toast(r.error ? 'Inbox: ' + r.error : `${r.new || 0} new emails`); this.loadMails(); },
    async loadPipeline() { this.pipelineCols = await api('/api/pipeline'); },
    catBadge(c) { return CAT[c] || CAT.other; },
    get mailTotal() { return Object.values(this.mailCounts).reduce((a, b) => a + b, 0); },

    // ------------------------------------------------ live
    async loadSessions() { this.sessions = await api('/api/sessions'); this.$nextTick(() => this.attachCanvases()); },
    attachCanvases() {
      document.querySelectorAll('canvas[data-sid]').forEach(cv => {
        const sid = cv.dataset.sid;
        if (cv._ws) return;
        const ws = new WebSocket(`ws://${location.host}/ws/live/${sid}`);
        cv._ws = ws; const ctx = cv.getContext('2d'); const img = new Image();
        img.onload = () => { cv.width = img.width; cv.height = img.height; ctx.drawImage(img, 0, 0); };
        ws.onmessage = (m) => {
          const f = JSON.parse(m.data);
          if (f.type === 'frame') { img.src = 'data:image/jpeg;base64,' + f.data; const s = this.sessions.find(x => x.id === sid); if (s) { s.url = f.url; s.step = f.step; s.state = f.state; s.taken_over = f.taken_over; } }
          if (f.type === 'closed') { ws.close(); }
        };
        this.sockets[sid + (cv.dataset.big ? 'b' : '')] = ws;
        if (cv.dataset.big) this.bindInput(cv, ws);
      });
    },
    bindInput(cv, ws) {
      const pos = e => { const r = cv.getBoundingClientRect(); return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height }; };
      cv.addEventListener('click', e => ws.send(JSON.stringify({ type: 'click', ...pos(e) })));
      cv.addEventListener('wheel', e => { e.preventDefault(); ws.send(JSON.stringify({ type: 'wheel', dy: e.deltaY })); }, { passive: false });
      const wrap = cv.parentElement; wrap.tabIndex = 0;
      wrap.addEventListener('keydown', e => {
        if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) ws.send(JSON.stringify({ type: 'text', text: e.key }));
        else ws.send(JSON.stringify({ type: 'key', key: (e.ctrlKey ? 'Control+' : '') + e.key }));
        e.preventDefault();
      });
      wrap.addEventListener('paste', e => { ws.send(JSON.stringify({ type: 'text', text: e.clipboardData.getData('text') })); e.preventDefault(); });
    },
    openLive(s) { this.liveBig = s; this.$nextTick(() => this.attachCanvases()); },
    closeLive() { const ws = this.sockets[this.liveBig.id + 'b']; if (ws) ws.close(); delete this.sockets[this.liveBig.id + 'b']; this.liveBig = null; },
    async takeover(s, on) { const r = await api(`/api/sessions/${s.id}/takeover`, { method: 'POST', body: { on } }); s.taken_over = r.taken_over; if (on) this.toast('You have control. Click into the screen and type.'); },
    async closeSession(s) { await api(`/api/sessions/${s.id}/close`, { method: 'POST' }); if (this.liveBig && this.liveBig.id === s.id) this.closeLive(); this.loadSessions(); },
    watch(appId) { const s = this.sessions.find(x => x.application_id === appId); this.go('live'); if (s) setTimeout(() => this.openLive(s), 300); },

    // ------------------------------------------------ resume
    async loadResume() {
      this.resume = await api('/api/resume');
      this.tpl = this.resume.template || 'classic';
      if (this.resume.struct) await this.renderResume();
      this.profileYaml = (await api('/api/profile')).yaml;
      this.pf = await api('/api/profile/form'); this.pfDirty = false;
      this.answerBank = await api('/api/answers');
    },
    async renderResume() {
      this.resumeHtml = await api(`/api/resume/render?template=${this.tpl}&editable=1`);
      this.$nextTick(() => {
        document.querySelectorAll('#paper [contenteditable]').forEach(el => {
          el.addEventListener('blur', async () => {
            await api('/api/resume/edit', { method: 'POST', body: { path: el.dataset.path, value: el.innerText.trim() } });
            this.toast('Saved');
          });
          el.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); el.blur(); } });
        });
      });
    },
    async pickTpl(k) { this.tpl = k; await this.renderResume(); },
    async uploadResume(file) {
      if (!file) return;
      const fd = new FormData(); fd.append('file', file); this.uploading = true;
      try { await api('/api/resume/upload', { method: 'POST', body: fd }); this.toast('Resume read. Ranking jobs against it…'); await this.loadResume(); this.refreshState(); }
      catch (e) { this.toast(e.message); } finally { this.uploading = false; }
    },
    async doUpgrade() {
      this.upgrading = true;
      try {
        this.upgrade = await api('/api/resume/upgrade', { method: 'POST', body: { notes: this.notes } });
        this.draftHtml = await api(`/api/resume/render?template=${this.tpl}&editable=0&draft=1`);
      } catch (e) { this.toast(e.message); } finally { this.upgrading = false; }
    },
    async draft(action) { await api('/api/resume/draft/' + action, { method: 'POST' }); this.upgrade = null; this.draftHtml = ''; if (action === 'accept') { this.notes = ''; this.toast('Upgrade applied'); } await this.loadResume(); },
    async saveResume(use) {
      this.savingResume = true;
      try { const r = await api('/api/resume/save', { method: 'POST', body: { template: this.tpl, use } }); this.toast(use ? `Saved ${r.pdf}. Applications will use it.` : 'Saved'); await this.loadResume(); }
      catch (e) { this.toast(e.message); } finally { this.savingResume = false; }
    },
    async saveProfile() { try { await api('/api/profile', { method: 'PUT', body: { yaml: this.profileYaml } }); this.toast('Details saved'); this.refreshState(); } catch (e) { this.toast(e.message); } },
    async saveResumeProfile(patch) { await api('/api/resume/profile', { method: 'PUT', body: patch }); this.toast('Saved. Re-ranking…'); this.loadResume(); },
    async delAnswer(a) { await api('/api/answers?label=' + encodeURIComponent(a.label), { method: 'DELETE' }); this.answerBank = this.answerBank.filter(x => x !== a); },
    async saveAnswer(a) { await api('/api/answers', { method: 'PUT', body: { label: a.label, answer: a.answer } }); this.toast('Answer saved'); },

    // ------------------------------------------------ settings
    async loadSettings() { this.settings = await api('/api/settings'); await this.refreshState(); },
    async saveSettings(patch) { this.settings = await api('/api/settings', { method: 'PUT', body: patch }); this.toast('Settings saved'); this.refreshState(); },
    async toggleAuto(on) { await this.saveSettings({ auto_apply_enabled: on }); this.toast(on ? `Auto-apply on (${this.state.settings.apply_mode} mode)` : 'Auto-apply off'); },
    async testLLM() { this.llmPing = { pending: true }; this.llmPing = await api('/api/llm/ping'); },

    // helpers exposed to template
    ago, opened, when,
  };
}
