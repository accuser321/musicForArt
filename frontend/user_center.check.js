
      const GENRES = ['玄幻', '言情', '悬疑', '科幻'];
      let authToken = localStorage.getItem('mfa_auth_token') || '';
      let authPhone = localStorage.getItem('mfa_auth_phone') || '';
      let authUser = null;
      let worksState = { genre: '玄幻', page: 1, total: 0, items: [] };
      let booksState = { genre: '玄幻', page: 1, total: 0, items: [] };
      function apiBase() { return document.getElementById('apiBase').value.replace(/\/$/, ''); }
      function escHtml(s) { return String(s ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;'); }
      function authHeaders(extra = {}) { const h = { ...(extra || {}) }; if (authToken) h.Authorization = `Bearer ${authToken}`; return h; }
      async function apiFetch(url, options = {}) { const opts = { ...(options || {}) }; opts.headers = authHeaders(opts.headers || {}); return window.fetch(url, opts); }
      async function readApiResponse(res) { const t = await res.text(); try { return JSON.parse(t); } catch { return { detail: t }; } }
      function normalizePhoneInput(raw) { let phone = String(raw || '').replace(/\D/g, ''); if (phone.startsWith('86') && phone.length === 13) phone = phone.slice(2); return phone; }
      function isValidPhoneInput(raw) { return /^1\d{10}$/.test(normalizePhoneInput(raw)); }
      function themeCls(genre) { return ({'玄幻':'theme-xuanhuan','言情':'theme-yanqing','悬疑':'theme-xuanyi','科幻':'theme-kehuan'})[String(genre||'').trim()] || ''; }
      function setLoginState() { document.getElementById('loginState').textContent = authPhone ? `${authPhone}${authUser && authUser.uid ? ` / UID:${authUser.uid}` : ''}（已登录）` : '未登录'; }
      function renderTabs(containerId, current, handlerName) {
        const box = document.getElementById(containerId);
        if (!box) return;
        box.innerHTML = GENRES.map((genre) => `<button class="tab ${genre === current ? 'active' : ''}" onclick="${handlerName}('${genre}')">${escHtml(genre)}</button>`).join('');
      }
      function renderWorks() {
        renderTabs('worksTabs', worksState.genre, 'setWorksGenre');
        const box = document.getElementById('worksList');
        const info = document.getElementById('worksPagerInfo');
        const totalPages = Math.max(1, Math.ceil((worksState.total || 0) / 8));
        info.textContent = `第 ${worksState.page} / ${totalPages} 页，当前赛道：${worksState.genre}`;
        box.innerHTML = (worksState.items || []).length ? worksState.items.map((item) => `
          <div class="item-card ${themeCls(item.genre)}">
            <div style="font-weight:700;">${escHtml(item.title || '-')}</div>
            <div class="meta">${escHtml(item.genre || '')} ｜ ${escHtml(item.role_label || '创作者')}</div>
            <div style="margin-top:6px; line-height:1.7;">${escHtml(item.summary || '')}</div>
            <div style="margin-top:6px;">${(Array.isArray(item.skills) ? item.skills : []).slice(0, 6).map((skill) => `<span class="pill">${escHtml(skill)}</span>`).join('')}</div>
          </div>
        `).join('') : '<div class="hint">当前赛道还没有已发布作品。</div>';
      }
      function renderBooks() {
        renderTabs('booksTabs', booksState.genre, 'setBooksGenre');
        const box = document.getElementById('booksList');
        const info = document.getElementById('booksPagerInfo');
        const totalPages = Math.max(1, Math.ceil((booksState.total || 0) / 8));
        info.textContent = `第 ${booksState.page} / ${totalPages} 页，当前赛道：${booksState.genre}`;
        box.innerHTML = (booksState.items || []).length ? booksState.items.map((item) => `
          <div class="item-card ${themeCls(item.genre)}">
            <div style="font-weight:700;">${escHtml(item.title || '-')}</div>
            <div class="meta">${escHtml(item.genre || '')} ｜ 预算：${escHtml(item.budget_text || '待沟通')} ｜ 保证金 ${escHtml(item.deposit_amount || 0)} 元</div>
            <div style="margin-top:6px; line-height:1.7;">${escHtml(item.description || '')}</div>
          </div>
        `).join('') : '<div class="hint">当前赛道还没有已发布书单。</div>';
      }
      async function loadWorks() {
        const res = await window.fetch(`${apiBase()}/home/creator-showcases?genre=${encodeURIComponent(worksState.genre)}&page=${worksState.page}&page_size=8`);
        const data = await readApiResponse(res);
        worksState.items = Array.isArray(data.items) ? data.items : [];
        worksState.total = Number(data.total || 0);
        renderWorks();
      }
      async function loadBooks() {
        const res = await window.fetch(`${apiBase()}/home/copyright-ads?genre=${encodeURIComponent(booksState.genre)}&page=${booksState.page}&page_size=8`);
        const data = await readApiResponse(res);
        booksState.items = Array.isArray(data.items) ? data.items : [];
        booksState.total = Number(data.total || 0);
        renderBooks();
      }
      function setWorksGenre(genre) { worksState.genre = genre; worksState.page = 1; loadWorks(); }
      function setBooksGenre(genre) { booksState.genre = genre; booksState.page = 1; loadBooks(); }
      function changeWorksPage(delta) { const totalPages = Math.max(1, Math.ceil((worksState.total || 0) / 8)); worksState.page = Math.max(1, Math.min(totalPages, worksState.page + delta)); loadWorks(); }
      function changeBooksPage(delta) { const totalPages = Math.max(1, Math.ceil((booksState.total || 0) / 8)); booksState.page = Math.max(1, Math.min(totalPages, booksState.page + delta)); loadBooks(); }
      async function requestCode() {
        const phone = normalizePhoneInput((document.getElementById('loginPhone').value || '').trim());
        document.getElementById('loginPhone').value = phone;
        if (!isValidPhoneInput(phone)) return alert('请输入有效的11位手机号');
        const res = await window.fetch(`${apiBase()}/auth/request-code`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ phone }) });
        const data = await readApiResponse(res);
        if (data.code) document.getElementById('loginCode').value = data.code;
        alert(data.message || data.detail || '验证码已发送');
      }
      async function loginByCode() {
        const phone = normalizePhoneInput((document.getElementById('loginPhone').value || '').trim());
        const code = (document.getElementById('loginCode').value || '').trim();
        if (!isValidPhoneInput(phone) || !code) return alert('请输入手机号和验证码');
        const res = await window.fetch(`${apiBase()}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ phone, code }) });
        const data = await readApiResponse(res);
        if (!res.ok || !data.token) return alert(data.detail || '登录失败');
        authToken = data.token; authPhone = data.user?.phone || phone; authUser = data.user || null;
        localStorage.setItem('mfa_auth_token', authToken); localStorage.setItem('mfa_auth_phone', authPhone);
        setLoginState();
        await loadRechargeSummary();
        alert('登录成功');
      }
      async function syncAuthBootstrap() {
        if (!authToken) { setLoginState(); return; }
        try {
          const res = await apiFetch(`${apiBase()}/auth/me`);
          const data = await readApiResponse(res);
          if (res.ok) { authUser = data; authPhone = data.phone || authPhone; localStorage.setItem('mfa_auth_phone', authPhone); }
        } catch (_) {}
        setLoginState();
        await loadRechargeSummary();
      }
      async function loadRechargeSummary() {
        const box = document.getElementById('rechargeSummaryView');
        if (!authToken) { box.innerHTML = '请先登录后查看余额与充值记录。'; return; }
        const res = await apiFetch(`${apiBase()}/home/recharge-summary`);
        const data = await readApiResponse(res);
        if (!res.ok) { box.innerHTML = escHtml(data.detail || '加载失败'); return; }
        const balances = data.balances || {};
        const orders = Array.isArray(data.orders) ? data.orders : [];
        box.innerHTML = `
          <div class="kv"><div>文字包余额</div><div>${escHtml(balances.text_char_pack_balance || 0)} 字</div></div>
          <div class="kv"><div>音效下载包</div><div>${escHtml(balances.sfx_download_pack_balance || 0)} 次</div></div>
          <div class="kv"><div>保证金余额</div><div>${escHtml(Number(balances.deposit_balance || 0).toFixed(2))} 元</div></div>
          <div class="hint" style="margin-top:8px;">最近订单：${orders.length ? escHtml(`${orders[0].package_name || '-'} / ${orders[0].status_label || orders[0].status || '-'}`) : '暂无'}</div>
        `;
      }
      async function submitWork() {
        if (!authToken) return alert('请先登录');
        const fd = new FormData();
        fd.set('title', (document.getElementById('workTitle').value || '').trim());
        fd.set('genre', (document.getElementById('workGenre').value || '').trim());
        fd.set('role_label', (document.getElementById('workRole').value || '').trim());
        fd.set('summary', (document.getElementById('workSummary').value || '').trim());
        fd.set('skills', (document.getElementById('workSkills').value || '').trim());
        fd.set('sample_link', (document.getElementById('workLink').value || '').trim());
        const file = document.getElementById('workFile').files[0];
        if (file) fd.set('sample_file', file);
        const res = await apiFetch(`${apiBase()}/home/creator-showcases`, { method: 'POST', body: fd });
        const data = await readApiResponse(res);
        alert(data.message || data.detail || '提交完成');
      }
      async function submitBooklist() {
        if (!authToken) return alert('请先登录');
        const payload = {
          title: (document.getElementById('bookTitle').value || '').trim(),
          genre: (document.getElementById('bookGenre').value || '').trim(),
          description: (document.getElementById('bookDescription').value || '').trim(),
          budget_text: (document.getElementById('bookBudget').value || '').trim(),
          deposit_amount: Number(document.getElementById('bookDeposit').value || 0),
          contact_note: (document.getElementById('bookContact').value || '').trim(),
        };
        const res = await apiFetch(`${apiBase()}/home/copyright-ads`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        const data = await readApiResponse(res);
        alert(data.message || data.detail || '提交完成');
      }
      async function submitRechargeOrder() {
        if (!authToken) return alert('请先登录');
        const payload = {
          order_type: (document.getElementById('rechargeOrderType').value || '').trim(),
          package_name: (document.getElementById('rechargePackageName').value || '').trim(),
          units: Number(document.getElementById('rechargeUnits').value || 0),
          payable_amount: Number(document.getElementById('rechargeAmount').value || 0),
          deposit_offset: Number(document.getElementById('rechargeDepositOffset').value || 0),
          note: (document.getElementById('rechargeNote').value || '').trim(),
        };
        const res = await apiFetch(`${apiBase()}/home/recharge-orders`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        const data = await readApiResponse(res);
        alert(data.message || data.detail || '提交完成');
        await loadRechargeSummary();
      }
      syncAuthBootstrap();
      loadWorks();
      loadBooks();
    