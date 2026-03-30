
      if ('scrollRestoration' in history) {
        history.scrollRestoration = 'manual';
      }
      window.scrollTo(0, 0);
      let token = localStorage.getItem('mfa_admin_token') || '';
      let loginPhone = localStorage.getItem('mfa_admin_phone') || '';
      let latestSupplements = [];
      let graphMaintenanceState = null;
      let graphMaintenanceCatalogState = { genres: [], selectedGenreKey: '', selectedNodeKey: '', verbQuery: '' };
      let inheritanceDashboardState = { blocked_pool: { items: [], count: 0 }, review_pool: { items: [], count: 0, days: 30 } };
      const INHERITANCE_BLOCKED_PAGE_SIZE = 5;
      let inheritanceBlockedPageState = {
        currentPage: 1,
        totalPages: 1,
      };
      let inheritanceReviewPageState = {
        currentPage: 1,
        totalPages: 1,
      };
      const ACTION_DOC_ANCHORS = [
        {
          path: 'docs/OPS_ACTION_GRAPH_AND_SUPPLEMENTS_GUIDE.md',
          name: '运营总手册',
          desc: '整理动作分析与场景搭建后台的核心操作顺序、功能用途和运营主链路。'
        },
        {
          path: 'docs/OPS_ACTION_GRAPH_AND_SUPPLEMENTS_QA.md',
          name: '运营问答手册',
          desc: '记录真实运营疑问、系统解释和操作指引，适合后台复测和培训使用。'
        },
        {
          path: 'docs/GRAPH_REASONING.md',
          name: '图谱推理说明',
          desc: '说明图谱推荐、命中、覆盖和补充单之间的推理链路，方便排查动作推荐逻辑。'
        },
        {
          path: 'docs/SEMANTIC_MATCH.md',
          name: '语义匹配说明',
          desc: '解释动作词、语义扩展词、音效词与素材候选之间的匹配逻辑。'
        },
        {
          path: 'docs/NEO4J_SETUP.md',
          name: 'Neo4j 使用说明',
          desc: '用于查看动作图谱与 Neo4j 同步、状态检查、浏览查询等相关说明。'
        },
        {
          path: 'docs/API.md',
          name: '接口说明',
          desc: '汇总项目接口，用于对照动作分析后台涉及的查询、补充单和图谱维护接口。'
        }
      ];

      function base() { return document.getElementById('apiBase').value.replace(/\/$/, ''); }
      function h(extra = {}) {
        const x = { ...(extra || {}) };
        if (token) x.Authorization = `Bearer ${token}`;
        return x;
      }
      function setState() {
        document.getElementById('loginState').textContent = loginPhone ? `${loginPhone} 已登录` : '未登录';
      }
      async function read(res) {
        const t = await res.text();
        try { return JSON.parse(t); } catch { return t; }
      }
      function show(id, data) {
        document.getElementById(id).textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
      }

      const USERS_PAGE_SIZE = 6;
      let usersPageState = {
        currentPage: 1,
        totalPages: 1,
        items: [],
        ymd: '',
      };
      let leaderboardAdminState = {
        domain: 'action',
        windowKey: '10d',
        data: null,
      };
      const OPS_TIMELINE_PAGE_SIZE = 4;
      let opsTimelineState = {
        currentPage: 1,
        totalPages: 1,
        items: [],
      };
      let actionFallbackMonitorState = {
        days: 30,
        items: [],
        rules: [],
      };
      let fallbackAlertState = {
        pendingCount: 0,
        items: [],
        latestAt: '',
      };
      let fallbackAlertTimer = null;
      let fallbackRiskTermState = [];
      let fallbackRiskTermPagerState = {
        page: 1,
        pageSize: 8,
      };
      let fallbackRuleState = {
        items: [],
        query: '',
        page: 1,
        pageSize: 8,
      };
      const GENRE_OPTIONS = ['玄幻', '言情', '悬疑', '科幻'];
      const DEFAULT_SINGLE_CHAR_RISK_TERMS = new Set(['走', '看', '听', '说', '道', '向', '来', '去', '上', '下', '进', '出', '拿', '放', '推', '拉', '打', '撞', '叫', '喊', '望']);

      function formatReadableTime(value) {
        const raw = String(value || '').trim();
        if (!raw) return '-';
        let normalized = raw.replace(/\.\d+$/, '');
        if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(normalized)) normalized = normalized.replace(' ', 'T');
        const hasTimezone = /(?:Z|[+\-]\d{2}:\d{2})$/.test(normalized);
        const d = new Date(hasTimezone ? normalized : `${normalized}Z`);
        if (Number.isNaN(d.getTime())) return raw.replace('T', ' ').replace(/\.\d+$/, '');
        const parts = new Intl.DateTimeFormat('zh-CN', {
          timeZone: 'Asia/Shanghai',
          hour12: false,
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        }).formatToParts(d);
        const map = Object.fromEntries(parts.map(p => [p.type, p.value]));
        const yyyy = map.year || '0000';
        const mm = map.month || '00';
        const dd = map.day || '00';
        const hh = map.hour || '00';
        const mi = map.minute || '00';
        const ss = map.second || '00';
        return `${yyyy}-${mm}-${dd} ${hh}:${mi}:${ss}`;
      }

      function actionDisplayName(action) {
        const key = String(action || '').trim();
        return ({
          'create_project': '创建项目',
          'audio_analysis': '音乐分析',
          'text_analysis': '文本分析',
          'action_verb_analysis': '动作提取',
          'action_sfx_graph': '动作图谱推荐',
          'action_supplement_sheet': '动作补充单',
          'scene_building_analysis': '场景搭建分析',
          'scene_sfx_graph': '场景音效推荐',
          'scene_supplement_generation': '场景补充单',
          'text_narration_analysis': '文本演绎分析',
          'fusion_execution': '生成后期执行单',
          'action_sfx_asset_download': '下载单个音效',
          'scene_sfx_asset_download': '下载场景音效',
          'export_download': '导出下载',
        })[key] || key || '未标注动作';
      }

      function tierDefaultQuota(tierCode) {
        return String(tierCode || '') === '22'
          ? { text: 10000, sfx: 200 }
          : { text: 5000, sfx: 100 };
      }

      function applyTierQuotaPreset() {
        const tier = (document.getElementById('uTier').value || '33').trim();
        const preset = tierDefaultQuota(tier);
        document.getElementById('uTextCharLimit').value = String(preset.text);
        document.getElementById('uSfxDownloadLimit').value = String(preset.sfx);
      }

      function renderUsersTable(data) {
        const box = document.getElementById('usersTableOut');
        const summary = document.getElementById('usersSummaryOut');
        const pager = document.getElementById('usersPaginationOut');
        if (!box || !summary || !pager) return;
        const items = Array.isArray(data && data.items) ? data.items : [];
        const ymd = String((data && data.ymd) || '').trim() || '今日';
        if (!items.length) {
          summary.textContent = `${ymd} 暂无用户数据。`;
          pager.innerHTML = '';
          box.innerHTML = '<div class="muted">当前没有可展示的用户额度数据。</div>';
          return;
        }
        usersPageState.items = items;
        usersPageState.ymd = ymd;
        usersPageState.totalPages = Math.max(1, Math.ceil(items.length / USERS_PAGE_SIZE));
        usersPageState.currentPage = Math.min(usersPageState.currentPage, usersPageState.totalPages);
        const totalTextUsed = items.reduce((sum, item) => sum + Number((((item || {}).quota || {}).text_chars_used) || 0), 0);
        const totalSfxUsed = items.reduce((sum, item) => sum + Number((((item || {}).quota || {}).sfx_download_used) || 0), 0);
        summary.textContent = `${ymd} 共 ${items.length} 个用户，文本已用 ${totalTextUsed} 字，音效下载已用 ${totalSfxUsed} 次。下方按每页 6 个用户分页展示。`;
        renderUsersPage();
      }

      function changeUsersPage(delta) {
        const next = Math.max(1, Math.min(usersPageState.totalPages, usersPageState.currentPage + delta));
        if (next === usersPageState.currentPage) return;
        usersPageState.currentPage = next;
        renderUsersPage();
      }

      function renderUsersPage() {
        const box = document.getElementById('usersTableOut');
        const pager = document.getElementById('usersPaginationOut');
        if (!box || !pager) return;
        const items = Array.isArray(usersPageState.items) ? usersPageState.items : [];
        const start = (usersPageState.currentPage - 1) * USERS_PAGE_SIZE;
        const pageItems = items.slice(start, start + USERS_PAGE_SIZE);
        const pageStart = items.length ? start + 1 : 0;
        const pageEnd = items.length ? start + pageItems.length : 0;
        pager.innerHTML = `
          <div class="cell" style="flex:0 0 140px;"><button ${usersPageState.currentPage <= 1 ? 'disabled' : ''} onclick="changeUsersPage(-1)">上一页</button></div>
          <div class="cell" style="flex:1 1 260px; align-self:center; color:#9fb5d8;">第 ${usersPageState.currentPage} / ${usersPageState.totalPages} 页，当前显示 ${pageStart}-${pageEnd} / ${items.length}</div>
          <div class="cell" style="flex:0 0 140px;"><button ${usersPageState.currentPage >= usersPageState.totalPages ? 'disabled' : ''} onclick="changeUsersPage(1)">下一页</button></div>
        `;
        box.innerHTML = `
          <div style="overflow:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:12px;">
              <thead>
                <tr>
                  <th style="text-align:left; padding:8px; border-bottom:1px solid #2d4f80;">识别信息</th>
                  <th style="text-align:left; padding:8px; border-bottom:1px solid #2d4f80;">前台等级</th>
                  <th style="text-align:left; padding:8px; border-bottom:1px solid #2d4f80;">角色</th>
                  <th style="text-align:left; padding:8px; border-bottom:1px solid #2d4f80;">邀请码</th>
                  <th style="text-align:left; padding:8px; border-bottom:1px solid #2d4f80;">文本额度</th>
                  <th style="text-align:left; padding:8px; border-bottom:1px solid #2d4f80;">下载额度</th>
                  <th style="text-align:left; padding:8px; border-bottom:1px solid #2d4f80;">推荐关系</th>
                </tr>
              </thead>
              <tbody>
                ${pageItems.map((item) => {
                  const quota = item.quota || {};
                  const inviteText = item.invite_activated
                    ? `已激活${item.invite_code_used ? ` / ${escHtml(item.invite_code_used)}` : ''}`
                    : '未激活';
                  return `
                    <tr>
                      <td style="padding:8px; border-bottom:1px solid #1f3557;">手机号:${escHtml(item.phone || '-')}<br/>UID:${escHtml(item.uid || '-')}</td>
                      <td style="padding:8px; border-bottom:1px solid #1f3557;">级别:${escHtml(item.user_tier_code || '')}<br/>标记:${item.is_authorized ? '授权不限额' : '按日计数'}</td>
                      <td style="padding:8px; border-bottom:1px solid #1f3557;">后台角色:${escHtml(item.ops_role_code || '')}<br/>授权:${item.is_authorized ? '不限次' : '受限'}</td>
                      <td style="padding:8px; border-bottom:1px solid #1f3557;">${inviteText}</td>
                      <td style="padding:8px; border-bottom:1px solid #1f3557;">已用 ${escHtml(quota.text_chars_used || 0)} / 限额 ${escHtml(quota.text_chars_limit || 0)}<br/>剩余 ${escHtml(quota.text_chars_remaining || 0)}</td>
                      <td style="padding:8px; border-bottom:1px solid #1f3557;">已用 ${escHtml(quota.sfx_download_used || 0)} / 限额 ${escHtml(quota.sfx_download_limit || 0)}<br/>剩余 ${escHtml(quota.sfx_download_remaining || 0)}</td>
                      <td style="padding:8px; border-bottom:1px solid #1f3557;">来源:${escHtml(item.referred_by_uid || item.referred_by_phone || '-')}<br/>拉新:${escHtml(item.referral_user_count || 0)}</td>
                    </tr>
                  `;
                }).join('')}
              </tbody>
            </table>
          </div>
        `;
      }
      function escHtml(s) {
        return String(s ?? '')
          .replaceAll('&', '&amp;')
          .replaceAll('<', '&lt;')
          .replaceAll('>', '&gt;')
          .replaceAll('"', '&quot;')
          .replaceAll("'", '&#39;');
      }
      function escAttr(s) {
        return escHtml(s);
      }
      function clipText(s, max = 36) {
        const value = String(s || '').trim();
        if (!value) return '';
        return value.length > max ? `${value.slice(0, max)}...` : value;
      }
      function renderPills(list) {
        const arr = Array.isArray(list) ? list.filter(Boolean) : [];
        if (!arr.length) return '<span class="muted">无</span>';
        return arr.map(x => `<span class="pill">${escHtml(x)}</span>`).join('');
      }
      function renderDocAnchors(outId, docs) {
        const box = document.getElementById(outId);
        if (!box) return;
        const rows = Array.isArray(docs) ? docs : [];
        box.innerHTML = rows.map(doc => `
          <div class="doc-anchor-item">
            <div class="doc-anchor-path">${escHtml(`/Users/demo/Documents/New project/musicForArt/${doc.path}`)}</div>
            <div class="doc-anchor-name">${escHtml(doc.name || doc.path)}</div>
            <div class="doc-anchor-desc">${escHtml(doc.desc || '')}</div>
          </div>
        `).join('') || '<div class="muted">当前没有可展示的本地文档锚点。</div>';
      }
      function sourceLabel(source) {
        return ({
          'common': '通用元数据',
          'genre': '赛道特化',
          'common+genre': '通用元数据 + 赛道特化',
          'fallback': '保底生成',
        })[String(source || '').trim()] || '未标注';
      }
      function reviewStatusLabel(status) {
        return ({
          'pending': '待审核',
          'approved': '已通过',
          'rejected': '已驳回',
          'active': '发布中',
          'draft': '草稿',
          'closed': '关闭',
        })[String(status || '').trim()] || String(status || '-');
      }
      async function loadCreatorShowcases() {
        const status = (document.getElementById('creatorShowcaseStatus')?.value || '').trim();
        const q = (document.getElementById('creatorShowcaseQuery')?.value || '').trim();
        const qs = new URLSearchParams();
        if (status) qs.set('status', status);
        if (q) qs.set('q', q);
        const res = await fetch(`${base()}/admin/creator-showcases?${qs.toString()}`, { headers: h() });
        const data = await read(res);
        const box = document.getElementById('creatorShowcasesView');
        if (!box) return;
        if (!res.ok) {
          box.innerHTML = `<div class="muted">${escHtml(data?.detail || '加载失败')}</div>`;
          return;
        }
        const items = Array.isArray(data.items) ? data.items : [];
        box.innerHTML = items.length ? items.map((item) => `
          <div class="panel" style="margin-bottom:8px;">
            <div style="font-weight:700;">${escHtml(item.title || '-')}</div>
            <div class="muted-sm" style="margin-top:4px;">${escHtml(item.user_phone || '')} ｜ ${escHtml(item.genre || '')} ｜ ${escHtml(reviewStatusLabel(item.status))}</div>
            <div style="margin-top:6px; line-height:1.7;">${escHtml(item.summary || '')}</div>
            <div style="margin-top:8px; display:flex; gap:8px; flex-wrap:wrap;">
              <button class="ghost-btn" onclick="reviewCreatorShowcase(${Number(item.id || 0)}, 'approved')">通过</button>
              <button class="ghost-btn" onclick="reviewCreatorShowcase(${Number(item.id || 0)}, 'rejected')">驳回</button>
            </div>
          </div>
        `).join('') : '<div class="muted">当前没有作品数据。</div>';
      }
      async function reviewCreatorShowcase(id, decision) {
        const note = window.prompt(decision === 'approved' ? '审核备注（可选）' : '驳回原因（可选）', '') || '';
        const res = await fetch(`${base()}/admin/creator-showcases/review`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ id, decision, note }),
        });
        const data = await read(res);
        show('eventsOut', data);
        await loadCreatorShowcases();
      }
      async function loadCopyrightAdsAdmin() {
        const res = await fetch(`${base()}/admin/copyright-ads`, { headers: h() });
        const data = await read(res);
        const box = document.getElementById('copyrightAdsAdminView');
        if (!box) return;
        if (!res.ok) {
          box.innerHTML = `<div class="muted">${escHtml(data?.detail || '加载失败')}</div>`;
          return;
        }
        const items = Array.isArray(data.items) ? data.items : [];
        box.innerHTML = items.length ? items.map((item) => `
          <div class="panel" style="margin-bottom:8px;">
            <div style="font-weight:700;">${escHtml(item.title || '-')}</div>
            <div class="muted-sm" style="margin-top:4px;">${escHtml(item.user_phone || '')} ｜ ${escHtml(item.genre || '')} ｜ ${escHtml(reviewStatusLabel(item.status))} ｜ 保证金 ${escHtml(item.deposit_amount || 0)} 元</div>
            <div style="margin-top:6px; line-height:1.7;">${escHtml(item.description || '')}</div>
            <div style="margin-top:8px; display:flex; gap:8px; flex-wrap:wrap;">
              <button class="ghost-btn" onclick="reviewBooklist(${Number(item.id || 0)}, 'active')">发布</button>
              <button class="ghost-btn" onclick="reviewBooklist(${Number(item.id || 0)}, 'rejected')">驳回</button>
              <button class="ghost-btn" onclick="reviewBooklist(${Number(item.id || 0)}, 'closed')">关闭</button>
            </div>
          </div>
        `).join('') : '<div class="muted">当前没有书单数据。</div>';
      }
      async function reviewBooklist(id, decision) {
        const res = await fetch(`${base()}/admin/copyright-ads/review`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ id, decision }),
        });
        const data = await read(res);
        show('assetFeedbackOut', data);
        await loadCopyrightAdsAdmin();
      }
      async function saveCopyrightAd() {
        const payload = {
          title: (document.getElementById('copyrightTitle').value || '').trim(),
          genre: (document.getElementById('copyrightGenre').value || '').trim(),
          description: (document.getElementById('copyrightDescription').value || '').trim(),
          budget_text: (document.getElementById('copyrightBudget').value || '').trim(),
          deposit_amount: Number(document.getElementById('copyrightDeposit').value || 0),
          contact_note: (document.getElementById('copyrightContact').value || '').trim(),
          status: (document.getElementById('copyrightStatus').value || 'active').trim(),
        };
        const res = await fetch(`${base()}/admin/copyright-ads`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify(payload),
        });
        const data = await read(res);
        show('assetFeedbackOut', data);
        await loadCopyrightAdsAdmin();
      }
      async function loadRecruitmentNeedsAdmin() {
        const res = await fetch(`${base()}/admin/recruitment-needs`, { headers: h() });
        const data = await read(res);
        const box = document.getElementById('recruitmentNeedsAdminView');
        if (!box) return;
        if (!res.ok) {
          box.innerHTML = `<div class="muted">${escHtml(data?.detail || '加载失败')}</div>`;
          return;
        }
        const items = Array.isArray(data.items) ? data.items : [];
        box.innerHTML = items.length ? items.map((item) => `
          <div class="panel" style="margin-bottom:8px;">
            <div style="font-weight:700;">${escHtml(item.title || '-')}</div>
            <div class="muted-sm" style="margin-top:4px;">${escHtml(item.genre || '')} ｜ ${escHtml(reviewStatusLabel(item.status))} ｜ 截止 ${escHtml(item.deadline_text || '长期有效')}</div>
            <div style="margin-top:6px; line-height:1.7;">${escHtml(item.description || '')}</div>
          </div>
        `).join('') : '<div class="muted">当前没有招聘需求。</div>';
      }
      async function saveRecruitmentNeed() {
        const payload = {
          title: (document.getElementById('recruitmentTitle').value || '').trim(),
          genre: (document.getElementById('recruitmentGenre').value || '').trim(),
          description: (document.getElementById('recruitmentDescription').value || '').trim(),
          budget_text: (document.getElementById('recruitmentBudget').value || '').trim(),
          deadline_text: (document.getElementById('recruitmentDeadline').value || '').trim(),
          contact_note: (document.getElementById('recruitmentContact').value || '').trim(),
          status: (document.getElementById('recruitmentStatus').value || 'active').trim(),
        };
        const res = await fetch(`${base()}/admin/recruitment-needs`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify(payload),
        });
        const data = await read(res);
        show('bundleOut', data);
        await loadRecruitmentNeedsAdmin();
      }
      async function loadRechargeOrders() {
        const status = (document.getElementById('rechargeOrderStatus')?.value || '').trim();
        const q = (document.getElementById('rechargeOrderQuery')?.value || '').trim();
        const qs = new URLSearchParams();
        if (status) qs.set('status', status);
        if (q) qs.set('q', q);
        const res = await fetch(`${base()}/admin/recharge-orders?${qs.toString()}`, { headers: h() });
        const data = await read(res);
        const box = document.getElementById('rechargeOrdersView');
        if (!box) return;
        if (!res.ok) {
          box.innerHTML = `<div class="muted">${escHtml(data?.detail || '加载失败')}</div>`;
          return;
        }
        const items = Array.isArray(data.items) ? data.items : [];
        box.innerHTML = items.length ? items.map((item) => `
          <div class="panel" style="margin-bottom:8px;">
            <div style="font-weight:700;">${escHtml(item.user_phone || '-')} ｜ ${escHtml(item.package_name || '-')}</div>
            <div class="muted-sm" style="margin-top:4px;">${escHtml(item.order_type_label || item.order_type || '')} ｜ 数量 ${escHtml(item.units || 0)} ｜ 状态 ${escHtml(reviewStatusLabel(item.status))}</div>
            <div class="muted-sm" style="margin-top:4px;">应付 ${escHtml(item.payable_amount || 0)} 元 ｜ 保证金抵扣 ${escHtml(item.deposit_offset || 0)} 元</div>
            <div style="margin-top:8px; display:flex; gap:8px; flex-wrap:wrap;">
              <button class="ghost-btn" onclick="reviewRechargeOrder(${Number(item.id || 0)}, 'approved')">审核通过</button>
              <button class="ghost-btn" onclick="reviewRechargeOrder(${Number(item.id || 0)}, 'rejected')">驳回</button>
            </div>
          </div>
        `).join('') : '<div class="muted">当前没有充值订单。</div>';
      }
      async function reviewRechargeOrder(id, decision) {
        const note = window.prompt(decision === 'approved' ? '审核备注（可选）' : '驳回原因（可选）', '') || '';
        const res = await fetch(`${base()}/admin/recharge-orders/review`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ id, decision, note }),
        });
        const data = await read(res);
        show('usersOut', data);
        await loadRechargeOrders();
      }
      function isInheritanceBlocked(genre, verbHead) {
        const items = inheritanceDashboardState?.blocked_pool?.items || [];
        return items.some(item => String(item.genre || '').trim() === String(genre || '').trim() && String(item.verb_head || '').trim() === String(verbHead || '').trim());
      }
      function changeInheritanceBlockedPage(delta) {
        const next = Math.max(1, Math.min(inheritanceBlockedPageState.totalPages, inheritanceBlockedPageState.currentPage + delta));
        if (next === inheritanceBlockedPageState.currentPage) return;
        inheritanceBlockedPageState.currentPage = next;
        renderInheritanceDashboard();
      }
      function changeInheritanceReviewPage(delta) {
        const next = Math.max(1, Math.min(inheritanceReviewPageState.totalPages, inheritanceReviewPageState.currentPage + delta));
        if (next === inheritanceReviewPageState.currentPage) return;
        inheritanceReviewPageState.currentPage = next;
        renderInheritanceDashboard();
      }
      function renderInheritanceDashboard() {
        const box = document.getElementById('graphInheritanceDashboard');
        if (!box) return;
        const selectedGenreKey = String(graphMaintenanceCatalogState?.selectedGenreKey || '').trim();
        const showAll = !selectedGenreKey || selectedGenreKey === 'common';
        const allBlockedItems = Array.isArray(inheritanceDashboardState?.blocked_pool?.items) ? inheritanceDashboardState.blocked_pool.items : [];
        const allReviewItems = Array.isArray(inheritanceDashboardState?.review_pool?.items) ? inheritanceDashboardState.review_pool.items : [];
        const blockedItems = showAll
          ? allBlockedItems
          : allBlockedItems.filter(item => String(item?.genre || '').trim() === selectedGenreKey);
        const reviewItems = showAll
          ? allReviewItems
          : allReviewItems.filter(item => String(item?.genre || '').trim() === selectedGenreKey);
        inheritanceBlockedPageState.totalPages = Math.max(1, Math.ceil(blockedItems.length / INHERITANCE_BLOCKED_PAGE_SIZE));
        inheritanceBlockedPageState.currentPage = Math.min(inheritanceBlockedPageState.currentPage, inheritanceBlockedPageState.totalPages);
        const blockedStart = (inheritanceBlockedPageState.currentPage - 1) * INHERITANCE_BLOCKED_PAGE_SIZE;
        const blockedPageItems = blockedItems.slice(blockedStart, blockedStart + INHERITANCE_BLOCKED_PAGE_SIZE);
        const blockedPageStart = blockedItems.length ? blockedStart + 1 : 0;
        const blockedPageEnd = blockedItems.length ? blockedStart + blockedPageItems.length : 0;
        inheritanceReviewPageState.totalPages = Math.max(1, Math.ceil(reviewItems.length / INHERITANCE_BLOCKED_PAGE_SIZE));
        inheritanceReviewPageState.currentPage = Math.min(inheritanceReviewPageState.currentPage, inheritanceReviewPageState.totalPages);
        const reviewStart = (inheritanceReviewPageState.currentPage - 1) * INHERITANCE_BLOCKED_PAGE_SIZE;
        const reviewPageItems = reviewItems.slice(reviewStart, reviewStart + INHERITANCE_BLOCKED_PAGE_SIZE);
        const reviewPageStart = reviewItems.length ? reviewStart + 1 : 0;
        const reviewPageEnd = reviewItems.length ? reviewStart + reviewPageItems.length : 0;
        const blockedPagerHtml = blockedItems.length > INHERITANCE_BLOCKED_PAGE_SIZE ? `
          <div class="ops-pager" style="margin-top:8px;">
            <button style="width:auto; min-width:120px;" ${inheritanceBlockedPageState.currentPage <= 1 ? 'disabled' : ''} onclick="changeInheritanceBlockedPage(-1)">上一页</button>
            <div class="ops-pager-info">第 ${inheritanceBlockedPageState.currentPage} / ${inheritanceBlockedPageState.totalPages} 页，当前显示 ${blockedPageStart}-${blockedPageEnd} / ${blockedItems.length}</div>
            <button style="width:auto; min-width:120px;" ${inheritanceBlockedPageState.currentPage >= inheritanceBlockedPageState.totalPages ? 'disabled' : ''} onclick="changeInheritanceBlockedPage(1)">下一页</button>
          </div>
        ` : '';
        const reviewPagerHtml = reviewItems.length > INHERITANCE_BLOCKED_PAGE_SIZE ? `
          <div class="ops-pager" style="margin-top:8px;">
            <button style="width:auto; min-width:120px;" ${inheritanceReviewPageState.currentPage <= 1 ? 'disabled' : ''} onclick="changeInheritanceReviewPage(-1)">上一页</button>
            <div class="ops-pager-info">第 ${inheritanceReviewPageState.currentPage} / ${inheritanceReviewPageState.totalPages} 页，当前显示 ${reviewPageStart}-${reviewPageEnd} / ${reviewItems.length}</div>
            <button style="width:auto; min-width:120px;" ${inheritanceReviewPageState.currentPage >= inheritanceReviewPageState.totalPages ? 'disabled' : ''} onclick="changeInheritanceReviewPage(1)">下一页</button>
          </div>
        ` : '';
        const blockedHtml = blockedPageItems.length ? blockedPageItems.map(item => `
          <div class="term-block" style="margin-top:8px;">
            <div class="term-title">${escHtml(item.genre || '')} ← ${escHtml(item.verb_head || '')}</div>
            <div class="muted-sm note-prefix">${item.genre_exists ? '当前赛道已删除这个通用动词引用，但该赛道本身仍有真实的赛道特化节点，所以它还会继续显示。' : '当前赛道已删除这个通用动词。恢复后，它会再次出现在当前赛道的可用节点和推荐结果中。'}</div>
            <div class="row" style="margin-top:6px;">
              <div class="cell"><button onclick="openGraphMaintenance('${escHtml(item.node_key || item.verb_head || '')}')">打开通用元数据节点</button></div>
              <div class="cell"><button onclick="toggleGraphInheritance('${escHtml(item.genre || '')}','${escHtml(item.verb_head || '')}','restore')">恢复到当前赛道</button></div>
            </div>
          </div>
        `).join('') : '<div class="muted-sm note-prefix">当前没有被删除的通用动词项。</div>';
        const reviewHtml = reviewPageItems.length ? reviewPageItems.map(item => `
          <div class="term-block" style="margin-top:6px; padding:10px 12px;">
            <div style="display:flex; justify-content:space-between; gap:12px; align-items:flex-start;">
              <div class="term-title" style="margin-bottom:0;">${escHtml(item.genre || '')} ← ${escHtml(item.verb_head || '')}</div>
              <div class="mini-action-row" style="margin-top:0; flex:0 0 auto;">
                <button class="ghost-btn" style="padding:4px 10px;" onclick="openGraphMaintenance('${escHtml(item.verb_head || '')}')">打开通用元数据</button>
                <button class="ghost-btn" style="padding:4px 10px;" onclick="toggleGraphInheritance('${escHtml(item.genre || '')}','${escHtml(item.verb_head || '')}','restore')">恢复到当前赛道</button>
              </div>
            </div>
            <div class="status-bar" style="margin-top:4px; gap:6px;">
              <span class="status-chip">近${escHtml(inheritanceDashboardState?.review_pool?.days || 30)}天命中：${escHtml(item.hit_count || 0)}</span>
              <span class="status-chip">${escHtml(item.is_currently_blocked ? '当前仍在删除池中' : '当前已恢复' )}</span>
            </div>
            <div class="muted-sm note-prefix" style="margin-top:4px;">示例片段：${escHtml(clipText(item.sample_excerpt || '暂无示例片段', 46))}</div>
          </div>
        `).join('') : '<div class="muted-sm note-prefix">当前没有“删除后仍高频命中”的复核项。</div>';
        box.innerHTML = `
          <div class="section-box">
            <h5>赛道删除池</h5>
            <div class="muted-sm note-prefix" style="margin-bottom:8px;">当前展示范围：${showAll ? '全部赛道' : escHtml(selectedGenreKey)}</div>
            ${blockedHtml}
            ${blockedPagerHtml}
          </div>
          <div class="section-box" style="margin-top:10px;">
            <h5>删除后命中复核</h5>
            <div class="muted-sm note-prefix" style="margin-bottom:8px;">当前展示范围：${showAll ? '全部赛道' : escHtml(selectedGenreKey)}</div>
            ${reviewHtml}
            ${reviewPagerHtml}
          </div>
        `;
      }
      function currentFallbackMonitorDays() {
        return 30;
      }
      function fallbackScopeDefault(item) {
        return String(item?.genre || '').trim() ? 'genre' : 'common';
      }
      function renderGenreSelectOptions(selectedValue = '') {
        const selected = String(selectedValue || '').trim();
        return [
          '<option value="">请选择赛道</option>',
          ...GENRE_OPTIONS.map(genre => `<option value="${escAttr(genre)}" ${genre === selected ? 'selected' : ''}>${escHtml(genre)}</option>`),
        ].join('');
      }
      function classifyFallbackRisk(term, formalHead = '') {
        const value = String(term || '').trim();
        const head = String(formalHead || '').trim();
        const matchedRule = (Array.isArray(fallbackRiskTermState) ? fallbackRiskTermState : []).find(item => {
          return Boolean(item?.enabled) && String(item?.term || '').trim() === value;
        });
        if (!value) return { level: 'safe', label: '可归并', hint: '当前词可作为普通归并项处理。' };
        if (value === head) return { level: 'safe', label: '标准展示词', hint: '这是当前准备保留的标准展示词。' };
        if (matchedRule) {
          return {
            level: String(matchedRule.risk_level || 'warn').trim() === 'danger' ? 'danger' : 'warn',
            label: String(matchedRule.risk_level || 'warn').trim() === 'danger' ? '高风险单字' : '泛词谨慎',
            hint: String(matchedRule.note || '').trim() || '这个词已在风险词表中，请谨慎归并。',
          };
        }
        if (value.length === 1 && DEFAULT_SINGLE_CHAR_RISK_TERMS.has(value)) {
          return { level: 'danger', label: '高风险单字', hint: '这是单字泛词，直接归并后可能误伤大量其他动作，建议谨慎处理或先忽略。' };
        }
        if (value.length === 1) {
          return { level: 'warn', label: '单字谨慎', hint: '单字词语义太宽，建议谨慎归并。' };
        }
        return { level: 'safe', label: '建议归并', hint: '当前词更像误拆出来的碎词，可优先归并。' };
      }
      function hasFallbackRiskRule(term) {
        const value = String(term || '').trim();
        return (Array.isArray(fallbackRiskTermState) ? fallbackRiskTermState : []).some(item => {
          return Boolean(item?.enabled) && String(item?.term || '').trim() === value;
        });
      }
      function getFallbackRelatedRuleCount(term) {
        const value = String(term || '').trim();
        if (!value) return 0;
        return (Array.isArray(fallbackRuleState.items) ? fallbackRuleState.items : []).filter(item => {
          return String(item?.alias_term || '').trim() === value || String(item?.formal_head || '').trim() === value;
        }).length;
      }
      function focusFallbackRulesForTerm(term) {
        const value = String(term || '').trim();
        if (!value) return;
        fallbackRuleState.query = value;
        fallbackRuleState.page = 1;
        renderActionFallbackRules(fallbackRuleState.items);
        const input = document.getElementById('fallbackRuleSearch');
        if (input) input.value = value;
        const box = document.getElementById('fallbackMonitorRules');
        if (box) box.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
      function renderFallbackRiskPills(terms, formalHead = '') {
        const arr = Array.isArray(terms) ? terms.filter(Boolean) : [];
        if (!arr.length) return '<span class="muted">当前没有待归并碎词。</span>';
        return arr.map(term => {
          const risk = classifyFallbackRisk(term, formalHead);
          const tone = risk.level === 'danger' ? 'danger' : (risk.level === 'warn' ? 'warn' : 'safe');
          const canAddRisk = String(term || '').trim() && String(term || '').trim() !== String(formalHead || '').trim() && !hasFallbackRiskRule(term);
          return `<span class="fallback-rule-chip ${tone}" title="${escAttr(risk.hint)}">${escHtml(term)}<span class="pill">${escHtml(risk.label)}</span>${canAddRisk ? `<button class="ghost-btn" style="padding:2px 8px;" onclick="addFallbackRiskTermFromMonitor('${escAttr(term)}','${escAttr(risk.level)}')">加入高风险词表</button>` : ''}</span>`;
        }).join('');
      }
      function renderFallbackAliasChecklist(aliasTerms, formalHead = '', idx = 0) {
        const arr = Array.isArray(aliasTerms) ? aliasTerms.filter(Boolean) : [];
        const formalBlock = formalHead ? `<div class="fallback-rule-chip safe" style="display:flex; align-items:center; gap:8px; margin-bottom:8px;">
          <span>${escHtml(formalHead)}</span>
          <span class="pill">标准展示词</span>
        </div>` : '';
        if (!arr.length) return `${formalBlock}<div class="muted-sm note-prefix">当前没有可收敛的碎词候选。</div>`;
        return `${formalBlock}<div class="fallback-alias-list">${arr.map(term => {
          const risk = classifyFallbackRisk(term, formalHead);
          const tone = risk.level === 'danger' ? 'danger' : (risk.level === 'warn' ? 'warn' : 'safe');
          const checked = risk.level === 'safe';
          return `<label class="fallback-rule-chip ${tone}" style="display:flex; align-items:center; gap:8px;">
            <input type="checkbox" class="fallback-alias-check" data-idx="${idx}" value="${escAttr(term)}" ${checked ? 'checked' : ''} />
            <span>${escHtml(term)}</span>
            <span class="pill">${escHtml(risk.label)}</span>
          </label>`;
        }).join('')}</div>`;
      }
      function captureFallbackMonitorDraftState() {
        const items = Array.isArray(actionFallbackMonitorState?.items) ? actionFallbackMonitorState.items : [];
        items.forEach((item, idx) => {
          item.__selected_aliases = getSelectedFallbackAliases(idx);
          item.__formal_head = String(document.getElementById(`fallbackFormal_${idx}`)?.value || '').trim();
          item.__scope = String(document.getElementById(`fallbackScope_${idx}`)?.value || fallbackScopeDefault(item)).trim();
          item.__genre = String(document.getElementById(`fallbackGenre_${idx}`)?.value || item?.genre || '').trim();
        });
      }
      function getSelectedFallbackAliases(idx) {
        return Array.from(document.querySelectorAll(`.fallback-alias-check[data-idx="${idx}"]:checked`))
          .map(input => String(input.value || '').trim())
          .filter(Boolean);
      }
      function setFallbackAliasChecks(idx, checked) {
        document.querySelectorAll(`.fallback-alias-check[data-idx="${idx}"]`).forEach(input => {
          input.checked = !!checked;
        });
      }
      function applySuggestedFallbackFormal(idx, suggested) {
        const input = document.getElementById(`fallbackFormal_${idx}`);
        if (!input) return;
        input.value = String(suggested || '').trim();
      }
      function addFallbackAliasManual(idx) {
        const input = document.getElementById(`fallbackAliasManual_${idx}`);
        const value = String(input?.value || '').trim();
        if (!value) return;
        captureFallbackMonitorDraftState();
        const item = (actionFallbackMonitorState?.items || [])[idx];
        if (!item) return;
        const manual = Array.isArray(item.__manual_aliases) ? item.__manual_aliases.slice() : [];
        if (!manual.includes(value)) manual.push(value);
        item.__manual_aliases = manual;
        if (input) input.value = '';
        renderActionFallbackMonitor({
          ...actionFallbackMonitorState,
          pending_items: actionFallbackMonitorState.items,
          rules: actionFallbackMonitorState.rules,
          risk_terms: fallbackRiskTermState,
        });
      }
      function syncFallbackScopeUi(idx) {
        const scope = String(document.getElementById(`fallbackScope_${idx}`)?.value || 'common').trim();
        const genreSelect = document.getElementById(`fallbackGenre_${idx}`);
        const note = document.getElementById(`fallbackGenreNote_${idx}`);
        if (!genreSelect || !note) return;
        const isGenre = scope === 'genre';
        genreSelect.disabled = !isGenre;
        if (!isGenre) genreSelect.value = '';
        note.textContent = '';
      }
      function renderActionFallbackRules(rules) {
        const box = document.getElementById('fallbackMonitorRules');
        if (!box) return;
        fallbackRuleState.items = Array.isArray(rules) ? rules : [];
        const query = String(fallbackRuleState.query || '').trim().toLowerCase();
        const filtered = fallbackRuleState.items.filter(item => {
          if (!query) return true;
          const hay = [
            item?.alias_term,
            item?.formal_head,
            item?.genre,
            item?.scope,
          ].map(v => String(v || '').toLowerCase()).join(' ');
          return hay.includes(query);
        });
        const totalPages = Math.max(1, Math.ceil(filtered.length / fallbackRuleState.pageSize));
        fallbackRuleState.page = Math.min(fallbackRuleState.page, totalPages);
        const start = (fallbackRuleState.page - 1) * fallbackRuleState.pageSize;
        const pageItems = filtered.slice(start, start + fallbackRuleState.pageSize);
        if (!fallbackRuleState.items.length) {
          box.innerHTML = '<div class="section-box"><h5>现有标准展示词收敛规则</h5><div class="muted-sm note-prefix">当前还没有已保存的保底归并规则。</div></div>';
          return;
        }
        box.innerHTML = `
          <div class="section-box">
            <h5>现有标准展示词收敛规则</h5>
            <div class="row" style="margin-top:8px;">
              <div class="cell"><input id="fallbackRuleSearch" placeholder="搜索 alias / 标准展示词 / 赛道" value="${escAttr(fallbackRuleState.query || '')}" oninput="updateFallbackRuleSearch(this.value)" /></div>
            </div>
            <div class="muted-sm note-prefix" style="margin-top:6px;">共 ${filtered.length} 条匹配结果；当前第 ${fallbackRuleState.page} / ${totalPages} 页。</div>
            <div class="fallback-rule-list">
              ${pageItems.map(item => `
                <span class="fallback-rule-chip">
                  ${escHtml(item.alias_term || '')} → ${escHtml(item.formal_head || '')}
                  <span class="pill">${escHtml(String(item.scope || '') === 'genre' ? `赛道层${item.genre ? `·${item.genre}` : ''}` : '通用层')}</span>
                  <button class="ghost-btn" style="padding:2px 8px;" onclick="releaseActionFallbackRule('${escAttr(item.alias_term || '')}','${escAttr(item.scope || 'common')}','${escAttr(item.genre || '')}')">释放</button>
                  <button class="ghost-btn" style="padding:2px 8px;" onclick="releaseActionFallbackRule('${escAttr(item.alias_term || '')}','${escAttr(item.scope || 'common')}','${escAttr(item.genre || '')}', true)">释放并加入高风险词表</button>
                </span>
              `).join('')}
            </div>
            <div class="ops-pager" style="margin-top:10px;">
              <button style="width:auto; min-width:120px;" ${fallbackRuleState.page <= 1 ? 'disabled' : ''} onclick="changeFallbackRulePage(-1)">上一页</button>
              <div class="ops-pager-info">当前显示 ${filtered.length ? `${start + 1}-${Math.min(start + pageItems.length, filtered.length)}` : '0'} / ${filtered.length}</div>
              <button style="width:auto; min-width:120px;" ${fallbackRuleState.page >= totalPages ? 'disabled' : ''} onclick="changeFallbackRulePage(1)">下一页</button>
            </div>
          </div>
        `;
      }
      function updateFallbackRuleSearch(value) {
        fallbackRuleState.query = String(value || '').trim();
        fallbackRuleState.page = 1;
        renderActionFallbackRules(fallbackRuleState.items);
      }
      function changeFallbackRulePage(delta) {
        const query = String(fallbackRuleState.query || '').trim().toLowerCase();
        const filteredCount = (fallbackRuleState.items || []).filter(item => {
          if (!query) return true;
          const hay = [
            item?.alias_term,
            item?.formal_head,
            item?.genre,
            item?.scope,
          ].map(v => String(v || '').toLowerCase()).join(' ');
          return hay.includes(query);
        }).length;
        const totalPages = Math.max(1, Math.ceil(filteredCount / fallbackRuleState.pageSize));
        const next = Math.max(1, Math.min(totalPages, fallbackRuleState.page + delta));
        if (next === fallbackRuleState.page) return;
        fallbackRuleState.page = next;
        renderActionFallbackRules(fallbackRuleState.items);
      }
      function renderActionFallbackRiskTermsAdmin(_items) {
        return;
      }
      function changeFallbackRiskTermPage(delta) {
        return;
      }
      function prefillFallbackRiskTerm(term, riskLevel, note, enabled) {
        const termInput = document.getElementById('fallbackRiskTermInput');
        const levelInput = document.getElementById('fallbackRiskLevelInput');
        const noteInput = document.getElementById('fallbackRiskNoteInput');
        const enabledInput = document.getElementById('fallbackRiskEnabledInput');
        if (termInput) termInput.value = String(term || '').trim();
        if (levelInput) levelInput.value = String(riskLevel || 'warn').trim() || 'warn';
        if (noteInput) noteInput.value = String(note || '').trim();
        if (enabledInput) enabledInput.value = String(enabled || '1').trim() || '1';
      }
      async function saveFallbackRiskTerm() {
        const term = String(document.getElementById('fallbackRiskTermInput')?.value || '').trim();
        const riskLevel = String(document.getElementById('fallbackRiskLevelInput')?.value || 'warn').trim();
        const note = String(document.getElementById('fallbackRiskNoteInput')?.value || '').trim();
        const enabled = (document.getElementById('fallbackRiskEnabledInput')?.value || '1') === '1';
        if (!term) return show('fallbackMonitorOut', '请先填写风险词');
        const res = await fetch(`${base()}/action-graph/fallback-risk-terms`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ term, risk_level: riskLevel, note, enabled }),
        });
        const data = await read(res);
        show('fallbackMonitorOut', data);
        if (!res.ok) return;
        fallbackRiskTermState = Array.isArray(data?.items) ? data.items : [];
        renderActionFallbackRiskTermsAdmin(fallbackRiskTermState);
        await loadActionFallbackMonitor();
      }
      async function deleteFallbackRiskTerm(term) {
        const res = await fetch(`${base()}/action-graph/fallback-risk-terms/delete`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ term: String(term || '').trim() }),
        });
        const data = await read(res);
        show('fallbackMonitorOut', data);
        if (!res.ok) return;
        fallbackRiskTermState = Array.isArray(data?.items) ? data.items : [];
        renderActionFallbackRiskTermsAdmin(fallbackRiskTermState);
        await loadActionFallbackMonitor();
      }
      async function addFallbackRiskTermFromMonitor(term, level = 'warn') {
        const value = String(term || '').trim();
        if (!value) return;
        const riskLevel = String(level || 'warn').trim() === 'danger' ? 'danger' : 'warn';
        const note = riskLevel === 'danger'
          ? '来自保底生成监控卡的一键加入，高风险单字或高风险泛词。'
          : '来自保底生成监控卡的一键加入，建议谨慎归并。';
        const res = await fetch(`${base()}/action-graph/fallback-risk-terms`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ term: value, risk_level: riskLevel, note, enabled: true }),
        });
        const data = await read(res);
        show('fallbackMonitorOut', data);
        if (!res.ok) return;
        fallbackRiskTermState = Array.isArray(data?.items) ? data.items : [];
        renderActionFallbackRiskTermsAdmin(fallbackRiskTermState);
        renderActionFallbackMonitor({
          ...actionFallbackMonitorState,
          pending_items: actionFallbackMonitorState.items,
          rules: actionFallbackMonitorState.rules,
          risk_terms: fallbackRiskTermState,
        });
      }
      function renderActionFallbackMonitor(data) {
        const summary = document.getElementById('fallbackMonitorSummary');
        const box = document.getElementById('fallbackMonitorView');
        if (!summary || !box) return;
        const items = Array.isArray(data?.pending_items)
          ? data.pending_items
          : (Array.isArray(data?.items) ? data.items : []);
        const rules = Array.isArray(data?.rules) ? data.rules : [];
        actionFallbackMonitorState = {
          days: Number(data?.days || currentFallbackMonitorDays() || 30),
          items,
          rules,
        };
        fallbackRiskTermState = Array.isArray(data?.risk_terms) ? data.risk_terms : [];
        renderActionFallbackRules(rules);
        renderActionFallbackRiskTermsAdmin(fallbackRiskTermState);
        renderGraphPriorityTodo();
        if (!items.length) {
          summary.textContent = '当前没有新的待确认保底词映射。';
          box.innerHTML = '<div class="muted">当前没有需要人工收敛的高频保底词。</div>';
          return;
        }
        const totalHits = items.reduce((sum, item) => sum + Number(item?.hit_count || 0), 0);
        summary.textContent = `当前共有 ${items.length} 组待确认词集，累计命中 ${totalHits} 次；已按最新命中时间优先排序。`;
        box.innerHTML = `
          <div class="fallback-monitor-grid">
            ${items.map((item, idx) => {
              const suggested = String(item?.suggested_formal_term || '').trim();
              const currentFormal = String(item?.__formal_head || '').trim();
              const fallbackTerms = Array.isArray(item?.fallback_terms) ? item.fallback_terms.filter(Boolean) : [];
              const manualAliases = Array.isArray(item?.__manual_aliases) ? item.__manual_aliases.filter(Boolean) : [];
              const suggestedAliases = Array.isArray(item?.candidate_alias_terms) ? item.candidate_alias_terms.filter(Boolean) : [];
              const autoFilteredTerms = Array.isArray(item?.auto_filtered_terms) ? item.auto_filtered_terms.filter(Boolean) : [];
              const defaultAliases = mergeUniqueTerms([
                ...suggestedAliases.filter(term => String(term || '').trim() && String(term || '').trim() !== currentFormal),
                ...manualAliases,
              ]);
              const scope = String(item?.__scope || fallbackScopeDefault(item)).trim() || fallbackScopeDefault(item);
              const genre = String(item?.__genre || item?.genre || '').trim();
              const latestAt = formatReadableTime(item?.latest_at);
              const parentHeads = Array.isArray(item?.parent_heads) ? item.parent_heads.slice(0, 6) : [];
              const rawVerbs = Array.isArray(item?.raw_verbs) ? item.raw_verbs.slice(0, 6) : [];
              const examples = Array.isArray(item?.examples) ? item.examples.slice(0, 2) : [];
              return `
                <div class="fallback-monitor-card">
                  <div class="fallback-monitor-head">
                    <div>
                      <div class="fallback-monitor-title">待运营确认</div>
                    </div>
                    <div class="status-bar" style="margin-top:0;">
                      <span class="status-chip">命中 ${escHtml(item?.hit_count || 0)} 次</span>
                      <span class="status-chip">${escHtml(genre || '通用候选')}</span>
                    </div>
                  </div>
                  <div class="section-box" style="margin-top:10px;">
                    <h5>这组保底词</h5>
                    <div>${renderPills(fallbackTerms)}</div>
                    <div class="muted-sm note-prefix" style="margin-top:6px;">最近命中时间：${escHtml(latestAt)}</div>
                    <div class="muted-sm note-prefix" style="margin-top:4px;">关联动作头词：${parentHeads.length ? escHtml(parentHeads.join(' / ')) : '暂无'}</div>
                    <div class="muted-sm note-prefix" style="margin-top:4px;">前台原始动词：${rawVerbs.length ? escHtml(rawVerbs.join(' / ')) : '暂无'}</div>
                    <div class="graph-two-col" style="margin-top:8px;">
                      <div>
                        <div class="muted-sm">前台原始动词</div>
                        <div>${rawVerbs.length ? renderPills(rawVerbs) : '<span class="muted">暂无</span>'}</div>
                      </div>
                      <div>
                        <div class="muted-sm">最终进入保底词集</div>
                        <div>${fallbackTerms.length ? renderPills(fallbackTerms) : '<span class="muted">暂无</span>'}</div>
                      </div>
                    </div>
                    <div class="muted-sm note-prefix" style="margin-top:4px;">示例片段：${examples.length ? escHtml(examples.join(' / ')) : '暂无示例片段'}</div>
                  </div>
                  <div class="editor-group">
                    <h6>标准展示词映射确认</h6>
                    <div class="row">
                      <div class="cell">
                        <input id="fallbackFormal_${idx}" value="${escHtml(currentFormal)}" placeholder="${escAttr(suggested ? `标准展示词，如 ${suggested}` : '标准展示词，如 惊叹')}" />
                      </div>
                      <div class="cell">
                        <select id="fallbackScope_${idx}">
                          <option value="common" ${scope === 'common' ? 'selected' : ''}>提升到通用层</option>
                          <option value="genre" ${scope === 'genre' ? 'selected' : ''}>提升到赛道层</option>
                        </select>
                      </div>
                    </div>
                    ${suggested ? `
                    <div class="mini-action-row" style="margin-top:6px;">
                      <button type="button" class="ghost-btn" onclick="applySuggestedFallbackFormal(${idx}, '${escAttr(suggested)}')">使用系统建议词：${escHtml(suggested)}</button>
                    </div>
                    ` : ''}
                    <div class="row" style="margin-top:8px;">
                      <div class="cell">
                        <select id="fallbackGenre_${idx}">
                          ${renderGenreSelectOptions(genre)}
                        </select>
                        <div id="fallbackGenreNote_${idx}" class="field-disabled-note"></div>
                      </div>
                    </div>
                    ${autoFilteredTerms.length ? `<div class="muted-sm note-prefix" style="margin-top:8px;">系统已自动过滤明显碎词：${escHtml(autoFilteredTerms.join(' / '))}</div>` : ''}
                    ${renderFallbackAliasChecklist(defaultAliases, currentFormal, idx)}
                    <div class="mini-action-row" style="margin-top:8px;">
                      <input id="fallbackAliasManual_${idx}" placeholder="手动增加碎词，如 惊恐道" />
                      <button type="button" class="ghost-btn" onclick="addFallbackAliasManual(${idx})">增加</button>
                    </div>
                    <div id="fallbackFeedback_${idx}" class="fallback-inline-feedback info">保存后，系统会优先把这些词映射到标准展示词，前台不再继续把碎词直接推荐给用户。</div>
                    <div class="mini-action-row">
                      <button id="fallbackSaveBtn_${idx}" onclick="applyActionFallbackResolution(${idx})">保存为标准展示词</button>
                      <button class="ghost-btn" onclick="openFallbackGraphMaintenance(${idx})">去图谱维护</button>
                    </div>
                  </div>
                </div>
              `;
            }).join('')}
          </div>
        `;
        items.forEach((_, idx) => syncFallbackScopeUi(idx));
        items.forEach((_, idx) => {
          const scopeSelect = document.getElementById(`fallbackScope_${idx}`);
          if (scopeSelect) {
            scopeSelect.onchange = () => syncFallbackScopeUi(idx);
          }
        });
      }
      function renderActionFallbackAlerts(data) {
        const banner = document.getElementById('fallbackAlertBanner');
        const title = document.getElementById('fallbackAlertTitle');
        const summary = document.getElementById('fallbackAlertSummary');
        const list = document.getElementById('fallbackAlertList');
        if (!banner || !title || !summary || !list) return;
        const pendingCount = Number(data?.pending_count || 0);
        const items = Array.isArray(data?.recent_items) ? data.recent_items : [];
        const latestAt = formatReadableTime(data?.latest_at);
        fallbackAlertState = { pendingCount, items, latestAt };
        banner.style.display = loginPhone ? '' : 'none';
        banner.classList.toggle('ok', pendingCount <= 0);
        if (pendingCount > 0) {
          title.textContent = `保底生成待办提醒 · ${pendingCount} 组待处理`;
          summary.textContent = `系统已自动识别到仍未完成标准展示词映射的保底词组。它们来自前台用户真实触发的“动作图谱推荐”，现在可以直接交给运营处理。${latestAt && latestAt !== '-' ? ` 最近一组命中时间：${latestAt}。` : ''}`;
          list.innerHTML = items.map(item => `
            <div class="alert-banner-item">
              <div style="font-weight:700; font-size:13px;">${escHtml(item?.suggested_formal_term || '待确认标准展示词')}</div>
              <div class="muted-sm" style="margin-top:4px;">保底词集：${escHtml((item?.fallback_terms || []).join(' / ') || '暂无')}</div>
              <div class="muted-sm" style="margin-top:4px;">赛道：${escHtml(item?.genre || '通用候选')} ｜ 命中 ${escHtml(item?.hit_count || 0)} 次</div>
              <div class="muted-sm" style="margin-top:4px;">最近命中：${escHtml(formatReadableTime(item?.latest_at))}</div>
            </div>
          `).join('');
        } else {
          title.textContent = '保底生成待办提醒 · 当前已清空';
          summary.textContent = '当前没有新的未处理保底词，后台无需额外介入。';
          list.innerHTML = '';
        }
      }
      async function loadActionFallbackAlerts(force = false) {
        if (!loginPhone && !force) return;
        const url = `${base()}/action-graph/fallback-monitor/alerts?limit=4&_ts=${Date.now()}`;
        const res = await fetch(url, { headers: h(), cache: 'no-store' });
        const data = await read(res);
        if (!res.ok) {
          show('fallbackMonitorOut', data);
          return;
        }
        renderActionFallbackAlerts(data);
      }
      function startFallbackAlertPolling() {
        if (fallbackAlertTimer) clearInterval(fallbackAlertTimer);
        if (!loginPhone) return;
        fallbackAlertTimer = setInterval(() => {
          loadActionFallbackAlerts().catch(err => show('fallbackMonitorOut', String(err?.message || err)));
        }, 60000);
      }
      function scrollToFallbackMonitor() {
        const card = document.getElementById('fallbackMonitorCard') || document.getElementById('fallbackMonitorView');
        if (card) {
          card.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }
      async function openFallbackGraphMaintenance(idx) {
        const formalHead = String(document.getElementById(`fallbackFormal_${idx}`)?.value || '').trim();
        const scope = String(document.getElementById(`fallbackScope_${idx}`)?.value || 'common').trim();
        const genre = String(document.getElementById(`fallbackGenre_${idx}`)?.value || '').trim();
        if (!formalHead) return show('graphManageOut', '请先填写标准展示词，再进入图谱维护');
        const nodeKey = scope === 'genre' && genre ? `${genre}::${formalHead}` : formalHead;
        const maintenanceCard = document.getElementById('graphManageBox')?.closest('.card');
        if (maintenanceCard) {
          maintenanceCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
        await openGraphMaintenance(nodeKey);
      }
      async function loadActionFallbackMonitor() {
        const url = `${base()}/action-graph/fallback-monitor?_ts=${Date.now()}`;
        const res = await fetch(url, { headers: h(), cache: 'no-store' });
        const data = await read(res);
        renderActionFallbackMonitor(data);
        show('fallbackMonitorOut', data);
      }
      async function applyActionFallbackResolution(idx) {
        const item = (actionFallbackMonitorState?.items || [])[idx];
        if (!item) return;
        const feedback = document.getElementById(`fallbackFeedback_${idx}`);
        const button = document.getElementById(`fallbackSaveBtn_${idx}`);
        const suggested = String(item?.suggested_formal_term || '').trim();
        const formalHead = String(document.getElementById(`fallbackFormal_${idx}`)?.value || '').trim();
        const scope = String(document.getElementById(`fallbackScope_${idx}`)?.value || 'common').trim();
        const genre = String(document.getElementById(`fallbackGenre_${idx}`)?.value || '').trim();
        const fallbackTerms = Array.isArray(item?.fallback_terms) ? item.fallback_terms.map(x => String(x || '').trim()).filter(Boolean) : [];
        const aliasTerms = mergeUniqueTerms(getSelectedFallbackAliases(idx))
          .filter(term => String(term || '').trim() && String(term || '').trim() !== formalHead);
        const ignoredTerms = mergeUniqueTerms(
          fallbackTerms.filter(term => term && term !== formalHead && !aliasTerms.includes(term))
        );
        if (!formalHead) {
          if (feedback) {
            feedback.className = 'fallback-inline-feedback error';
            feedback.textContent = suggested ? `请先填写标准展示词；如果你认同系统建议，可直接点“使用系统建议词：${suggested}”。` : '请先填写标准展示词。';
          }
          return show('fallbackMonitorOut', suggested ? `请先填写标准展示词，或使用系统建议词：${suggested}` : '请先填写标准展示词');
        }
        if (scope === 'genre' && !genre) {
          if (feedback) {
            feedback.className = 'fallback-inline-feedback error';
            feedback.textContent = '选择“提升到赛道层”时，请填写赛道。';
          }
          return show('fallbackMonitorOut', '选择“提升到赛道层”时，请填写赛道');
        }
        const highRiskAliases = aliasTerms.map(term => ({ term, risk: classifyFallbackRisk(term, formalHead) }))
          .filter(item => item.risk.level === 'danger' || item.risk.level === 'warn');
        if (highRiskAliases.length) {
          const riskText = highRiskAliases.map(item => `${item.term}（${item.risk.label}）`).join('、');
          const confirmed = window.confirm(`这组收敛里包含高风险碎词：${riskText}。\n继续保存后，这些词会直接归并到“${formalHead}”。\n如果这些词很泛，可能误伤其他语义。是否继续保存？`);
          if (!confirmed) {
            if (feedback) {
              feedback.className = 'fallback-inline-feedback warning';
              feedback.textContent = `已取消保存。当前包含高风险碎词：${riskText}。建议先释放、观察，或加入高风险词表。`;
            }
            return show('fallbackMonitorOut', `已取消保存，高风险碎词：${riskText}`);
          }
        }
        if (button) {
          button.disabled = true;
          button.textContent = '保存中...';
        }
        if (feedback) {
          feedback.className = 'fallback-inline-feedback info';
          feedback.textContent = '正在保存标准展示词收敛规则，并同步刷新图谱维护视图...';
        }
        try {
          const res = await fetch(`${base()}/action-graph/fallback-monitor/resolve`, {
            method: 'POST',
            headers: h({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({
              formal_head: formalHead,
              alias_terms: aliasTerms,
              ignored_terms: ignoredTerms,
              scope,
              genre,
            }),
          });
          const data = await read(res);
          show('fallbackMonitorOut', data);
          if (!res.ok) {
            if (feedback) {
              feedback.className = 'fallback-inline-feedback error';
              feedback.textContent = `保存失败：${data?.detail || data?.message || '请检查当前输入后重试。'}`;
            }
            return;
          }
          if (feedback) {
            feedback.className = 'fallback-inline-feedback success';
            feedback.textContent = aliasTerms.length
              ? `保存成功：已将 ${aliasTerms.join('、')} 归并到标准展示词 ${formalHead}。下次前台再命中这些碎词时，系统会优先推荐标准展示词。`
              : `保存成功：已将标准展示词 ${formalHead} 记入系统。后续同类词组会优先参考这条映射能力。`;
          }
          if (Array.isArray(actionFallbackMonitorState?.items)) {
            actionFallbackMonitorState.items = actionFallbackMonitorState.items.filter((_, itemIdx) => itemIdx !== idx);
            renderActionFallbackMonitor({
              ...actionFallbackMonitorState,
              pending_items: actionFallbackMonitorState.items,
              rules: actionFallbackMonitorState.rules,
              risk_terms: fallbackRiskTermState,
            });
          }
          await loadActionFallbackMonitor();
          await loadActionFallbackAlerts(true);
          await loadGraphMaintenanceCatalog(scope === 'common' ? formalHead : `${genre}::${formalHead}`);
          await loadGraphMaintenance(scope === 'common' ? formalHead : `${genre}::${formalHead}`);
        } catch (err) {
          const message = String(err?.message || err || 'unknown error');
          show('fallbackMonitorOut', { ok: false, detail: message });
          if (feedback) {
            feedback.className = 'fallback-inline-feedback error';
            feedback.textContent = `保存失败：${message}`;
          }
        } finally {
          if (button) {
            button.disabled = false;
            button.textContent = '保存为标准展示词';
          }
        }
      }
      async function releaseActionFallbackRule(aliasTerm, scope, genre, addToRisk = false) {
        const aliasValue = String(aliasTerm || '').trim();
        const res = await fetch(`${base()}/action-graph/fallback-monitor/release`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            alias_term: aliasValue,
            scope: String(scope || 'common').trim(),
            genre: String(genre || '').trim(),
          }),
        });
        const data = await read(res);
        show('fallbackMonitorOut', data);
        if (!res.ok) return;
        if (addToRisk && aliasValue) {
          const risk = classifyFallbackRisk(aliasValue);
          const riskLevel = risk.level === 'danger' ? 'danger' : 'warn';
          const note = '从标准展示词收敛规则释放后，已加入高风险碎词词表，避免再次被轻易收敛。';
          const riskRes = await fetch(`${base()}/action-graph/fallback-risk-terms`, {
            method: 'POST',
            headers: h({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ term: aliasValue, risk_level: riskLevel, note, enabled: true }),
          });
          const riskData = await read(riskRes);
          show('fallbackMonitorOut', riskData);
          if (riskRes.ok) {
            fallbackRiskTermState = Array.isArray(riskData?.items) ? riskData.items : [];
            renderActionFallbackRiskTermsAdmin(fallbackRiskTermState);
          }
        }
        await loadActionFallbackMonitor();
        await loadActionFallbackAlerts(true);
      }
      function supplementScopeDefault(source) {
        const key = String(source || '').trim();
        if (key === 'common') return 'common';
        if (key === 'genre') return 'genre';
        return '';
      }
      function supplementScopeHint(source) {
        const key = String(source || '').trim();
        if (key === 'common') return { text: '当前词来自通用层，已默认选择通用版素材。', level: 'info' };
        if (key === 'genre') return { text: '当前词来自赛道特化层，已默认选择当前赛道版素材。', level: 'info' };
        if (key === 'common+genre') return { text: '当前词同时来自通用元数据和赛道特化层，请先明确选择素材版本。', level: 'warning' };
        return { text: '当前词来源未标注，请先确认后再选择素材版本。', level: 'warning' };
      }
      function renderTermItems(list, suffix = '') {
        const arr = Array.isArray(list) ? list.filter(Boolean) : [];
        if (!arr.length) return '<span class="muted">无</span>';
        return `<div class="term-state-list">${arr.map(item => `
          <span class="term-state">
            ${escHtml(item.term || '')}${suffix}
            <span class="pill">${escHtml(sourceLabel(item.source))}</span>
          </span>
        `).join('')}</div>`;
      }
      function parseTermLines(raw) {
        return String(raw || '')
          .split(/\n|,|，/)
          .map(x => String(x || '').trim())
          .filter(Boolean);
      }
      function mergeUniqueTerms(list) {
        const out = [];
        const seen = new Set();
        for (const item of Array.isArray(list) ? list : []) {
          const value = String(item || '').trim();
          if (!value || seen.has(value)) continue;
          seen.add(value);
          out.push(value);
        }
        return out;
      }
      function findSupplementSeedStatus(nodeKey) {
        const key = String(nodeKey || '').trim();
        if (!key) return { label: '未建补充单', cls: '' };
        const related = latestSupplements.filter(item => String(item?.node_key || '').trim() === key);
        if (!related.length) return { label: '未建补充单', cls: '' };
        const pending = related.reduce((sum, item) => sum + Number(item?.progress?.pending_count || 0), 0);
        const covered = related.reduce((sum, item) => sum + Number(item?.progress?.covered_count || 0), 0);
        if (pending > 0) return { label: `待补 ${pending}`, cls: 'warn' };
        if (covered > 0) return { label: `已覆盖 ${covered}`, cls: 'ok' };
        return { label: '已建补充单', cls: '' };
      }
      function actionSeedReason(verbHead) {
        const key = String(verbHead || '').trim();
        const map = {
          '走': '基础位移动作，复用频率高',
          '快走': '常见过渡动作，容易命中脚步缺口',
          '跳': '动作感强，适合先补起跳与落地',
          '爬': '手脚并用类动作，素材区分度高',
          '飞': '高动态动作，常用作强化场面',
          '抓': '近景互动动作，覆盖面很广',
          '推': '接触与碰撞类动作，通用性强',
          '拉': '拉扯拖拽类动作，补库收益高',
          '摩擦': '布料/表面类通用动效，高复用',
          '蹲下': '姿态转换动作，适合补细微动作声',
        };
        return map[key] || '高复用基础动作';
      }
      function actionSeedStatusPriority(label) {
        const text = String(label || '').trim();
        if (text.startsWith('待补')) return 0;
        if (text === '未建补充单') return 1;
        if (text.startsWith('已覆盖')) return 2;
        return 3;
      }
      function actionSeedNextStep(nodeKey) {
        const status = findSupplementSeedStatus(nodeKey).label;
        if (status.startsWith('待补')) return '建议下一步：先去补充单补素材。';
        if (status === '未建补充单') return '建议下一步：先等待真实用户文本命中验证，命中后再进入补充链路。';
        if (status.startsWith('已覆盖')) return '建议下一步：优先回看图谱词是否还需继续扩充。';
        return '建议下一步：先检查图谱维护，再决定是否补素材。';
      }
      function actionSeedStatusExplain(label) {
        const text = String(label || '').trim();
        if (text.startsWith('待补')) return '已进入补充链路，当前仍缺素材。';
        if (text === '未建补充单') return '当前还缺真实用户命中验证，属于待验证基础种子。';
        if (text.startsWith('已覆盖')) return '当前已有基础素材，可继续观察是否还需补细分版本。';
        return '当前已进入补充链路，可继续按业务状态跟进。';
      }
      function summarizeActionSeedStatuses(items) {
        const rows = Array.isArray(items) ? items : [];
        const summary = { pending: 0, untracked: 0, covered: 0, created: 0 };
        rows.forEach(item => {
          const status = findSupplementSeedStatus(item?.node_key).label;
          if (status.startsWith('待补')) summary.pending += 1;
          else if (status === '未建补充单') summary.untracked += 1;
          else if (status.startsWith('已覆盖')) summary.covered += 1;
          else summary.created += 1;
        });
        return summary;
      }
      function renderActionSeedStats(summary) {
        const node = document.getElementById('graphSeedStats');
        if (!node) return;
        const data = summary || { pending: 0, untracked: 0, covered: 0, created: 0 };
        node.innerHTML = `
          <div class="status-bar">
            <span class="status-chip" title="已经进入补充链路，当前仍缺素材">待补 ${escHtml(data.pending || 0)}</span>
            <span class="status-chip" title="基础种子已预置，但还没被真实文本触发验证">待验证 ${escHtml(data.untracked || 0)}</span>
            <span class="status-chip" title="当前已有基础素材覆盖">已覆盖 ${escHtml(data.covered || 0)}</span>
          </div>
        `;
      }
      function supplementGroupMap() {
        const groups = new Map();
        for (const item of Array.isArray(latestSupplements) ? latestSupplements : []) {
          const key = String((item?.parent_node && item.parent_node.node_key) || `${item?.genre || ''}::${item?.verb || ''}`).trim();
          if (!key) continue;
          const current = groups.get(key) || {
            node_key: key,
            genre: String(item?.genre || '').trim(),
            verb: String(item?.verb || '').trim(),
            target_head: String(item?.target_head || item?.verb || '').trim(),
            items: [],
            pending_count: 0,
            covered_count: 0,
            ready_to_notify_count: 0,
            oldest_created_at: '',
            latest_created_at: '',
          };
          current.items.push(item);
          current.pending_count += Number((item?.progress && item.progress.pending_count) || 0);
          current.covered_count += Number((item?.progress && item.progress.covered_count) || 0);
          if (item?.ready_to_notify && String(item?.notification_status || '') !== '已通知') current.ready_to_notify_count += 1;
          const createdAt = String(item?.created_at || '').trim();
          if (createdAt && (!current.oldest_created_at || createdAt < current.oldest_created_at)) current.oldest_created_at = createdAt;
          if (createdAt && (!current.latest_created_at || createdAt > current.latest_created_at)) current.latest_created_at = createdAt;
          groups.set(key, current);
        }
        return groups;
      }
      function daysSinceIso(value) {
        const text = String(value || '').trim();
        if (!text) return null;
        const d = new Date(text);
        if (Number.isNaN(d.getTime())) return null;
        return Math.floor((Date.now() - d.getTime()) / 86400000);
      }
      function buildGraphPriorityBuckets() {
        const fallbackItems = Array.isArray(actionFallbackMonitorState?.items) ? actionFallbackMonitorState.items : [];
        const sections = Array.isArray(graphMaintenanceCatalogState?.genres) ? graphMaintenanceCatalogState.genres : [];
        const groupMap = supplementGroupMap();
        const recentFallback = [...fallbackItems]
          .sort((a, b) => Number(b?.hit_count || 0) - Number(a?.hit_count || 0) || String(b?.latest_at || '').localeCompare(String(a?.latest_at || '')))
          .slice(0, 5)
          .map(item => ({
            kind: 'fallback',
            title: String(item?.suggested_formal_term || '待确认标准展示词').trim(),
            subtitle: `${String(item?.genre || '通用候选').trim() || '通用候选'} ｜ 命中 ${Number(item?.hit_count || 0)} 次`,
            extra: `保底词：${(item?.fallback_terms || []).join(' / ') || '暂无'}`,
            latest_at: formatReadableTime(item?.latest_at),
            primaryText: '去处理保底生成',
            primaryAction: `scrollToFallbackMonitor()`,
          }));
        const supplementMissing = [];
        for (const item of fallbackItems) {
          const suggested = String(item?.suggested_formal_term || '').trim();
          const genre = String(item?.genre || '').trim();
          const nodeKey = genre ? `${genre}::${suggested}` : suggested;
          if (!suggested || !nodeKey || groupMap.has(nodeKey)) continue;
          const key = nodeKey;
          if (supplementMissing.some(entry => entry.nodeKey === key)) continue;
          supplementMissing.push({
            nodeKey: key,
            title: suggested,
            subtitle: `${genre || '通用层'} ｜ 最近真实命中 ${Number(item?.hit_count || 0)} 次`,
            extra: `前台原始动词：${(item?.raw_verbs || []).join(' / ') || (item?.parent_heads || []).join(' / ') || '暂无'}`,
            latest_at: formatReadableTime(item?.latest_at),
          });
        }
        const recentHitNoSupplement = supplementMissing
          .sort((a, b) => String(b.latest_at || '').localeCompare(String(a.latest_at || '')))
          .slice(0, 5)
          .map(item => ({
            kind: 'hit',
            title: item.title,
            subtitle: item.subtitle,
            extra: item.extra,
            latest_at: item.latest_at,
            primaryText: '去图谱维护',
            primaryAction: `openGraphMaintenance('${escAttr(item.nodeKey)}')`,
          }));
        const longPending = [...groupMap.values()]
          .filter(item => Number(item.pending_count || 0) > 0)
          .map(item => ({
            ...item,
            stale_days: daysSinceIso(item.oldest_created_at),
          }))
          .filter(item => item.stale_days === null || item.stale_days >= 7)
          .sort((a, b) => Number(b.stale_days ?? -1) - Number(a.stale_days ?? -1) || Number(b.pending_count || 0) - Number(a.pending_count || 0))
          .slice(0, 5)
          .map(item => ({
            kind: 'pending',
            title: String(item.target_head || item.verb || item.node_key || '').trim(),
            subtitle: `${String(item.genre || '通用层').trim() || '通用层'} ｜ 待补 ${Number(item.pending_count || 0)} 个`,
            extra: `补充单 ${item.items.length} 条 ｜ 已挂起 ${item.stale_days === null ? '多日' : `${item.stale_days} 天`}`,
            latest_at: formatReadableTime(item.oldest_created_at || item.latest_created_at),
            primaryText: '去补充',
            primaryAction: `jumpToSupplementGroup('${escAttr(item.node_key)}', 'pending')`,
          }));
        return { recentFallback, recentHitNoSupplement, longPending };
      }
      function renderGraphPriorityTodo() {
        const hint = document.getElementById('graphSeedHint');
        const stats = document.getElementById('graphSeedStats');
        const actions = document.getElementById('graphSeedActions');
        if (!hint || !stats || !actions) return;
        const buckets = buildGraphPriorityBuckets();
        const total = buckets.recentFallback.length + buckets.recentHitNoSupplement.length + buckets.longPending.length;
        hint.textContent = total
          ? '动态优先级提醒区：这里只给运营待办摘要，帮助快速判断“先处理什么”，不再展示固定种子清单。'
          : '动态优先级提醒区：当前还没有新的重点待办。';
        stats.innerHTML = `
          <div class="status-bar">
            <span class="status-chip" title="最近高频保底生成词">高频保底 ${escHtml(buckets.recentFallback.length)}</span>
            <span class="status-chip" title="最近真实命中但还没建补充单的动作词">待建补充 ${escHtml(buckets.recentHitNoSupplement.length)}</span>
            <span class="status-chip" title="已建补充单但长期未补齐的动作词">长期未补齐 ${escHtml(buckets.longPending.length)}</span>
          </div>
        `;
        const renderCards = (title, desc, list) => `
          <div class="section-box" style="margin-bottom:10px;">
            <h5>${title}</h5>
            <div class="muted-sm note-prefix" style="margin-bottom:8px;">${desc}</div>
            ${list.length ? `<div class="fallback-rule-list">${list.map(item => `
              <div class="term-block">
                <div class="term-title">${escHtml(item.title || '')}</div>
                <div class="muted-sm">${escHtml(item.subtitle || '')}</div>
                <div class="muted-sm" style="margin-top:4px;">${escHtml(item.extra || '')}</div>
                <div class="muted-sm" style="margin-top:4px;">最近时间：${escHtml(item.latest_at || '-')}</div>
                <button class="link-btn" style="margin-top:6px;" onclick="${item.primaryAction}">${escHtml(item.primaryText || '查看')}</button>
              </div>
            `).join('')}</div>` : '<div class="muted-sm">当前没有需要优先处理的项目。</div>'}
          </div>
        `;
        actions.innerHTML = [
          renderCards('最近高频保底生成词', '来自前台用户真实触发后的保底词集。优先处理命中频率高、碎词明显的词组。', buckets.recentFallback),
          renderCards('最近真实命中但还没建补充单的动作词', '这些词已经被真实前台使用命中，但当前还没有进入补充单链路，适合先判断是否要启动补库。', buckets.recentHitNoSupplement),
          renderCards('已建补充单但长期未补齐的动作词', '这些词已经进入补充单，但仍有待补项，且已挂起较久，适合优先清理积压。', buckets.longPending),
        ].join('');
      }
      function normalizeCompositeTerm(raw) {
        return String(raw || '').trim().replace(/（整体）$/u, '').replace(/（组合）$/u, '').trim();
      }
      function buildLayerEditorState(layer) {
        const src = layer || {};
        return {
          semantic_terms: mergeUniqueTerms(src.semantic_terms || []),
          direct_sfx_terms: mergeUniqueTerms(src.direct_sfx_terms || []),
          composite_sfx_terms: mergeUniqueTerms(src.composite_sfx_terms || []),
        };
      }
      function getLayerStateKey(layer) {
        return layer === 'common' ? 'common_editor' : 'genre_editor';
      }
      function getLayerState(layer) {
        if (!graphMaintenanceState) return { semantic_terms: [], direct_sfx_terms: [], composite_sfx_terms: [] };
        const key = getLayerStateKey(layer);
        return graphMaintenanceState[key] || { semantic_terms: [], direct_sfx_terms: [], composite_sfx_terms: [] };
      }
      function getLayerSearch(layer) {
        if (!graphMaintenanceState) return { semantic: '', direct: '', composite: '' };
        return graphMaintenanceState.search?.[layer] || { semantic: '', direct: '', composite: '' };
      }
      function getCandidateSearch(field) {
        if (!graphMaintenanceState) return '';
        return String(graphMaintenanceState.candidateSearch?.[field] || '');
      }
      function renderGraphMaintenancePreservingFocus(data, focusId, selectionStart = null, selectionEnd = null) {
        renderGraphMaintenance(data);
        if (!focusId) return;
        setTimeout(() => {
          const input = document.getElementById(focusId);
          if (!input || typeof input.focus !== 'function') return;
          input.focus();
          if (typeof input.setSelectionRange === 'function' && selectionStart !== null && selectionEnd !== null) {
            try {
              input.setSelectionRange(selectionStart, selectionEnd);
            } catch (_) {}
          }
        }, 0);
      }
      function setLayerSearch(layer, field, value) {
        if (!graphMaintenanceState) return;
        const active = document.activeElement;
        const focusId = active?.id || `search-${layer}-${field}`;
        const selectionStart = typeof active?.selectionStart === 'number' ? active.selectionStart : String(value || '').length;
        const selectionEnd = typeof active?.selectionEnd === 'number' ? active.selectionEnd : selectionStart;
        graphMaintenanceState.search = graphMaintenanceState.search || {};
        graphMaintenanceState.search[layer] = {
          ...(graphMaintenanceState.search[layer] || { semantic: '', direct: '', composite: '' }),
          [field]: String(value || ''),
        };
        renderGraphMaintenancePreservingFocus(graphMaintenanceState.rawData || {}, focusId, selectionStart, selectionEnd);
      }
      function setCandidateSearch(field, value) {
        if (!graphMaintenanceState) return;
        const active = document.activeElement;
        const focusId = active?.id || `candidate-search-${field}`;
        const selectionStart = typeof active?.selectionStart === 'number' ? active.selectionStart : String(value || '').length;
        const selectionEnd = typeof active?.selectionEnd === 'number' ? active.selectionEnd : selectionStart;
        graphMaintenanceState.candidateSearch = {
          ...(graphMaintenanceState.candidateSearch || { semantic: '', sfx: '' }),
          [field]: String(value || ''),
        };
        renderGraphMaintenancePreservingFocus(graphMaintenanceState.rawData || {}, focusId, selectionStart, selectionEnd);
      }
      function setLayerState(layer, nextState) {
        if (!graphMaintenanceState) return;
        graphMaintenanceState[getLayerStateKey(layer)] = {
          semantic_terms: mergeUniqueTerms(nextState.semantic_terms || []),
          direct_sfx_terms: mergeUniqueTerms(nextState.direct_sfx_terms || []),
          composite_sfx_terms: mergeUniqueTerms(nextState.composite_sfx_terms || []),
        };
      }
      function updateLayerTerms(layer, termType, updater) {
        const current = getLayerState(layer);
        const next = {
          semantic_terms: [...current.semantic_terms],
          direct_sfx_terms: [...current.direct_sfx_terms],
          composite_sfx_terms: [...current.composite_sfx_terms],
        };
        next[termType] = mergeUniqueTerms(updater(next[termType]));
        setLayerState(layer, next);
      }
      function removeLayerTerm(layer, termType, term) {
        const current = getLayerState(layer);
        const next = {
          semantic_terms: [...(current.semantic_terms || [])],
          direct_sfx_terms: [...(current.direct_sfx_terms || [])],
          composite_sfx_terms: [...(current.composite_sfx_terms || [])],
        };
        next[termType] = next[termType].filter(x => x !== term);
        if (termType === 'semantic_terms' && next.composite_sfx_terms.includes(term)) {
          next.composite_sfx_terms = next.composite_sfx_terms.filter(x => x !== term);
        }
        if (termType === 'composite_sfx_terms' && next.semantic_terms.includes(term)) {
          next.semantic_terms = next.semantic_terms.filter(x => x !== term);
        }
        setLayerState(layer, next);
        renderGraphMaintenance(graphMaintenanceState?.rawData || {});
      }
      function addLayerTerm(layer, termType, term) {
        const value = termType === 'composite_sfx_terms' ? normalizeCompositeTerm(term) : String(term || '').trim();
        if (!value) return;
        updateLayerTerms(layer, termType, (list) => [...list, value]);
        renderGraphMaintenance(graphMaintenanceState?.rawData || {});
      }
      function addCustomLayerTerm(layer, termType, inputId) {
        const input = document.getElementById(inputId);
        const value = input?.value || '';
        addLayerTerm(layer, termType, value);
        if (input) input.value = '';
      }
      function buildLayerSavePayload(layer) {
        const state = getLayerState(layer);
        return {
          semantic_terms: mergeUniqueTerms(state.semantic_terms || []),
          sfx_terms: mergeUniqueTerms([...(state.direct_sfx_terms || []), ...(state.composite_sfx_terms || [])]),
        };
      }
      function filterSuggestionTerms(terms, query) {
        const q = String(query || '').trim();
        const list = mergeUniqueTerms(terms || []);
        if (!q) return list.sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'));
        const lower = q.toLowerCase();
        return list
          .filter(term => String(term || '').toLowerCase().includes(lower))
          .sort((a, b) => {
            const ai = String(a || '').toLowerCase().indexOf(lower);
            const bi = String(b || '').toLowerCase().indexOf(lower);
            return ai - bi || String(a || '').localeCompare(String(b || ''), 'zh-Hans-CN');
          });
      }
      function graphMaintenanceFeedback(data) {
        if (!data || !data.ok) return '';
        if (Array.isArray(data.promoted_semantic_terms) || Array.isArray(data.promoted_sfx_terms)) {
          const semanticCount = (data.promoted_semantic_terms || []).length;
          const sfxCount = (data.promoted_sfx_terms || []).length;
          const modeText = data.remove_from_genre ? '并已从赛道层移除' : '并保留赛道层';
          const semanticText = semanticCount ? `语义词：${(data.promoted_semantic_terms || []).join('、')}` : '';
          const sfxText = sfxCount ? `音效词：${(data.promoted_sfx_terms || []).join('、')}` : '';
          return `迁移已完成。${semanticText}${semanticText && sfxText ? ' ｜ ' : ''}${sfxText}${semanticText || sfxText ? ' ｜ ' : ''}${modeText}。`;
        }
        if (Array.isArray(data.demoted_semantic_terms) || Array.isArray(data.demoted_sfx_terms)) {
          const semanticCount = (data.demoted_semantic_terms || []).length;
          const sfxCount = (data.demoted_sfx_terms || []).length;
          const modeText = data.remove_from_common ? '并已从通用元数据移除' : '并保留通用元数据';
          const semanticText = semanticCount ? `语义词：${(data.demoted_semantic_terms || []).join('、')}` : '';
          const sfxText = sfxCount ? `音效词：${(data.demoted_sfx_terms || []).join('、')}` : '';
          return `赛道分配已完成。${semanticText}${semanticText && sfxText ? ' ｜ ' : ''}${sfxText}${semanticText || sfxText ? ' ｜ ' : ''}${modeText}。`;
        }
        if (Array.isArray(data.removed_overlap_semantic_terms) || Array.isArray(data.removed_overlap_sfx_terms)) {
          const semanticCount = (data.removed_overlap_semantic_terms || []).length;
          const sfxCount = (data.removed_overlap_sfx_terms || []).length;
          const layerText = data.removed_from_layer === 'genre' ? '赛道特化层' : '通用元数据层';
          const semanticText = semanticCount ? `语义词：${(data.removed_overlap_semantic_terms || []).join('、')}` : '';
          const sfxText = sfxCount ? `音效词：${(data.removed_overlap_sfx_terms || []).join('、')}` : '';
          return `已从${layerText}移除重叠词。${semanticText}${semanticText && sfxText ? ' ｜ ' : ''}${sfxText}。`;
        }
        if (data.node_delete_action) {
          if (data.node_delete_action === 'delete_genre_keep_common') {
            return '已删除当前赛道特化节点，并保留通用元数据。';
          }
          if (data.node_delete_action === 'delete_common_keep_genre') {
            return '已删除通用元数据节点，并保留当前赛道特化。';
          }
          if (data.node_delete_action === 'delete_current_layer') {
            return `已删除当前${data.deleted_layer === 'genre' ? '赛道特化层' : '通用元数据层'}节点。`;
          }
        }
        if (data.updated_layer) {
          return `已保存${data.updated_layer === 'common' ? '通用元数据' : '赛道特化'}配置。`;
        }
        return '图谱维护操作已完成。';
      }
      function setGraphMaintenanceUiState(patch = {}) {
        graphMaintenanceState = graphMaintenanceState || {};
        graphMaintenanceState.ui = {
          ...(graphMaintenanceState.ui || {
            busyAction: '',
            feedbackText: '',
            feedbackLevel: 'info',
            inheritFeedbackText: '',
            promoteFeedbackText: '',
            demoteFeedbackText: '',
            demoteTargetGenre: '',
            inheritTargetGenre: '',
            overlapTargetGenre: '',
            nodeDeleteTargetGenre: '',
            promoteSelection: { semantic: [], sfx: [] },
            demoteSelection: { semantic: [], sfx: [] },
            overlapGenreSelection: { semantic: [], sfx: [] },
            overlapCommonSelection: { semantic: [], sfx: [] },
            overlapGenreFeedbackText: '',
            overlapCommonFeedbackText: '',
            commonSaveFeedbackText: '',
            genreSaveFeedbackText: '',
            nodeKeepOtherFeedbackText: '',
            nodeDeleteFeedbackText: '',
          }),
          ...(patch || {}),
        };
      }
      function getGraphCandidateSelection(actionName, termType) {
        const ui = graphMaintenanceState?.ui || {};
        const bucket = ui[actionName] || { semantic: [], sfx: [] };
        return Array.isArray(bucket?.[termType]) ? bucket[termType] : [];
      }
      function isGraphCandidateSelected(actionName, termType, term) {
        const value = String(term || '').trim();
        return getGraphCandidateSelection(actionName, termType).includes(value);
      }
      function setGraphCandidateSelection(actionName, termType, term, checked) {
        const value = String(term || '').trim();
        if (!value) return;
        const ui = graphMaintenanceState?.ui || {};
        const bucket = {
          semantic: [...(Array.isArray(ui?.[actionName]?.semantic) ? ui[actionName].semantic : [])],
          sfx: [...(Array.isArray(ui?.[actionName]?.sfx) ? ui[actionName].sfx : [])],
        };
        const next = checked
          ? mergeUniqueTerms([...(bucket[termType] || []), value])
          : (bucket[termType] || []).filter(item => item !== value);
        bucket[termType] = next;
        setGraphMaintenanceUiState({ [actionName]: bucket });
      }
      function inlineActionFeedback(text, level = 'info') {
        const value = String(text || '').trim();
        if (!value) return '';
        const bg = ({
          success: 'rgba(32, 125, 74, 0.12)',
          error: 'rgba(179, 63, 32, 0.12)',
          warning: 'rgba(194, 126, 13, 0.14)',
          info: 'rgba(41, 85, 160, 0.12)',
        })[level] || 'rgba(41, 85, 160, 0.12)';
        const color = ({
          success: '#1d6b44',
          error: '#9d3520',
          warning: '#9a630a',
          info: '#2955a0',
        })[level] || '#2955a0';
        return `<span style="display:inline-flex;align-items:center;min-height:36px;padding:0 12px;border-radius:999px;background:${bg};color:${color};font-size:12px;font-weight:700;">${escHtml(value)}</span>`;
      }
      function setSupplementInlineFeedback(itemId, termIdx, text, level = 'info') {
        const el = document.getElementById(`suppFeedback_${itemId}_${termIdx}`);
        if (!el) return;
        el.innerHTML = inlineActionFeedback(text, level);
      }
      function renderSupplementSfxBuckets(children, fallbackTerms, fallbackMissingTerms) {
        const direct = Array.isArray(children && children.direct_sfx_terms) ? children.direct_sfx_terms : [];
        const composite = Array.isArray(children && children.composite_sfx_terms) ? children.composite_sfx_terms : [];
        const missingDirect = Array.isArray(children && children.missing_direct_sfx_terms) ? children.missing_direct_sfx_terms : [];
        const missingComposite = Array.isArray(children && children.missing_composite_sfx_terms) ? children.missing_composite_sfx_terms : [];
        const covered = Array.isArray(children && children.covered_sfx_terms) ? children.covered_sfx_terms : [];
        const coveredSet = new Set(covered.map(x => String(x).trim()).filter(Boolean));
        const renderStateTerms = (arr, compositeMode = false) => {
          if (!arr.length) return '<span class="muted">无</span>';
          return `<div class="term-state-list">${arr.map(term => {
            const t = String(term || '').trim();
            const isCovered = coveredSet.has(t);
            return `<span class="term-state ${isCovered ? 'covered' : 'pending'}"><span class="term-icon">${isCovered ? '✓' : '?'}</span>${escHtml(t)}${compositeMode ? '（整体）' : ''}</span>`;
          }).join('')}</div>`;
        };
        return {
          allHtml: direct.length || composite.length
            ? `${direct.length ? `<div><div class="muted-sm">直达音效</div><div>${renderPills(direct)}</div></div>` : ''}${composite.length ? `<div style="margin-top:6px;"><div class="muted-sm">整体音效</div><div>${renderPills(composite.map(x => `${x}（整体）`))}</div></div>` : ''}`
            : renderPills(fallbackTerms || []),
          missingHtml: direct.length || composite.length
            ? `${direct.length ? `<div><div class="muted-sm">直达音效补充状态</div>${renderStateTerms(direct, false)}</div>` : ''}${composite.length ? `<div style="margin-top:6px;"><div class="muted-sm">整体音效补充状态</div>${renderStateTerms(composite, true)}</div>` : ''}`
            : renderPills(fallbackMissingTerms || []),
          coveredHtml: '<span class="muted">已合并状态已在上方用对号标记，不再重复展示。</span>',
        };
      }
      function supplementStatusLabel(status) {
        const key = String(status || '').trim();
        return ({
          'pending': '待合并',
          'partial': '部分补齐',
          'ready_to_notify': '已补齐待通知',
          'merged': '已合并（全补齐）',
          'merged_duplicate': '已并入重复单',
        })[key] || (key || '未知');
      }
      function updateSupplementStatusHint() {
        const sel = document.getElementById('suppStatus');
        const hint = document.getElementById('suppStatusHint');
        if (!sel || !hint) return;
        const key = String(sel.value || '').trim();
        const text = {
          '': '查看全部补充单状态，适合复盘和排查整条补库链路。',
          'pending': '`pending` 表示待合并。还没真正把素材并入这条补充单。',
          'partial': '`partial` 表示部分补齐。已经上传过一部分素材，但还没全部补全。',
          'ready_to_notify': '`ready_to_notify` 表示这条补充单已补齐待通知。该视图只展示业务状态为 `ready_to_notify` 且尚未通知用户的数据。',
          'merged': '`merged` 表示全补齐视图，用来总览已经补齐的补充单，不等同于提醒状态。',
          'notified': '`notified` 表示提醒状态已通知。已通知的数据会进入这个视图，不再出现在“已补齐待通知”视图里。',
        }[key] || '选择一个状态查看对应补充单。';
        hint.textContent = text;
      }

      function refreshNotifyTargets(items) {
        const sel = document.getElementById('notifyTarget');
        if (!sel) return;
        const list = Array.isArray(items) ? items : [];
        const readyItems = list
          .filter(item => String(item.status || '') === 'ready_to_notify' && String(item.notification_status || '') !== '已通知')
          .sort((a, b) => {
            const phoneCmp = String(a.user_phone || '').localeCompare(String(b.user_phone || ''));
            if (phoneCmp) return phoneCmp;
            return Number(a.id || 0) - Number(b.id || 0);
          });
        const options = ['<option value="">请选择手机号与单条补充单</option>'];
        for (const item of readyItems) {
          const phone = String(item.user_phone || '').trim();
          const id = Number(item.id || 0);
          const verb = String(item.verb || '').trim();
          const label = `${phone} ｜ #${id} ｜ ${verb || '未命名动作词'}`;
          options.push(`<option value="${escHtml(JSON.stringify({ phone, item_ids: [id], verb }))}">${escHtml(label)}</option>`);
        }
        sel.innerHTML = options.join('');
      }

      async function loadNotifyTargets() {
        const sel = document.getElementById('notifyTarget');
        if (!sel) return;
        const res = await fetch(`${base()}/ops/action-supplements?days=180&status=ready_to_notify`, { headers: h() });
        const data = await read(res);
        const items = Array.isArray(data && data.items) ? data.items : [];
        refreshNotifyTargets(items);
      }

      function renderStats(summary) {
        const s = summary || {};
        const statusCounter = s.status_counter || {};
        return `
          <div class="stats">
            <div class="stat"><div class="k">补充单数</div><div class="v">${escHtml(s.item_count || 0)}</div></div>
            <div class="stat"><div class="k">涉及用户</div><div class="v">${escHtml(s.unique_user_count || 0)}</div></div>
            <div class="stat"><div class="k">待补词数</div><div class="v">${escHtml(s.pending_term_count || 0)}</div></div>
            <div class="stat"><div class="k">已补词数</div><div class="v">${escHtml(s.covered_term_count || 0)}</div></div>
            <div class="stat"><div class="k">可通知单数</div><div class="v">${escHtml(s.ready_to_notify_count || 0)}</div></div>
            <div class="stat"><div class="k">已合并单数</div><div class="v">${escHtml(s.merged_count || 0)}</div></div>
          </div>
          <div class="status-bar">
            ${Object.entries(statusCounter).map(([k, v]) => `<span class="status-chip">${escHtml(k)}：${escHtml(v)}</span>`).join('')}
          </div>
        `;
      }

      function normalizePhoneInput(raw) {
        let phone = String(raw || '').replace(/\D/g, '');
        if (phone.startsWith('86') && phone.length === 13) phone = phone.slice(2);
        return phone;
      }

      function isValidPhoneInput(raw) {
        return /^1\d{10}$/.test(normalizePhoneInput(raw));
      }

      async function sendCode() {
        const phone = normalizePhoneInput((document.getElementById('phone').value || '').trim());
        document.getElementById('phone').value = phone;
        if (!phone) return show('usersOut', '请输入手机号');
        if (!isValidPhoneInput(phone)) return show('usersOut', '请输入有效的11位手机号');
        const res = await fetch(`${base()}/auth/request-code`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ phone }),
        });
        const data = await read(res);
        if (data && data.code) document.getElementById('code').value = data.code;
        show('usersOut', data);
      }

      async function login() {
        const phone = normalizePhoneInput((document.getElementById('phone').value || '').trim());
        const code = (document.getElementById('code').value || '').trim();
        document.getElementById('phone').value = phone;
        if (!isValidPhoneInput(phone)) return show('usersOut', '请输入有效的11位手机号');
        const res = await fetch(`${base()}/auth/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ phone, code }),
        });
        const data = await read(res);
        if (res.ok && data.token) {
          token = data.token;
          loginPhone = data.user.phone;
          localStorage.setItem('mfa_admin_token', token);
          localStorage.setItem('mfa_admin_phone', loginPhone);
          setState();
          loadBetaAccess();
          loadFrontendSecurity();
          loadInviteCodes();
          loadUsers();
          loadNotifyTargets();
          loadAssetFeedback();
          loadActionFallbackMonitor();
          loadActionFallbackAlerts();
          loadCreatorShowcases();
          loadCopyrightAdsAdmin();
          loadRechargeOrders();
          startFallbackAlertPolling();
        }
        show('usersOut', data);
      }

      async function loadUsers() {
        const ymd = (document.getElementById('usageViewDate').value || '').trim();
        const q = new URLSearchParams();
        if (ymd) q.set('ymd', ymd);
        const suffix = q.toString() ? `?${q.toString()}` : '';
        const res = await fetch(`${base()}/admin/users${suffix}`, { headers: h() });
        const data = await read(res);
        renderUsersTable(data);
        show('usersOut', data);
      }

      async function loadBetaAccess() {
        const res = await fetch(`${base()}/admin/beta-access`, { headers: h() });
        const data = await read(res);
        if (res.ok) {
          document.getElementById('betaInviteOnly').value = data.invite_only_enabled ? '1' : '0';
        }
        show('betaAccessOut', data);
      }

      async function saveBetaAccess() {
        const invite_only_enabled = document.getElementById('betaInviteOnly').value === '1';
        const res = await fetch(`${base()}/admin/beta-access`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ invite_only_enabled }),
        });
        show('betaAccessOut', await read(res));
      }

      function renderFrontendSecurityState(data) {
        const hint = document.getElementById('frontendSecurityHint');
        if (!hint) return;
        if (!data || typeof data !== 'object') {
          hint.textContent = '当前没有可展示的前端安全配置。';
          return;
        }
        const debugText = data.frontend_debug_expose_enabled ? '测试期调试 JSON 已开启' : '正式期调试 JSON 已关闭';
        const leaderboardText = data.home_leaderboards_enabled ? '首页热门榜已对外展示' : '首页热门榜当前隐藏';
        const downloadText = data.signed_downloads_required ? '正式环境签名下载已开启' : '当前环境仍兼容本地直连下载';
        const ttlText = `下载令牌时效 ${Number(data.download_token_ttl_sec || 300)} 秒`;
        hint.textContent = `${debugText}；${leaderboardText}；${downloadText}；${ttlText}。`;
        const sel = document.getElementById('frontendDebugExpose');
        if (sel) sel.value = data.frontend_debug_expose_enabled ? '1' : '0';
        const boardSel = document.getElementById('homeLeaderboardsVisible');
        if (boardSel) boardSel.value = data.home_leaderboards_enabled ? '1' : '0';
      }

      async function loadFrontendSecurity() {
        const res = await fetch(`${base()}/admin/frontend-security`, { headers: h() });
        const data = await read(res);
        renderFrontendSecurityState(data);
        show('frontendSecurityOut', data);
      }

      async function saveFrontendSecurity() {
        const frontend_debug_expose_enabled = document.getElementById('frontendDebugExpose').value === '1';
        const home_leaderboards_enabled = document.getElementById('homeLeaderboardsVisible').value === '1';
        const res = await fetch(`${base()}/admin/frontend-security`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ frontend_debug_expose_enabled, home_leaderboards_enabled }),
        });
        const data = await read(res);
        renderFrontendSecurityState(data);
        show('frontendSecurityOut', data);
      }

      async function loadInviteCodes() {
        const res = await fetch(`${base()}/admin/invite-codes`, { headers: h() });
        show('inviteCodesOut', await read(res));
      }

      async function saveUser() {
        const phone = normalizePhoneInput((document.getElementById('uPhone').value || '').trim());
        const uid = (document.getElementById('uUid').value || '').trim();
        document.getElementById('uPhone').value = phone;
        if (!isValidPhoneInput(phone)) return show('usersOut', '请输入有效的11位手机号');
        const is_authorized = document.getElementById('uAuth').value === '1';
        const is_admin = document.getElementById('uAdmin').value === '1';
        const ops_role_code = (document.getElementById('uRole').value || '33').trim();
        const user_tier_code = (document.getElementById('uTier').value || '33').trim();
        const daily_limit = Number(document.getElementById('uLimit').value || 3);
        const daily_text_char_limit = Number(document.getElementById('uTextCharLimit').value || 0);
        const daily_sfx_download_limit = Number(document.getElementById('uSfxDownloadLimit').value || 0);
        const res = await fetch(`${base()}/admin/users`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            phone,
            uid,
            ops_role_code,
            user_tier_code,
            is_authorized,
            is_admin,
            daily_limit,
            daily_text_char_limit,
            daily_sfx_download_limit,
          }),
        });
        show('usersOut', await read(res));
        await loadUsers();
      }

      async function resetUserUsage() {
        const phone = normalizePhoneInput((document.getElementById('usageResetPhone').value || '').trim());
        const ymd = (document.getElementById('usageResetDate').value || '').trim();
        document.getElementById('usageResetPhone').value = phone;
        if (!isValidPhoneInput(phone)) return show('usageResetOut', '请输入有效的11位手机号');
        const payload = { phone };
        if (ymd) payload.ymd = ymd;
        const res = await fetch(`${base()}/admin/users/reset-usage`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify(payload),
        });
        show('usageResetOut', await read(res));
        await loadUsers();
      }

      async function loadEvents() {
        const phone = (document.getElementById('qPhone').value || '').trim();
        const action = (document.getElementById('qAction').value || '').trim();
        const pid = (document.getElementById('qProject').value || '').trim();
        const limit = (document.getElementById('qLimit').value || '100').trim();
        const q = new URLSearchParams();
        if (phone) q.set('phone', phone);
        if (action) q.set('action', action);
        if (pid) q.set('project_id', pid);
        q.set('limit', limit);
        const res = await fetch(`${base()}/ops/user-events?${q.toString()}`, { headers: h() });
        const data = await read(res);
        renderOpsEvents(data);
        show('eventsOut', data);
      }

      async function loadFunnel() {
        const res = await fetch(`${base()}/ops/funnel?days=7`, { headers: h() });
        const data = await read(res);
        renderOpsFunnel(data);
        show('eventsOut', data);
      }

      async function loadRecommendations() {
        const res = await fetch(`${base()}/ops/recommendations?days=7`, { headers: h() });
        const data = await read(res);
        renderOpsRecommendations(data);
        show('eventsOut', data);
      }

      function summarizeEvent(item) {
        const action = String((item && item.action) || '').trim();
        const input = (item && item.input) || {};
        const output = (item && item.output) || {};
        if (action === 'create_project') return `创建项目：${input.title || output.project_id || '未命名'}。`;
        if (action === 'audio_analysis') return `完成音乐分析，识别 ${Number(output.marker_count || 0)} 个音乐锚点。`;
        if (action === 'text_analysis') return `完成文本分析，输入 ${Number(input.text_len || 0)} 字，输出 ${Number(output.scene_count || 0)} 个场景。`;
        if (action === 'action_verb_analysis') return `完成动作提取，输入 ${Number(input.text_len || 0)} 字，识别 ${Number(output.qualified_count || 0)} 个需做动作音效的动词。`;
        if (action === 'action_sfx_graph') return `完成动作图谱推荐，产出 ${Number(output.graph_item_count || 0)} 条动作图谱结果。`;
        if (action === 'scene_building_analysis') return `完成场景搭建分析，输入 ${Number(input.text_len || 0)} 字，识别 ${Number(output.scene_count || 0)} 个场景。`;
        if (action === 'scene_sfx_graph') return `完成场景音效推荐，产出 ${Number(output.scene_item_count || 0)} 条场景结果，缺口 ${Number(output.gap_count || 0)} 个。`;
        if (action === 'text_narration_analysis') return `完成文本演绎分析，输入 ${Number(input.text_len || 0)} 字，生成 ${Number(output.narration_clause_timeline_count || 0)} 条语句时间线。`;
        if (action === 'fusion_execution') return `生成后期执行单，输出 ${Number(output.cue_count || 0)} 条 cue。`;
        if (action === 'action_sfx_asset_download') return `下载单个音效：${input.file_name || input.label || '未标注音效'}。`;
        if (action === 'scene_sfx_asset_download') return `下载场景音效：${input.file_name || input.label || '未标注音效'}。`;
        return `记录到行为：${action || '未标注动作'}。`;
      }

      function renderOpsEvents(data) {
        const note = document.getElementById('opsAuditNote');
        const box = document.getElementById('opsAuditView');
        if (!note || !box) return;
        const items = Array.isArray(data && data.items) ? data.items : [];
        const actionCounts = (data && typeof data.action_counts === 'object' && data.action_counts) ? data.action_counts : {};
        const totalMatched = Number((data && data.total_matched) || items.length || 0);
        if (!items.length) {
          note.textContent = '行为日志：来自用户行为日志表，主要记录谁在什么时间做了什么业务动作。当前筛选条件下没有结果。';
          box.innerHTML = '<div class="ops-section"><h4>行为时间线</h4><div class="muted">当前筛选条件下没有行为日志。</div></div>';
          return;
        }
        const uniqueUsers = new Set(items.map(item => String(item.user_phone || '').trim()).filter(Boolean)).size;
        const uniqueProjects = new Set(items.map(item => String(item.project_id || '').trim()).filter(Boolean)).size;
        const topActions = Object.entries(actionCounts).sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0)).slice(0, 8);
        opsTimelineState.items = items;
        opsTimelineState.totalPages = Math.max(1, Math.ceil(opsTimelineState.items.length / OPS_TIMELINE_PAGE_SIZE));
        opsTimelineState.currentPage = 1;
        note.textContent = `行为日志：来自用户行为日志表，用来回答“谁在什么时间做了什么业务动作、结果如何、是否形成下载或执行单”等问题。当前时间范围内共命中 ${totalMatched} 条；下方时间线默认展示最新 ${items.length} 条。`;
        const summaryHtml = `
          <div class="ops-section">
            <h4>行为总览</h4>
            <div class="stats">
              <div class="stat"><div class="k">行为总数</div><div class="v">${escHtml(totalMatched)}</div></div>
              <div class="stat"><div class="k">涉及用户</div><div class="v">${escHtml(uniqueUsers)}</div></div>
              <div class="stat"><div class="k">涉及项目</div><div class="v">${escHtml(uniqueProjects)}</div></div>
              <div class="stat"><div class="k">最近一条</div><div class="v" style="font-size:14px;">${escHtml(formatReadableTime(items[0].created_at || '-'))}</div></div>
              <div class="stat"><div class="k">当前筛选</div><div class="v" style="font-size:14px;">${escHtml(items[0].action ? '已应用' : '全部')}</div></div>
            </div>
          </div>
        `;
        const distHtml = `
          <div class="ops-section">
            <h4>行为分布</h4>
            ${topActions.map(([action, count]) => {
              const ratio = Math.max(4, Math.round((Number(count || 0) / Math.max(1, totalMatched)) * 100));
              return `
                <div class="ops-bar-row">
                  <div class="ops-bar-label">
                    ${escHtml(actionDisplayName(action))}
                    <details class="ops-inline-detail">
                      <summary style="cursor:pointer; color:#9eb5d8;">查看动作码</summary>
                      <div>${escHtml(action)}</div>
                    </details>
                  </div>
                  <div class="ops-bar-track"><div class="ops-bar-fill" style="width:${ratio}%;"></div></div>
                  <div class="ops-bar-value">${escHtml(count)} 次</div>
                </div>
              `;
            }).join('')}
          </div>
        `;
        const timelineHtml = `
          <div class="ops-section">
            <h4>最近行为时间线</h4>
            <div id="opsTimelineBox"></div>
          </div>
        `;
        box.innerHTML = summaryHtml + distHtml + timelineHtml;
        renderOpsTimelinePage();
      }

      function changeOpsTimelinePage(delta) {
        const next = Math.max(1, Math.min(opsTimelineState.totalPages, opsTimelineState.currentPage + delta));
        if (next === opsTimelineState.currentPage) return;
        opsTimelineState.currentPage = next;
        renderOpsTimelinePage();
      }

      function renderOpsTimelinePage() {
        const box = document.getElementById('opsTimelineBox');
        if (!box) return;
        const items = Array.isArray(opsTimelineState.items) ? opsTimelineState.items : [];
        if (!items.length) {
          box.innerHTML = '<div class="muted">当前没有时间线数据。</div>';
          return;
        }
        const start = (opsTimelineState.currentPage - 1) * OPS_TIMELINE_PAGE_SIZE;
        const pageItems = items.slice(start, start + OPS_TIMELINE_PAGE_SIZE);
        const pageStart = start + 1;
        const pageEnd = start + pageItems.length;
        box.innerHTML = `
          <div class="ops-pager">
            <button style="width:auto; min-width:120px;" ${opsTimelineState.currentPage <= 1 ? 'disabled' : ''} onclick="changeOpsTimelinePage(-1)">上一页</button>
            <div class="ops-pager-info">第 ${opsTimelineState.currentPage} / ${opsTimelineState.totalPages} 页，当前显示 ${pageStart}-${pageEnd} / ${items.length}</div>
            <button style="width:auto; min-width:120px;" ${opsTimelineState.currentPage >= opsTimelineState.totalPages ? 'disabled' : ''} onclick="changeOpsTimelinePage(1)">下一页</button>
          </div>
          <div class="ops-timeline">
              ${pageItems.map(item => `
                <div class="ops-event-card">
                  <div class="ops-event-head">
                    <div class="ops-event-title">${escHtml(actionDisplayName(item.action || ''))}</div>
                    <div class="ops-event-meta">${escHtml(formatReadableTime(item.created_at || '-'))}</div>
                  </div>
                  <div class="ops-kv-list">
                    <div class="k">识别信息</div><div>UID：${escHtml(item.user_uid || '-')}<br/>手机号：${escHtml(item.user_phone || '-')}</div>
                    <div class="k">项目 ID</div><div>${escHtml(item.project_id || '-')}</div>
                    <div class="k">动作码</div><div>${escHtml(item.action || '-')}</div>
                    <div class="k">行为摘要</div><div>${escHtml(summarizeEvent(item))}</div>
                  </div>
                  <details style="margin-top:8px;">
                    <summary style="cursor:pointer; color:#9eb5d8;">展开详情</summary>
                    <div class="ops-event-summary" style="margin-top:8px;">
                      输入摘要：${escHtml(JSON.stringify(item.input || {}, null, 2))}<br/>
                      输出摘要：${escHtml(JSON.stringify(item.output || {}, null, 2))}<br/>
                      文件引用：${escHtml(JSON.stringify(item.file_refs || [], null, 2))}
                    </div>
                  </details>
                </div>
              `).join('')}
          </div>
        `;
      }

      function renderOpsFunnel(data) {
        const note = document.getElementById('opsAuditNote');
        const box = document.getElementById('opsAuditView');
        if (!note || !box) return;
        const funnel = Array.isArray(data && data.funnel) ? data.funnel : [];
        const exportType = (data && data.export_type_count) || {};
        note.textContent = '运营漏斗：当前并不是“用户增长漏斗”，而是 AI 推理链路漏斗。数据源来自推理日志 ReasoningLog，主要看有多少项目依次走到图谱推理、素材搜索、融合执行单和导出下载。';
        box.innerHTML = `
          <div class="ops-section">
            <h4>AI 推理链路漏斗</h4>
            <div class="muted" style="margin-bottom:8px;">统计周期：最近 ${escHtml(data && data.days || 7)} 天。这个功能适合观察“哪些项目走到后面的 AI/导出环节”，不等同于完整业务漏斗。</div>
            ${funnel.map(item => `
              <div class="ops-bar-row">
                <div class="ops-bar-label">${escHtml(item.step || '-')}</div>
                <div class="ops-bar-track"><div class="ops-bar-fill" style="width:${Math.max(4, Math.round(Number(item.rate_from_graph_reason || 0) * 100))}%;"></div></div>
                <div class="ops-bar-value">${escHtml(item.project_count || 0)} 个项目</div>
              </div>
            `).join('')}
          </div>
          <div class="ops-section">
            <h4>导出类型补充</h4>
            <div class="ops-kv-list">
              <div class="k">Cue CSV 导出</div><div>${escHtml(exportType.cue_csv || 0)} 次</div>
              <div class="k">SFX ZIP 导出</div><div>${escHtml(exportType.sfx_zip || 0)} 次</div>
            </div>
          </div>
        `;
      }

      function renderOpsRecommendations(data) {
        const note = document.getElementById('opsAuditNote');
        const box = document.getElementById('opsAuditView');
        if (!note || !box) return;
        const rec = (data && data.recommendations) || {};
        const expandTerms = Array.isArray(rec.expand_lexicon_for_terms) ? rec.expand_lexicon_for_terms : [];
        const addAssets = Array.isArray(rec.add_sfx_assets_for_terms) ? rec.add_sfx_assets_for_terms : [];
        note.textContent = '优化建议：数据源来自推理日志 ReasoningLog。它不是泛泛而谈的“建议”，而是告诉运营和图谱维护人员，哪些词经常搜不到、哪些音效经常缺素材，适合拿来指导补词典和补音效库。';
        box.innerHTML = `
          <div class="ops-section">
            <h4>建议先扩词典</h4>
            ${expandTerms.length ? expandTerms.map(item => `
              <div class="ops-event-card">
                <div class="ops-event-head">
                  <div class="ops-event-title">${escHtml(item.term || '-')}</div>
                  <div class="ops-event-meta">出现 ${escHtml(item.count || 0)} 次</div>
                </div>
                <div class="ops-event-summary">这类词在素材搜索中反复低命中，说明图谱或语义词典里可能还没有覆盖到。建议优先补同义词、归并头词，或者检查是否应该进入动作/场景图谱。</div>
              </div>
            `).join('') : '<div class="muted">当前没有“需扩词典”的明显词项。</div>'}
          </div>
          <div class="ops-section">
            <h4>建议先补素材</h4>
            ${addAssets.length ? addAssets.map(item => `
              <div class="ops-event-card">
                <div class="ops-event-head">
                  <div class="ops-event-title">${escHtml(item.term || '-')}</div>
                  <div class="ops-event-meta">缺失 ${escHtml(item.count || 0)} 次</div>
                </div>
                <div class="ops-event-summary">这类音效词在导出或匹配阶段反复缺素材，说明它已经形成真实补库需求。建议优先上传素材，或者在图谱后台核对它是否应拆成通用版/赛道版。</div>
              </div>
            `).join('') : '<div class="muted">当前没有“需补素材”的明显音效词。</div>'}
          </div>
        `;
      }

      function currentLeaderboardWindow() {
        return (document.getElementById('leaderboardWindow')?.value || '10d').trim() || '10d';
      }

      function renderLeaderboardAdmin(data) {
        const box = document.getElementById('leaderboardAdminView');
        if (!box) return;
        const layout = data?.layout || {};
        const domainOrder = Array.isArray(layout.domain_order) ? layout.domain_order : ['action', 'scene'];
        const actionPos = domainOrder.indexOf('action') + 1 || 1;
        const scenePos = domainOrder.indexOf('scene') + 1 || 2;
        const boards = Array.isArray(data?.boards) ? data.boards : [];
        box.innerHTML = `
          <div class="muted">当前展示的是动作后台对应的“动作榜”配置。排行榜前台按真实下载统计生成；如某条展示不适合直接上前台，可在下方对显示名、显示数量、前台排序做手动覆盖。</div>
          <div class="leader-admin-box">
            <h4 style="margin:0 0 8px; font-size:13px;">用户端位置与标题</h4>
            <div class="row">
              <div class="cell">
                <label class="muted">动作榜标题</label>
                <input id="lbActionTitle" value="${escHtml(layout?.domain_titles?.action || '动作音效热度榜')}" />
              </div>
              <div class="cell">
                <label class="muted">场景榜标题</label>
                <input id="lbSceneTitle" value="${escHtml(layout?.domain_titles?.scene || '场景搭建热度榜')}" />
              </div>
              <div class="cell">
                <label class="muted">动作榜位置</label>
                <select id="lbActionPos">
                  <option value="1" ${actionPos === 1 ? 'selected' : ''}>第 1 区</option>
                  <option value="2" ${actionPos === 2 ? 'selected' : ''}>第 2 区</option>
                </select>
              </div>
              <div class="cell">
                <label class="muted">场景榜位置</label>
                <select id="lbScenePos">
                  <option value="1" ${scenePos === 1 ? 'selected' : ''}>第 1 区</option>
                  <option value="2" ${scenePos === 2 ? 'selected' : ''}>第 2 区</option>
                </select>
              </div>
              <div class="cell" style="align-self:flex-end;"><button onclick="saveLeaderboardLayout()">保存前台位置与标题</button></div>
            </div>
          </div>
          ${boards.map((board) => `
            <div class="leader-admin-board">
              <h4>${escHtml(board.title || '')}</h4>
              <div class="muted">当前时间范围：${escHtml(data.window_key || '10d')}，当前榜单下载量 ${escHtml(board.download_count || 0)}。</div>
              ${Array.isArray(board.items) && board.items.length ? board.items.map((item, idx) => `
                <div class="leader-admin-item">
                  <div class="leader-admin-item-head">
                    <div><span class="leader-admin-rank">${idx + 1}</span> <strong>${escHtml(item.display_name || item.item_key || '-')}</strong></div>
                    <div>真实下载 ${escHtml(item.raw_count || item.count || 0)} 次${item.has_override ? ' ｜ 已有手动覆盖' : ''}</div>
                  </div>
                  <div class="row">
                    <div class="cell">
                      <label class="muted">前台展示名</label>
                      <input id="lb_name_${escAttr(board.board_key)}_${escAttr(item.item_key)}" value="${escHtml(item.display_name || '')}" />
                    </div>
                    <div class="cell">
                      <label class="muted">前台副标题</label>
                      <input id="lb_sub_${escAttr(board.board_key)}_${escAttr(item.item_key)}" value="${escHtml(item.subtitle || '')}" />
                    </div>
                    <div class="cell">
                      <label class="muted">前台显示数量</label>
                      <input id="lb_count_${escAttr(board.board_key)}_${escAttr(item.item_key)}" value="${escHtml(item.count || 0)}" />
                    </div>
                    <div class="cell">
                      <label class="muted">前台排序位</label>
                      <input id="lb_rank_${escAttr(board.board_key)}_${escAttr(item.item_key)}" value="${escHtml(item.manual_rank || '')}" placeholder="留空=按真实排序" />
                    </div>
                    <div class="cell">
                      <label class="muted">是否展示</label>
                      <select id="lb_enabled_${escAttr(board.board_key)}_${escAttr(item.item_key)}">
                        <option value="1" ${item.enabled === false ? '' : 'selected'}>展示</option>
                        <option value="0" ${item.enabled === false ? 'selected' : ''}>隐藏</option>
                      </select>
                    </div>
                    <div class="cell" style="align-self:flex-end;">
                      <button onclick="saveLeaderboardOverride('${escAttr(data.domain || 'action')}','${escAttr(board.board_key || '')}','${escAttr(item.item_key || '')}','${escAttr(item.scope_label || '')}','${escAttr(item.genre || '')}','${escAttr(item.file_name || '')}')">保存这一条</button>
                    </div>
                  </div>
                </div>
              `).join('') : '<div class="muted" style="margin-top:8px;">当前时间范围内暂无真实下载数据。</div>'}
            </div>
          `).join('')}
        `;
      }

      async function saveLeaderboardLayout() {
        const actionTitle = (document.getElementById('lbActionTitle')?.value || '').trim();
        const sceneTitle = (document.getElementById('lbSceneTitle')?.value || '').trim();
        const actionPos = Number(document.getElementById('lbActionPos')?.value || 1);
        const scenePos = Number(document.getElementById('lbScenePos')?.value || 2);
        const domainOrder = actionPos <= scenePos ? ['action', 'scene'] : ['scene', 'action'];
        const res = await fetch(`${base()}/admin/leaderboards/layout`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            domain_order: domainOrder,
            domain_titles: {
              action: actionTitle,
              scene: sceneTitle,
            },
          }),
        });
        const data = await read(res);
        show('assetFeedbackOut', data);
        await loadAssetFeedback();
      }

      async function saveLeaderboardOverride(domain, boardKey, itemKey, scopeLabel, genre, fileName) {
        const name = (document.getElementById(`lb_name_${boardKey}_${itemKey}`)?.value || '').trim();
        const subtitle = (document.getElementById(`lb_sub_${boardKey}_${itemKey}`)?.value || '').trim();
        const overrideCount = (document.getElementById(`lb_count_${boardKey}_${itemKey}`)?.value || '').trim();
        const manualRank = (document.getElementById(`lb_rank_${boardKey}_${itemKey}`)?.value || '').trim();
        const enabled = (document.getElementById(`lb_enabled_${boardKey}_${itemKey}`)?.value || '1') === '1';
        const res = await fetch(`${base()}/admin/leaderboards/override`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            domain,
            board_key: boardKey,
            window_key: currentLeaderboardWindow(),
            item_key: itemKey,
            display_name: name,
            subtitle,
            override_count: overrideCount,
            manual_rank: manualRank,
            enabled,
            meta: {
              scope_label: scopeLabel,
              genre,
              file_name: fileName,
            },
          }),
        });
        const data = await read(res);
        show('assetFeedbackOut', data);
        await loadAssetFeedback();
      }

      async function loadAssetFeedback() {
        const windowKey = currentLeaderboardWindow();
        leaderboardAdminState.windowKey = windowKey;
        const res = await fetch(`${base()}/admin/leaderboards/config?domain=action&window=${encodeURIComponent(windowKey)}`, { headers: h() });
        const data = await read(res);
        leaderboardAdminState.data = data;
        renderLeaderboardAdmin(data);
        show('assetFeedbackOut', data);
      }

      async function previewTokenize() {
        const text = (document.getElementById('tokenizeText').value || '').trim();
        const res = await fetch(`${base()}/semantic/tokenize?text=${encodeURIComponent(text)}`, { headers: h() });
        show('tokenizeOut', await read(res));
      }

      async function loadActionGraphNeo4jStatus() {
        const res = await fetch(`${base()}/action-graph/neo4j-status`, { headers: h() });
        show('actionNeo4jOut', await read(res));
      }

      async function syncActionGraphNeo4j() {
        const res = await fetch(`${base()}/action-graph/neo4j-sync`, {
          method: 'POST',
          headers: h(),
        });
        show('actionNeo4jOut', await read(res));
      }

      async function queryActionGraphNode() {
        const nodeKey = (document.getElementById('actionNodeKey').value || '').trim();
        const res = await fetch(`${base()}/action-graph/node?node_key=${encodeURIComponent(nodeKey)}`, { headers: h() });
        const data = await read(res);
        renderGraphDetail(data);
        show('actionNeo4jOut', data);
      }

      async function browseActionGraphNodes() {
        const genre = (document.getElementById('graphGenre').value || '').trim();
        const q = (document.getElementById('graphQuery').value || '').trim();
        const limit = (document.getElementById('graphLimit').value || '50').trim();
        const onlyWithGap = (document.getElementById('graphOnlyGap').value || '0').trim();
        const sortBy = (document.getElementById('graphSortBy').value || 'pending').trim();
        const statusFilter = (document.getElementById('graphStatusFilter').value || 'all').trim();
        const res = await fetch(`${base()}/action-graph/nodes?genre=${encodeURIComponent(genre)}&q=${encodeURIComponent(q)}&limit=${encodeURIComponent(limit)}&only_with_gap=${encodeURIComponent(onlyWithGap)}&sort_by=${encodeURIComponent(sortBy)}&status_filter=${encodeURIComponent(statusFilter)}`, { headers: h() });
        const data = await read(res);
        renderGraphBrowser(data);
        show('actionNeo4jOut', data);
      }

      async function applyGraphWorkbenchPreset(kind) {
        const onlyGap = document.getElementById('graphOnlyGap');
        const sortBy = document.getElementById('graphSortBy');
        const statusFilter = document.getElementById('graphStatusFilter');
        if (!onlyGap || !sortBy || !statusFilter) return;
        if (kind === 'all') {
          onlyGap.value = '0';
          sortBy.value = 'pending';
          statusFilter.value = 'all';
        } else if (kind === 'gap') {
          onlyGap.value = '1';
          sortBy.value = 'pending';
          statusFilter.value = 'incomplete';
        } else if (kind === 'notify') {
          onlyGap.value = '0';
          sortBy.value = 'notify';
          statusFilter.value = 'notify';
        } else if (kind === 'notified') {
          onlyGap.value = '0';
          sortBy.value = 'recent';
          statusFilter.value = 'notified';
        } else if (kind === 'active') {
          onlyGap.value = '0';
          sortBy.value = 'recent';
          statusFilter.value = 'all';
        }
        await browseActionGraphNodes();
      }

      function openGraphNode(nodeKey) {
        document.getElementById('actionNodeKey').value = nodeKey;
        queryActionGraphNode().then(() => {
          const detail = document.getElementById('graphDetail');
          if (detail) {
            detail.scrollIntoView({ behavior: 'smooth', block: 'start' });
            detail.style.boxShadow = '0 0 0 2px rgba(30,200,255,0.75)';
            setTimeout(() => { detail.style.boxShadow = ''; }, 1800);
          }
        });
      }

      async function openGraphMaintenance(nodeKey) {
        const input = document.getElementById('graphManageNodeKey');
        if (input) input.value = nodeKey;
        const hintText = actionSeedNextStep(nodeKey);
        if (graphMaintenanceCatalogState?.genres?.length) {
          renderGraphMaintenancePicker(nodeKey);
          await loadGraphMaintenance(nodeKey);
        } else {
          await loadGraphMaintenanceCatalog(nodeKey);
          await loadGraphMaintenance(nodeKey);
        }
        setGraphMaintenanceUiState({
          feedbackText: hintText,
          feedbackLevel: 'info',
        });
        renderGraphMaintenance(graphMaintenanceState?.rawData || {});
        const maintenanceCard = document.getElementById('graphManageBox')?.closest('.card');
        if (maintenanceCard) {
          maintenanceCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }

      function nodeKeyToId(nodeKey) {
        return `supp-group-${String(nodeKey || '').replace(/[^a-zA-Z0-9\u4e00-\u9fa5_-]+/g, '_')}`;
      }

      function splitGraphManageNodeKey(nodeKey) {
        const raw = String(nodeKey || '').trim();
        if (!raw) return { genreKey: '', verbHead: '' };
        if (raw.includes('::')) {
          const [genre, verbHead] = raw.split('::', 2);
          return { genreKey: String(genre || '').trim() || 'common', verbHead: String(verbHead || '').trim() };
        }
        return { genreKey: 'common', verbHead: raw };
      }

      function findGraphManageSection(genreKey) {
        return (graphMaintenanceCatalogState?.genres || []).find(section => String(section.genre_key || '') === String(genreKey || '')) || null;
      }

      function syncGraphManageNodeInput(nodeKey) {
        const input = document.getElementById('graphManageNodeKey');
        if (input) input.value = nodeKey || '';
      }

      function renderGraphMaintenancePicker(preferredNodeKey = '') {
        const genreSel = document.getElementById('graphManageGenre');
        const verbQueryInput = document.getElementById('graphManageVerbQuery');
        const verbSel = document.getElementById('graphManageVerb');
        const hint = document.getElementById('graphManagePickerHint');
        const seedHint = document.getElementById('graphSeedHint');
        const seedActions = document.getElementById('graphSeedActions');
        const sections = Array.isArray(graphMaintenanceCatalogState?.genres) ? graphMaintenanceCatalogState.genres : [];
        if (!genreSel || !verbSel || !verbQueryInput) return;
        if (!sections.length) {
          genreSel.innerHTML = '<option value="">暂无赛道目录</option>';
          verbQueryInput.value = graphMaintenanceCatalogState.verbQuery || '';
          verbSel.innerHTML = '<option value="">暂无动作词</option>';
          if (hint) hint.textContent = '当前还没有可维护的图谱目录。';
          if (seedHint) seedHint.textContent = '当前还没有可用于生成动态优先级提醒的数据。';
          if (seedActions) seedActions.innerHTML = '';
          return;
        }
        renderGraphPriorityTodo();
        const preferred = splitGraphManageNodeKey(preferredNodeKey);
        const selectedGenreKey = (
          preferred.genreKey && sections.some(section => String(section.genre_key || '') === preferred.genreKey)
            ? preferred.genreKey
            : graphMaintenanceCatalogState.selectedGenreKey && sections.some(section => String(section.genre_key || '') === String(graphMaintenanceCatalogState.selectedGenreKey || ''))
              ? graphMaintenanceCatalogState.selectedGenreKey
              : String(sections[0].genre_key || '')
        );
        graphMaintenanceCatalogState.selectedGenreKey = selectedGenreKey;
        verbQueryInput.value = graphMaintenanceCatalogState.verbQuery || '';
        genreSel.innerHTML = sections.map(section => `
          <option value="${escHtml(section.genre_key || '')}" ${String(section.genre_key || '') === selectedGenreKey ? 'selected' : ''}>
            ${escHtml(section.label || section.genre_key || '')}（${escHtml(section.node_count || 0)}）
          </option>
        `).join('');
        const section = findGraphManageSection(selectedGenreKey);
        const items = Array.isArray(section?.items) ? section.items : [];
        const query = String(graphMaintenanceCatalogState.verbQuery || '').trim().toLowerCase();
        const filteredItems = items.filter(item => !query || String(item.verb_head || '').toLowerCase().includes(query));
        if (!filteredItems.length) {
          graphMaintenanceCatalogState.selectedNodeKey = '';
          verbSel.innerHTML = `<option value="">${query ? '没有匹配当前搜索条件的动作词' : '当前赛道暂无动作词'}</option>`;
          syncGraphManageNodeInput('');
          if (hint) hint.textContent = query ? `当前赛道下没有匹配“${graphMaintenanceCatalogState.verbQuery}”的动作词。` : '当前赛道还没有可维护的动作词。';
          return;
        }
        const selectedNodeKey = (
          preferredNodeKey && filteredItems.some(item => String(item.node_key || '') === String(preferredNodeKey || ''))
            ? String(preferredNodeKey || '')
            : graphMaintenanceCatalogState.selectedNodeKey && filteredItems.some(item => String(item.node_key || '') === String(graphMaintenanceCatalogState.selectedNodeKey || ''))
              ? String(graphMaintenanceCatalogState.selectedNodeKey || '')
              : String(filteredItems[0].node_key || '')
        );
        graphMaintenanceCatalogState.selectedNodeKey = selectedNodeKey;
        verbSel.innerHTML = filteredItems.map(item => `
          <option value="${escHtml(item.node_key || '')}" ${String(item.node_key || '') === selectedNodeKey ? 'selected' : ''}>
            ${escHtml(item.verb_head || '')}
          </option>
        `).join('');
        syncGraphManageNodeInput(selectedNodeKey);
        const current = filteredItems.find(item => String(item.node_key || '') === selectedNodeKey) || filteredItems[0];
        if (hint && current) {
          hint.textContent = `当前已筛出 ${filteredItems.length}/${items.length} 个动作词，请继续查看下方维护详情。`;
        }
      }

      async function loadGraphMaintenanceCatalog(preferredNodeKey = '') {
        const res = await fetch(`${base()}/action-graph/maintenance-catalog`, { headers: h() });
        const data = await read(res);
        if (!res.ok || !data || !Array.isArray(data.genres)) {
          show('graphManageOut', data);
          graphMaintenanceCatalogState = { genres: [], selectedGenreKey: '', selectedNodeKey: '' };
          renderGraphMaintenancePicker('');
          return;
        }
        graphMaintenanceCatalogState = {
          genres: data.genres || [],
          selectedGenreKey: graphMaintenanceCatalogState.selectedGenreKey || '',
          selectedNodeKey: graphMaintenanceCatalogState.selectedNodeKey || '',
          verbQuery: graphMaintenanceCatalogState.verbQuery || '',
        };
        renderGraphMaintenancePicker(preferredNodeKey);
      }

      async function loadInheritanceDashboard() {
        const res = await fetch(`${base()}/action-graph/inheritance-dashboard?days=30`, { headers: h() });
        const data = await read(res);
        if (!res.ok || !data) {
          inheritanceDashboardState = { blocked_pool: { items: [], count: 0 }, review_pool: { items: [], count: 0, days: 30 } };
          renderInheritanceDashboard();
          return;
        }
        inheritanceDashboardState = {
          blocked_pool: data.blocked_pool || { items: [], count: 0 },
          review_pool: data.review_pool || { items: [], count: 0, days: 30 },
        };
        renderInheritanceDashboard();
      }

      async function toggleGraphInheritance(genre, verbHead, action) {
        const genreValue = String(genre || '').trim();
        const headValue = String(verbHead || '').trim();
        if (!genreValue || !headValue) return;
        const actionLabel = action === 'block' ? '从当前赛道删除' : '恢复到当前赛道';
        setGraphMaintenanceUiState({
          inheritFeedbackText: `正在${actionLabel}：${genreValue} -> ${headValue}，请稍候...`,
          feedbackLevel: 'info',
        });
        if (graphMaintenanceState?.rawData) renderGraphMaintenance(graphMaintenanceState.rawData || {});
        const res = await fetch(`${base()}/action-graph/inheritance-block`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ genre: genreValue, verb_head: headValue, action }),
        });
        const data = await read(res);
        show('graphManageOut', data);
        setGraphMaintenanceUiState({
          inheritFeedbackText: res.ok
            ? `${actionLabel}成功：${genreValue} 现在${action === 'block' ? '不再展示' : '重新展示'} ${headValue}。`
            : (data?.detail || data?.message || `${actionLabel}失败，请稍后重试。`),
          feedbackLevel: res.ok ? 'success' : 'error',
        });
        if (!res.ok) {
          if (graphMaintenanceState?.rawData) renderGraphMaintenance(graphMaintenanceState.rawData || {});
          return;
        }
        await loadInheritanceDashboard();
        await loadGraphMaintenanceCatalog(currentGraphManageNodeKey());
        const currentNodeKey = currentGraphManageNodeKey();
        if (currentNodeKey) await loadGraphMaintenance(currentNodeKey);
      }

      async function toggleCurrentInheritance(blocked) {
        const rawData = graphMaintenanceState?.rawData || {};
        const isCommonOnlyNode = !String(rawData.genre || '').trim();
        const genreValue = String(
          isCommonOnlyNode
            ? (document.getElementById('graphInheritTargetGenre')?.value || graphMaintenanceState?.ui?.inheritTargetGenre || '')
            : (rawData.genre || '')
        ).trim();
        const verbHead = String(rawData.verb_head || '').trim();
        if (!genreValue || !verbHead) {
          setGraphMaintenanceUiState({
            feedbackText: '请先选择目标赛道，再执行继承控制。',
            feedbackLevel: 'warning',
          });
          renderGraphMaintenance(rawData);
          return show('graphManageOut', '请先选择目标赛道');
        }
        if (isCommonOnlyNode) {
          setGraphMaintenanceUiState({ inheritTargetGenre: genreValue });
        }
        await toggleGraphInheritance(genreValue, verbHead, blocked ? 'block' : 'restore');
      }

      async function deleteCurrentCommonVerb() {
        const rawData = graphMaintenanceState?.rawData || {};
        const isCommonOnlyNode = !String(rawData.genre || '').trim();
        if (isCommonOnlyNode) {
          return deleteGraphNode('delete_current_layer');
        }
        return deleteGraphNode('delete_common_keep_genre');
      }

      async function deleteCurrentGenreVerb() {
        const rawData = graphMaintenanceState?.rawData || {};
        const hasSavedCommonNode = Boolean(rawData?.common_layer?.exists);
        if (hasSavedCommonNode) {
          return deleteGraphNode('delete_genre_keep_common');
        }
        return deleteGraphNode('delete_current_layer');
      }

      async function onGraphManageGenreChange() {
        const genreSel = document.getElementById('graphManageGenre');
        graphMaintenanceCatalogState.selectedGenreKey = (genreSel?.value || '').trim();
        graphMaintenanceCatalogState.selectedNodeKey = '';
        inheritanceBlockedPageState.currentPage = 1;
        renderInheritanceDashboard();
        renderGraphMaintenancePicker('');
        const nodeKey = graphMaintenanceCatalogState.selectedNodeKey || '';
        if (nodeKey) await loadGraphMaintenance(nodeKey);
      }

      function onGraphManageVerbQueryChange() {
        const input = document.getElementById('graphManageVerbQuery');
        graphMaintenanceCatalogState.verbQuery = (input?.value || '').trim();
        renderGraphMaintenancePicker('');
      }

      async function onGraphManageVerbChange() {
        const verbSel = document.getElementById('graphManageVerb');
        const nodeKey = (verbSel?.value || '').trim();
        graphMaintenanceCatalogState.selectedNodeKey = nodeKey;
        syncGraphManageNodeInput(nodeKey);
        renderGraphMaintenancePicker(nodeKey);
        if (nodeKey) await loadGraphMaintenance(nodeKey);
      }

      async function openNewGenreNode() {
        const genreSel = document.getElementById('graphManageGenre');
        const input = document.getElementById('graphCreateVerb');
        const genreValue = String(genreSel?.value || '').trim();
        const verbValue = String(input?.value || '').trim();
        if (!genreValue) {
          const msg = '请先选择要创建节点的赛道。';
          const hint = document.getElementById('graphManagePickerHint');
          if (hint) hint.textContent = msg;
          return show('graphManageOut', msg);
        }
        if (!verbValue) {
          const msg = '请先输入新的赛道动作词。';
          const hint = document.getElementById('graphManagePickerHint');
          if (hint) hint.textContent = msg;
          return show('graphManageOut', msg);
        }
        const nodeKey = `${genreValue}::${verbValue}`;
        graphMaintenanceCatalogState.selectedNodeKey = nodeKey;
        syncGraphManageNodeInput(nodeKey);
        await loadGraphMaintenance(nodeKey);
        setGraphMaintenanceUiState({
          feedbackText: `已打开新的赛道动作词节点：${nodeKey}。请在下方“赛道特化维护区”补充语义词和音效词后保存。`,
          feedbackLevel: 'info',
        });
        renderGraphMaintenance(graphMaintenanceState?.rawData || {});
        const box = document.getElementById('graphManageBox');
        if (box) box.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }

      async function openNewCommonNode() {
        const input = document.getElementById('graphCreateCommonVerb');
        const verbValue = String(input?.value || '').trim();
        if (!verbValue) {
          const msg = '请先输入新的通用动词。';
          const hint = document.getElementById('graphManagePickerHint');
          if (hint) hint.textContent = msg;
          return show('graphManageOut', msg);
        }
        const nodeKey = `${verbValue}`;
        graphMaintenanceCatalogState.selectedGenreKey = 'common';
        graphMaintenanceCatalogState.selectedNodeKey = nodeKey;
        syncGraphManageNodeInput(nodeKey);
        await loadGraphMaintenance(nodeKey);
        setGraphMaintenanceUiState({
          feedbackText: `已打开新的通用动词节点：${nodeKey}。请在下方“通用元数据层”补充语义词和音效词后保存。`,
          feedbackLevel: 'info',
        });
        renderGraphMaintenance(graphMaintenanceState?.rawData || {});
        const box = document.getElementById('graphManageBox');
        if (box) box.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }

      async function jumpToSupplementGroup(nodeKey, status = '') {
        if (status) {
          const sel = document.getElementById('suppStatus');
          if (sel) sel.value = status;
        }
        await loadSupplements();
        const target = document.getElementById(nodeKeyToId(nodeKey));
        if (target) {
          target.scrollIntoView({ behavior: 'smooth', block: 'start' });
          target.style.boxShadow = '0 0 0 2px rgba(30,200,255,0.75)';
          setTimeout(() => { target.style.boxShadow = ''; }, 1800);
        }
      }

      function renderGraphBrowser(data) {
        const box = document.getElementById('graphBrowser');
        if (!box) return;
        const items = Array.isArray(data && data.items) ? data.items : [];
        if (!items.length) {
          box.innerHTML = '<div class="muted">当前没有可浏览的父节点。</div>';
          return;
        }
        const totalNodes = items.length;
        const gapNodes = items.filter(item => Number(item.pending_count || 0) > 0).length;
        const notifyNodes = items.filter(item => Number(item.ready_to_notify_count || 0) > 0).length;
        const notifiedNodes = items.filter(item => Number(item.notified_count || 0) > 0).length;
        const activeNodes = items.filter(item => Number(item.supplement_item_count || 0) > 0).length;
        const topGapItems = [...items]
          .filter(item => Number(item.pending_count || 0) > 0)
          .sort((a, b) => Number(b.pending_count || 0) - Number(a.pending_count || 0) || Number(b.supplement_item_count || 0) - Number(a.supplement_item_count || 0))
          .slice(0, 5);
        const topNotifyItems = [...items]
          .filter(item => Number(item.ready_to_notify_count || 0) > 0)
          .sort((a, b) => Number(b.ready_to_notify_count || 0) - Number(a.ready_to_notify_count || 0) || Number(b.pending_count || 0) - Number(a.pending_count || 0))
          .slice(0, 5);
        const renderTaskCards = (list, kind) => {
          if (!list.length) return '<div class="muted">当前没有推荐任务。</div>';
          return list.map(item => `
            <div class="term-block">
              <div class="term-title">${escHtml(item.node_key || '')}</div>
              <div class="status-bar" style="margin-top:4px;">
                <span class="status-chip">直达 ${escHtml(item.direct_sfx_count || 0)}</span>
                <span class="status-chip">整体 ${escHtml(item.composite_sfx_count || 0)}</span>
              </div>
              <div class="muted-sm">待补 ${escHtml(item.pending_count || 0)} ｜ 可通知 ${escHtml(item.ready_to_notify_count || 0)} ｜ 已通知 ${escHtml(item.notified_count || 0)}</div>
              <div class="muted-sm" style="margin-top:4px;">最近补充：${escHtml(item.last_supplement_at || '暂无')}</div>
              <button class="link-btn" style="margin-top:6px;" onclick="openGraphNode('${escHtml(item.node_key || '')}')">查看详情</button>
              <button class="link-btn" style="margin-top:6px;" onclick="openGraphMaintenance('${escHtml(item.node_key || '')}')">去图谱维护</button>
              ${kind === 'gap' ? `<button class="link-btn" style="margin-top:6px;" onclick="jumpToSupplementGroup('${escHtml(item.node_key || '')}', 'pending')">去补充</button>` : ''}
              ${kind === 'notify' ? `<button class="link-btn" style="margin-top:6px;" onclick="jumpToSupplementGroup('${escHtml(item.node_key || '')}', 'ready_to_notify')">去通知</button>` : ''}
            </div>
          `).join('');
        };
        const rows = items.map(item => `
          <tr>
            <td>${escHtml(item.genre || '')}</td>
            <td>
              <strong>${escHtml(item.verb_head || '')}</strong>
              <div class="muted-sm">${escHtml(item.node_key || '')}</div>
              <div class="status-bar" style="margin-top:6px;">
                <span class="status-chip">直达 ${escHtml(item.direct_sfx_count || 0)}</span>
                <span class="status-chip">整体 ${escHtml(item.composite_sfx_count || 0)}</span>
              </div>
            </td>
            <td>${escHtml(item.semantic_count || 0)}</td>
            <td>
              <div>${escHtml(item.sfx_count || 0)}</div>
              <div class="muted-sm">直达 ${escHtml(item.direct_sfx_count || 0)} / 整体 ${escHtml(item.composite_sfx_count || 0)}</div>
            </td>
            <td>
              <div class="muted-sm">补充单 ${escHtml(item.supplement_item_count || 0)}</div>
              <div class="muted-sm">已补 ${escHtml(item.covered_count || 0)} / 待补 ${escHtml(item.pending_count || 0)}</div>
              <div class="muted-sm">可通知 ${escHtml(item.ready_to_notify_count || 0)} / 已通知 ${escHtml(item.notified_count || 0)} / 待继续补齐 ${escHtml(item.incomplete_count || 0)}</div>
              <div class="progress-line"><div class="progress-fill" style="width:${Math.round(((item.completion_ratio || 0) * 100))}%"></div></div>
            </td>
            <td>
              <button class="link-btn" onclick="openGraphNode('${escHtml(item.node_key || '')}')">查看子级</button>
              <button class="link-btn" style="margin-top:6px;" onclick="openGraphMaintenance('${escHtml(item.node_key || '')}')">去图谱维护</button>
              <button class="link-btn" style="margin-top:6px;" onclick="jumpToSupplementGroup('${escHtml(item.node_key || '')}', 'pending')">去补充</button>
              ${Number(item.ready_to_notify_count || 0) > 0 ? `<button class="link-btn" style="margin-top:6px;" onclick="jumpToSupplementGroup('${escHtml(item.node_key || '')}', 'ready_to_notify')">去通知</button>` : ''}
            </td>
          </tr>
        `).join('');
        box.innerHTML = `
          <div class="stats" style="margin-bottom:10px;">
            <div class="stat clickable" onclick="applyGraphWorkbenchPreset('all')"><div class="k">当前父节点数</div><div class="v">${escHtml(totalNodes)}</div></div>
            <div class="stat clickable" onclick="applyGraphWorkbenchPreset('gap')"><div class="k">待补节点数</div><div class="v">${escHtml(gapNodes)}</div></div>
            <div class="stat clickable" onclick="applyGraphWorkbenchPreset('notify')"><div class="k">可通知节点数</div><div class="v">${escHtml(notifyNodes)}</div></div>
            <div class="stat clickable" onclick="applyGraphWorkbenchPreset('notified')"><div class="k">已通知节点数</div><div class="v">${escHtml(notifiedNodes)}</div></div>
            <div class="stat clickable" onclick="applyGraphWorkbenchPreset('active')"><div class="k">活跃节点数</div><div class="v">${escHtml(activeNodes)}</div></div>
          </div>
          <div class="graph-two-col" style="margin-bottom:10px;">
            <div class="card" style="padding:10px;">
              <h3 style="margin-bottom:8px;">今日优先补充</h3>
              ${renderTaskCards(topGapItems, 'gap')}
            </div>
            <div class="card" style="padding:10px;">
              <h3 style="margin-bottom:8px;">今日优先通知</h3>
              ${renderTaskCards(topNotifyItems, 'notify')}
            </div>
          </div>
          <div class="supp-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>赛道</th>
                  <th>父节点</th>
                  <th>语义子级数</th>
                  <th>音效子级数</th>
                  <th>业务状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>${rows}</tbody>
            </table>
          </div>
        `;
      }

      function renderGraphDetail(data) {
        const box = document.getElementById('graphDetail');
        if (!box) return;
        if (!data || data.detail) {
          box.innerHTML = '<div class="muted">当前没有可展示的父节点详情。</div>';
          return;
        }
        const directTerms = Array.isArray(data.direct_sfx_terms) ? data.direct_sfx_terms : [];
        const compositeTerms = Array.isArray(data.composite_sfx_terms) ? data.composite_sfx_terms : [];
        const semanticTerms = Array.isArray(data.semantic_terms) ? data.semantic_terms : [];
        const summary = data.summary || {};
        const supp = data.supplement_summary || {};
        const explain = data.business_explanation || {};
        const layers = data.graph_layers || {};
        const merged = layers.merged || {};
        const latestAt = supp.latest_created_at || supp.last_supplement_at || '';
        const chips = Object.entries(supp.status_counter || {}).map(([k, v]) => `<span class="status-chip">${escHtml(k)}：${escHtml(v)}</span>`).join('');
        const supplementItems = Array.isArray(supp.supplement_items) ? supp.supplement_items : [];
        const readyNotifyItems = supplementItems.filter(item => !!item.ready_to_notify && String(item.notification_status || '') !== '已通知');
        const whyReasons = [
          `命名规则：${explain.display_name_rule || '音效名（类型-赛道）'}`,
          `语义子级：${explain.semantic_edge_meaning || '用于扩展理解与召回。'}`,
          `直达音效边：${explain.direct_edge_meaning || '表示可直接命中的素材标签。'}`,
          `整体音效边：${explain.composite_edge_meaning || '表示整体动作音效。'}`,
          `运营提示：${explain.operator_hint || '优先补待补音效词。'}`,
        ];
        const renderAssetCards = (items) => {
          if (!Array.isArray(items) || !items.length) return '<div class="muted">暂无已入库素材。</div>';
          return `<div class="asset-grid">${items.map(item => `
            <div class="asset-card">
              <div class="asset-name">${escHtml(item.display_name || item.asset_label || item.file_name || '')}</div>
              <div class="asset-meta">
                原始名：${escHtml(item.asset_label || item.file_name || '未知')}<br/>
                文件：${escHtml(item.file_name || '未知')}<br/>
                路径：${escHtml(item.asset_file_path || '未记录')}
              </div>
              <div class="asset-badges">
                <span class="asset-badge">${escHtml(item.sfx_mode || '未分类')}</span>
                <span class="asset-badge">${escHtml(item.scope_label || data.genre || '未标赛道')}</span>
              </div>
            </div>
          `).join('')}</div>`;
        };
        const graphNodes = (items, kind, suffix = '') => {
          if (!Array.isArray(items) || !items.length) return '<div class="muted">暂无</div>';
          return items.map(item => `
            <div class="graph-node ${kind}">
              <div class="node-name">${escHtml(item)}${suffix}</div>
              <div class="node-meta">${kind === 'direct' ? '直达音效边' : kind === 'composite' ? '整体音效边' : '语义关联边'}</div>
            </div>
          `).join('');
        };
        const graphView = `
          <div class="graph-browser">
            <div class="graph-stage">
              <div class="graph-lane">
                <h5>语义子级</h5>
                ${graphNodes(semanticTerms, 'semantic')}
              </div>
              <div class="graph-center">
                <div class="node-title">${escHtml(data.node_key || '')}</div>
                <div class="node-sub">赛道：${escHtml(data.genre || '')} ｜ 动作词：${escHtml(data.verb_head || '')}</div>
                <div class="status-bar" style="justify-content:center; margin-top:10px;">
                  <span class="status-chip">直达 ${escHtml(summary.direct_sfx_count || 0)}</span>
                  <span class="status-chip">整体 ${escHtml(summary.composite_sfx_count || 0)}</span>
                  <span class="status-chip">待补 ${escHtml(supp.pending_count || 0)}</span>
                  <span class="status-chip">可通知 ${escHtml(supp.ready_to_notify_count || 0)}</span>
                </div>
              </div>
              <div class="graph-lane">
                <h5>直达音效边</h5>
                ${graphNodes(directTerms, 'direct')}
                <h5 style="margin-top:10px;">整体音效边</h5>
                ${graphNodes(compositeTerms, 'composite', '（整体）')}
              </div>
            </div>
          </div>
        `;
        const supplementRows = supplementItems.length ? supplementItems.map(item => `
          <tr>
            <td>${item.ready_to_notify && String(item.notification_status || '') !== '已通知' ? `<input type="checkbox" class="graph-notify-check" value="${escHtml(item.id || '')}" />` : ''}</td>
            <td>${escHtml(item.id || '')}</td>
            <td>
              <div>${escHtml(item.status || '')}</div>
              <div class="muted-sm">${item.ready_to_notify && String(item.notification_status || '') !== '已通知' ? '可通知' : (String(item.notification_status || '') === '已通知' ? '已通知用户' : '待继续补齐')}</div>
              <div class="muted-sm">提醒状态：${escHtml(item.notification_status || '未通知')}</div>
            </td>
            <td>${escHtml(item.user_phone || '')}</td>
            <td>${escHtml(item.sentence_excerpt || '')}</td>
            <td>${escHtml(formatReadableTime(item.created_at || ''))}</td>
            <td>
              <div class="muted-sm">已补 ${escHtml(item.covered_count || 0)} / 待补 ${escHtml(item.pending_count || 0)}</div>
              <div class="progress-line"><div class="progress-fill" style="width:${Math.round(((item.completion_ratio || 0) * 100))}%"></div></div>
              ${Array.isArray(item.pending_terms) && item.pending_terms.length ? `<div class="muted-sm" style="margin-top:6px;">缺口：${item.pending_terms.map(x => escHtml(x)).join('、')}</div>` : '<div class="muted-sm" style="margin-top:6px;">缺口：无</div>'}
            </td>
            <td>${Array.isArray(item.assets) && item.assets.length ? item.assets.map(x => escHtml(x.display_name || x.asset_label || x.file_name || '')).join('、') : '<span class="muted">暂无</span>'}</td>
          </tr>
        `).join('') : '';
        box.innerHTML = `
          <div class="graph-detail">
            <h4>父节点详情：${escHtml(data.node_key || '')}</h4>
            <div class="view-switch">
              <button class="view-chip active" type="button" onclick="toggleGraphDetailView('list', this)">列表视图</button>
              <button class="view-chip" type="button" onclick="toggleGraphDetailView('graph', this)">图谱视图</button>
            </div>
            <div class="explain-box">
              <h5>为什么是这个节点结构</h5>
              <ul class="reason-list">
                ${whyReasons.map(x => `<li>${escHtml(x)}</li>`).join('')}
              </ul>
            </div>
            <div class="status-bar">
              <span class="status-chip">赛道：${escHtml(data.genre || '')}</span>
              <span class="status-chip">动作词：${escHtml(data.verb_head || '')}</span>
              <span class="status-chip">语义子级：${escHtml(summary.semantic_count || 0)}</span>
              <span class="status-chip">音效子级：${escHtml(summary.sfx_count || 0)}</span>
              <span class="status-chip">直达音效：${escHtml(summary.direct_sfx_count || 0)}</span>
              <span class="status-chip">整体音效：${escHtml(summary.composite_sfx_count || 0)}</span>
              <span class="status-chip">补充单：${escHtml(supp.item_count || 0)}</span>
              <span class="status-chip">已补：${escHtml(supp.covered_count || 0)}</span>
              <span class="status-chip">待补：${escHtml(supp.pending_count || 0)}</span>
              <span class="status-chip">可通知：${escHtml(supp.ready_to_notify_count || 0)}</span>
              <span class="status-chip">已通知：${escHtml(supp.notified_count || 0)}</span>
              <span class="status-chip">待继续补齐：${escHtml(supp.incomplete_count || 0)}</span>
            </div>
            <div id="graphDetailListView">
              ${Array.isArray(merged.semantic_term_items) || Array.isArray(merged.sfx_term_items) ? `
                <div class="section-box">
                  <h5>图谱来源归属</h5>
                  <div class="muted-sm note-prefix">语义扩展词</div>
                  <div>${renderTermItems(merged.semantic_term_items || [])}</div>
                  <div class="muted-sm note-prefix" style="margin-top:8px;">直达音效来源(可下载)</div>
                  <div>${renderTermItems(merged.direct_sfx_term_items || [])}</div>
                  <div class="muted-sm note-prefix" style="margin-top:8px;">整体音效来源(可下载)</div>
                  <div>${renderTermItems(merged.composite_sfx_term_items || [], '（整体）')}</div>
                  <button class="link-btn" style="margin-top:8px;" onclick="openGraphMaintenance('${escHtml(data.node_key || '')}')">去图谱维护</button>
                </div>
              ` : ''}
              <div style="margin-top:10px;">
                <div class="muted-sm note-prefix">语义子级</div>
                <div>${renderPills(semanticTerms)}</div>
              </div>
              <div class="graph-two-col" style="margin-top:10px;">
                <div>
                  <div class="muted-sm note-prefix">直达音效边</div>
                  <div>${renderPills(directTerms)}</div>
                </div>
                <div>
                  <div class="muted-sm note-prefix">整体音效边</div>
                  <div>${renderPills(compositeTerms.map(x => `${x}（整体）`))}</div>
                </div>
              </div>
              <div class="graph-two-col">
                <div>
                  <div class="muted-sm note-prefix">已补音效子级</div>
                  <div>${renderPills(supp.covered_terms || [])}</div>
                </div>
                <div>
                  <div class="muted-sm note-prefix">待补音效子级</div>
                  <div>${renderPills(supp.pending_terms || data.sfx_terms || [])}</div>
                </div>
              </div>
              <div class="section-box">
                <h5>补充单状态</h5>
                <div class="status-bar">${chips || '<span class="muted">暂无补充单记录</span>'}</div>
                <div class="muted-sm note-prefix" style="margin-top:6px;">补齐进度：${escHtml(supp.covered_count || 0)}/${escHtml((supp.target_terms && supp.target_terms.length) || summary.sfx_count || 0)}</div>
                <div class="progress-line"><div class="progress-fill" style="width:${Math.round((((supp.completion_ratio) || 0) * 100))}%"></div></div>
                ${latestAt ? `<div class="muted-sm note-prefix" style="margin-top:6px;">最近补充单时间：${escHtml(latestAt)}</div>` : ''}
              </div>
              <div class="section-box">
                <h5>最近补充单与已入库素材</h5>
                ${supplementRows ? `
                  <div class="supp-table-wrap" style="margin-top:6px;">
                    <table>
                      <thead>
                        <tr>
                          <th>通知</th>
                          <th>ID</th>
                          <th>状态</th>
                          <th>用户手机号</th>
                          <th>原句片段</th>
                          <th>时间</th>
                          <th>补齐状态</th>
                          <th>已入库素材</th>
                        </tr>
                      </thead>
                      <tbody>${supplementRows}</tbody>
                    </table>
                  </div>
                ` : '<div class="muted" style="margin-top:6px;">当前没有补充单明细。</div>'}
              </div>
              <div class="section-box">
                <h5>父节点已入库素材</h5>
                ${renderAssetCards(
                  supplementItems.flatMap(item => Array.isArray(item.assets) ? item.assets : [])
                  .filter((x, idx, arr) => {
                    const key = `${x.display_name || ''}|${x.asset_file_path || ''}|${x.file_name || ''}`;
                    return arr.findIndex(y => `${y.display_name || ''}|${y.asset_file_path || ''}|${y.file_name || ''}` === key) === idx;
                  })
                )}
              </div>
              <div class="section-box">
                <h5>节点内可通知操作</h5>
                <div class="row" style="margin-top:6px;">
                  <div class="cell"><input id="graphNotifyIds" placeholder="可选：手动补充ID，逗号分隔" /></div>
                  <div class="cell"><button onclick="notifySupplementsFromGraph()">通知当前父节点已勾选补充单</button></div>
                </div>
                <div class="muted-sm note-prefix" style="margin-top:6px;">当前可通知补充单数：${escHtml(readyNotifyItems.length)}</div>
              </div>
            </div>
            <div id="graphDetailGraphView" style="display:none;">
              ${graphView}
              <div class="explain-box">
                <h5>图谱解释</h5>
                <ul class="reason-list">
                  ${whyReasons.map(x => `<li>${escHtml(x)}</li>`).join('')}
                </ul>
              </div>
            </div>
          </div>
        `;
      }

      function toggleGraphDetailView(mode, btn) {
        const list = document.getElementById('graphDetailListView');
        const graph = document.getElementById('graphDetailGraphView');
        if (!list || !graph) return;
        list.style.display = mode === 'list' ? '' : 'none';
        graph.style.display = mode === 'graph' ? '' : 'none';
        const chips = document.querySelectorAll('#graphDetail .view-chip');
        chips.forEach(chip => chip.classList.remove('active'));
        if (btn) btn.classList.add('active');
      }

      function currentGraphManageNodeKey() {
        const rawNodeKey = String(graphMaintenanceState?.rawData?.node_key || '').trim();
        if (rawNodeKey) return rawNodeKey;
        const verbSel = document.getElementById('graphManageVerb');
        const selected = (verbSel?.value || '').trim();
        if (selected) return selected;
        return (document.getElementById('graphManageNodeKey')?.value || '').trim();
      }

      function graphManageTargetGenreOptions(selectedValue = '') {
        const sections = Array.isArray(graphMaintenanceCatalogState?.genres) ? graphMaintenanceCatalogState.genres : [];
        const genres = sections
          .map(section => String(section.genre || '').trim())
          .filter(value => value);
        const uniqueGenres = Array.from(new Set(genres));
        return [
          '<option value="">请选择目标赛道</option>',
          ...uniqueGenres.map(genre => `<option value="${escHtml(genre)}" ${selectedValue === genre ? 'selected' : ''}>${escHtml(genre)}</option>`),
        ].join('');
      }

      async function loadGraphMaintenance(nodeKeyOverride = '') {
        const nodeKey = String(nodeKeyOverride || currentGraphManageNodeKey() || '').trim();
        if (!nodeKey) return show('graphManageOut', '请输入节点Key');
        syncGraphManageNodeInput(nodeKey);
        if (graphMaintenanceCatalogState?.genres?.length) renderGraphMaintenancePicker(nodeKey);
        const qs = new URLSearchParams({ node_key: nodeKey });
        const targetGenre = String(graphMaintenanceState?.ui?.overlapTargetGenre || '').trim();
        if (!nodeKey.includes('::') && targetGenre) qs.set('target_genre', targetGenre);
        const res = await fetch(`${base()}/action-graph/node-layers?${qs.toString()}`, { headers: h() });
        const data = await read(res);
        renderGraphMaintenance(data);
        show('graphManageOut', data);
      }

      function renderEditableTermChips(layer, termType, items, suffix = '') {
        const current = getLayerState(layer);
        const semanticSet = new Set((current.semantic_terms || []).map(x => String(x || '').trim()).filter(Boolean));
        let arr = Array.isArray(items) ? items : [];
        if (termType === 'composite_sfx_terms') {
          arr = arr.filter(term => !semanticSet.has(String(term || '').trim()));
        }
        if (!arr.length) return '<div class="muted">当前为空</div>';
        return `<div class="term-chip-row">${arr.map(term => `
          <span class="term-chip">
            ${escHtml(term)}${suffix}
            <button type="button" onclick="removeLayerTerm('${escHtml(layer)}','${escHtml(termType)}','${escHtml(term)}')">移除</button>
          </span>
        `).join('')}</div>`;
      }

      function renderSuggestionPills(layer, searchField, termType, sourceItems, selectedItems, suffix = '') {
        const current = getLayerState(layer);
        const selected = new Set((Array.isArray(selectedItems) ? selectedItems : []).map(x => String(x || '').trim()).filter(Boolean));
        if (termType === 'composite_sfx_terms') {
          (current.semantic_terms || []).forEach(term => selected.add(String(term || '').trim()));
        }
        const uniqueTerms = mergeUniqueTerms((Array.isArray(sourceItems) ? sourceItems : []).map(item => item && item.term));
        const available = filterSuggestionTerms(uniqueTerms.filter(term => !selected.has(term)), getLayerSearch(layer)?.[searchField] || '');
        const searchInputId = `search-${layer}-${searchField}`;
        const searchValue = getLayerSearch(layer)?.[searchField] || '';
        const controls = `
          <div class="mini-action-row">
            <input id="${searchInputId}" placeholder="搜索现成词" value="${escHtml(searchValue)}" oninput="setLayerSearch('${escHtml(layer)}','${escHtml(searchField)}', this.value)" />
          </div>
        `;
        if (!available.length) return `${controls}<div class="muted">当前没有可直接加入的现成词</div>`;
        return `${controls}<div class="suggestion-list">${available.map(term => `
          <button type="button" class="ghost-btn" onclick="addLayerTerm('${escHtml(layer)}','${escHtml(termType)}','${escHtml(term)}')">${escHtml(term)}${suffix}</button>
        `).join('')}</div>`;
      }

      function renderLayerEditor(layer, editorState, pools) {
        const semanticInputId = `custom-${layer}-semantic`;
        const directInputId = `custom-${layer}-direct`;
        const compositeInputId = `custom-${layer}-composite`;
        return `
          <div class="editor-group">
            <h6>语义子级</h6>
            ${renderEditableTermChips(layer, 'semantic_terms', editorState.semantic_terms || [])}
            <div class="muted-sm" style="margin-top:8px;">从现有合并结果快速加入</div>
            ${renderSuggestionPills(layer, 'semantic', 'semantic_terms', pools.mergedSemanticItems || [], editorState.semantic_terms || [])}
            <div class="mini-action-row">
              <input id="${semanticInputId}" placeholder="新增自定义语义词" />
              <button type="button" class="ghost-btn" onclick="addCustomLayerTerm('${escHtml(layer)}','semantic_terms','${semanticInputId}')">新增</button>
            </div>
          </div>
          <div class="editor-group">
            <h6>直达音效</h6>
            ${renderEditableTermChips(layer, 'direct_sfx_terms', editorState.direct_sfx_terms || [])}
            <div class="muted-sm" style="margin-top:8px;">从现有合并结果快速加入</div>
            ${renderSuggestionPills(layer, 'direct', 'direct_sfx_terms', pools.mergedDirectItems || [], editorState.direct_sfx_terms || [])}
            <div class="mini-action-row">
              <input id="${directInputId}" placeholder="新增自定义直达音效词" />
              <button type="button" class="ghost-btn" onclick="addCustomLayerTerm('${escHtml(layer)}','direct_sfx_terms','${directInputId}')">新增</button>
            </div>
          </div>
          <div class="editor-group">
            <h6>整体音效</h6>
            ${renderEditableTermChips(layer, 'composite_sfx_terms', editorState.composite_sfx_terms || [], '（整体）')}
            <div class="muted-sm" style="margin-top:8px;">从现有合并结果快速加入</div>
            ${renderSuggestionPills(layer, 'composite', 'composite_sfx_terms', pools.mergedCompositeItems || [], editorState.composite_sfx_terms || [], '（整体）')}
            <div class="mini-action-row">
              <input id="${compositeInputId}" placeholder="新增自定义整体音效词" />
              <button type="button" class="ghost-btn" onclick="addCustomLayerTerm('${escHtml(layer)}','composite_sfx_terms','${compositeInputId}')">新增</button>
            </div>
          </div>
        `;
      }

      function buildGraphMaintenanceExplanation(data) {
        const commonLayer = data.common_layer || {};
        const genreLayer = data.genre_layer || {};
        const merged = data.merged || {};
        const nodeLabel = String(data.node_key || data.verb_head || '').trim();
        const commonExists = Boolean(commonLayer.exists);
        const genreExists = Boolean(genreLayer.exists);
        const hasFallbackTerms = Boolean(merged.has_fallback_terms);
        let sourceType = '未标注';
        let sourceTone = 'fallback';
        let summary = '当前节点的出现原因还不够明确，建议先核对图谱来源后再上传素材。';
        let guidance = '上传前先确认该词是跨赛道复用，还是当前赛道特化，避免把素材传错层。';
        let operatorTip = '给运营的提示：请先核对来源类型，再决定素材应该按通用复用思路处理，还是按当前赛道特化处理。';

        if (commonExists && genreExists) {
          sourceType = '通用元数据 + 赛道特化';
          sourceTone = 'hybrid';
          summary = `${nodeLabel} 同时存在于通用元数据层和当前赛道特化层。它既有跨赛道可复用的基础边，也有当前赛道单独维护的强化边。`;
          guidance = '运营上传前请先区分：哪些素材是基础可复用素材，哪些素材明显带有当前赛道风格，再分别处理。';
          operatorTip = '给运营的提示：不要把两类素材混传。基础动作素材按可复用思路判断，赛道强化素材按当前赛道语境判断。';
        } else if (genreExists) {
          sourceType = '赛道特化';
          sourceTone = 'genre';
          summary = `${nodeLabel} 当前是赛道特化节点，说明它在本赛道下被单独维护，不是单纯引用通用元数据得到的公共节点。`;
          guidance = '运营上传时应优先按当前赛道语境理解，补充更贴近该赛道表达的素材。';
          operatorTip = '给运营的提示：来源类型为“赛道特化”时，请优先按当前赛道理解这条词，不要直接当成通用复用词上传。';
        } else if (commonExists) {
          sourceType = '通用元数据';
          sourceTone = 'common';
          summary = `${nodeLabel} 当前只在通用元数据层维护。它出现在这个赛道目录里，是因为当前赛道正在引用这条通用元数据，不代表它已经是当前赛道专属词。`;
          guidance = '';
          operatorTip = '';
        } else if (hasFallbackTerms) {
          sourceType = '保底生成';
          sourceTone = 'fallback';
          summary = `${nodeLabel} 当前能在前台结果中出现，是因为系统使用了保底生成词进行兜底推荐，并不表示它已经正式维护进图谱。`;
          guidance = '运营应先评估这条词是否值得沉淀到通用层或赛道层，再决定是否上传素材。';
          operatorTip = '给运营的提示：来源类型为“保底生成”时，建议先判断是否要沉淀进图谱，再决定是否上传素材。';
        }

        if (hasFallbackTerms && (commonExists || genreExists)) {
          guidance += ' 同时，当前节点里还有部分保底词，说明前台已在使用，但元数据或赛道特化维护还未完全补齐。';
        }

        return {
          sourceType,
          sourceTone,
          summary,
          guidance,
          operatorTip,
        };
      }

      function renderGraphMaintenance(data) {
        const box = document.getElementById('graphManageBox');
        if (!box) return;
        if (!data || data.detail) {
          box.innerHTML = '<div class="muted">当前没有可维护的图谱节点。</div>';
          return;
        }
        graphMaintenanceState = {
          rawData: data,
          common_editor: graphMaintenanceState?.rawData?.node_key === data.node_key ? getLayerState('common') : buildLayerEditorState(data.common_layer || {}),
          genre_editor: graphMaintenanceState?.rawData?.node_key === data.node_key ? getLayerState('genre') : buildLayerEditorState(data.genre_layer || {}),
          search: graphMaintenanceState?.rawData?.node_key === data.node_key
            ? (graphMaintenanceState.search || { common: { semantic: '', direct: '', composite: '' }, genre: { semantic: '', direct: '', composite: '' } })
            : { common: { semantic: '', direct: '', composite: '' }, genre: { semantic: '', direct: '', composite: '' } },
          candidateSearch: graphMaintenanceState?.rawData?.node_key === data.node_key
            ? (graphMaintenanceState.candidateSearch || { semantic: '', sfx: '' })
            : { semantic: '', sfx: '' },
          ui: graphMaintenanceState?.rawData?.node_key === data.node_key
            ? (graphMaintenanceState.ui || { busyAction: '', feedbackText: '', feedbackLevel: 'info', promoteFeedbackText: '', demoteFeedbackText: '', demoteTargetGenre: '', inheritTargetGenre: '', overlapTargetGenre: '', nodeDeleteTargetGenre: '', promoteSelection: { semantic: [], sfx: [] }, demoteSelection: { semantic: [], sfx: [] }, overlapGenreSelection: { semantic: [], sfx: [] }, overlapCommonSelection: { semantic: [], sfx: [] }, overlapGenreFeedbackText: '', overlapCommonFeedbackText: '', commonSaveFeedbackText: '', genreSaveFeedbackText: '', nodeKeepOtherFeedbackText: '', nodeDeleteFeedbackText: '' })
            : { busyAction: '', feedbackText: '', feedbackLevel: 'info', inheritFeedbackText: '', promoteFeedbackText: '', demoteFeedbackText: '', demoteTargetGenre: '', inheritTargetGenre: '', overlapTargetGenre: '', nodeDeleteTargetGenre: '', promoteSelection: { semantic: [], sfx: [] }, demoteSelection: { semantic: [], sfx: [] }, overlapGenreSelection: { semantic: [], sfx: [] }, overlapCommonSelection: { semantic: [], sfx: [] }, overlapGenreFeedbackText: '', overlapCommonFeedbackText: '', commonSaveFeedbackText: '', genreSaveFeedbackText: '', nodeKeepOtherFeedbackText: '', nodeDeleteFeedbackText: '' },
        };
        const commonLayer = data.common_layer || {};
        const genreLayer = data.genre_layer || {};
        const merged = data.merged || {};
        const commonEditor = getLayerState('common');
        const genreEditor = getLayerState('genre');
        const isCommonOnlyNode = !String(data.genre || '').trim();
        const migrationCandidates = data.migration_candidates || {};
        const semanticCandidates = Array.isArray(migrationCandidates.semantic_terms) ? migrationCandidates.semantic_terms : [];
        const semanticCandidateTerms = new Set(semanticCandidates.map(item => String(item?.term || '').trim()).filter(Boolean));
        const sfxCandidates = (Array.isArray(migrationCandidates.sfx_terms) ? migrationCandidates.sfx_terms : [])
          .filter(item => !semanticCandidateTerms.has(String(item?.term || '').trim()));
        const demotionCandidates = data.demotion_candidates || {};
        const demoteSemanticCandidates = Array.isArray(demotionCandidates.semantic_terms) ? demotionCandidates.semantic_terms : [];
        const demoteSemanticTerms = new Set(demoteSemanticCandidates.map(item => String(item?.term || '').trim()).filter(Boolean));
        const demoteSfxCandidates = (Array.isArray(demotionCandidates.sfx_terms) ? demotionCandidates.sfx_terms : [])
          .filter(item => !demoteSemanticTerms.has(String(item?.term || '').trim()));
        const overlapCandidates = data.overlap_candidates || {};
        const overlapSemanticCandidates = Array.isArray(overlapCandidates.semantic_terms) ? overlapCandidates.semantic_terms : [];
        const overlapSemanticTerms = new Set(overlapSemanticCandidates.map(item => String(item?.term || '').trim()).filter(Boolean));
        const overlapSfxCandidates = (Array.isArray(overlapCandidates.sfx_terms) ? overlapCandidates.sfx_terms : [])
          .filter(item => !overlapSemanticTerms.has(String(item?.term || '').trim()));
        const hasFallbackTerms = Boolean(merged.has_fallback_terms);
        const feedback = graphMaintenanceState?.ui?.feedbackText || graphMaintenanceFeedback(data);
        const explanation = buildGraphMaintenanceExplanation(data);
        const busyAction = String(graphMaintenanceState?.ui?.busyAction || '');
        const inheritedOnlyInGenreView = !isCommonOnlyNode && !!commonLayer.exists && !genreLayer.exists;
        const inheritTargetGenre = String(graphMaintenanceState?.ui?.inheritTargetGenre || data.genre || '').trim();
        const inheritBlocked = isCommonOnlyNode
          ? (inheritTargetGenre ? isInheritanceBlocked(inheritTargetGenre, data.verb_head || '') : false)
          : isInheritanceBlocked(data.genre || '', data.verb_head || '');
        const inheritTargetHasGenreNode = isCommonOnlyNode
          ? Boolean((findGraphManageSection(inheritTargetGenre)?.items || []).find(item => String(item.verb_head || '').trim() === String(data.verb_head || '').trim())?.genre_exists)
          : Boolean(genreLayer.exists);
        const renderCandidateBlock = (items, termType, suffix = '', checkboxClass = 'graph-promote-check', selectionKey = 'promoteSelection') => {
          if (!items.length) return '<div class="muted">当前没有可迁移候选。</div>';
          const query = getCandidateSearch(termType);
          const sorted = [...items]
          .filter(item => !query || String(item.term || '').toLowerCase().includes(String(query || '').trim().toLowerCase()))
          .sort((a, b) => {
            const ap = String(a.priority || '') === 'high' ? 0 : 1;
            const bp = String(b.priority || '') === 'high' ? 0 : 1;
            return ap - bp || Number(b.genre_count || 0) - Number(a.genre_count || 0) || String(a.term || '').localeCompare(String(b.term || ''), 'zh-Hans-CN');
          });
          const searchId = `candidate-search-${termType}`;
          const controls = `
            <div class="mini-action-row">
              <input id="${searchId}" placeholder="搜索候选词" value="${escHtml(query)}" oninput="setCandidateSearch('${escHtml(termType)}', this.value)" />
            </div>
          `;
          if (!sorted.length) return `${controls}<div class="muted">没有匹配当前搜索条件的候选词</div>`;
          const high = sorted.filter(item => String(item.priority || '') === 'high');
          const normal = sorted.filter(item => String(item.priority || '') !== 'high');
          const renderList = (list, title) => !list.length ? '' : `
            <div class="candidate-section-title">${title}</div>
            ${list.map((item) => `
            <label class="term-block ${String(item.priority || '') === 'high' ? 'priority-high' : 'priority-normal'}" style="display:block;">
              <div style="display:flex; gap:8px; align-items:flex-start;">
                <input type="checkbox" class="${escHtml(checkboxClass)}" data-term-type="${escHtml(termType)}" value="${escHtml(item.term || '')}" ${isGraphCandidateSelected(selectionKey, termType, item.term || '') ? 'checked' : ''} onchange="setGraphCandidateSelection('${escHtml(selectionKey)}','${escHtml(termType)}','${escHtml(item.term || '')}', this.checked)" />
                <div>
                  <div class="term-title">${escHtml(item.term || '')}${suffix}</div>
                  <div class="status-bar" style="margin-top:4px;">
                    <span class="status-chip">${escHtml(termType === 'semantic' ? '语义子级' : '音效子级')}</span>
                    <span class="status-chip">${escHtml(String(item.priority || '') === 'high' ? '高优先' : '一般候选')}</span>
                  </div>
                  <div class="muted-sm">${escHtml(item.reason || '')}</div>
                  <div class="muted-sm">出现赛道数：${escHtml(item.genre_count || 0)}${Array.isArray(item.appears_in_genres) && item.appears_in_genres.length ? ` ｜ ${escHtml(item.appears_in_genres.join('、'))}` : ''}</div>
                </div>
              </div>
            </label>
            `).join('')}
          `;
          return `${controls}${renderList(high, '优先处理候选')} ${renderList(normal, '一般候选')}`.trim();
        };
        box.innerHTML = `
          <div class="graph-detail">
            <h4>维护节点：${escHtml(data.node_key || '')}</h4>
            <div class="layer-state-grid">
              <div class="layer-state-card ${commonLayer.exists ? 'active' : 'inactive'}">
                <div class="k">通用元数据</div>
                <div class="v">${commonLayer.exists ? '已配置' : '未配置'}</div>
                <div class="state-tip">${commonLayer.exists ? '当前节点已有跨赛道可复用的基础元数据。' : '当前节点还没有通用元数据底座，无法被各赛道统一复用。'}</div>
              </div>
              <div class="layer-state-card ${genreLayer.exists ? 'active' : 'inactive'}">
                <div class="k">赛道特化</div>
                <div class="v">${genreLayer.exists ? '已配置' : '未配置'}</div>
                <div class="state-tip">${genreLayer.exists ? '当前赛道已对这个动作做了单独强化维护。' : '当前赛道还没有单独维护，当前展示来自通用元数据引用或保底结果。'}</div>
              </div>
            </div>
            <div class="status-bar">
              <span class="status-chip">合并语义：${escHtml((merged.summary || {}).semantic_count || 0)}</span>
              <span class="status-chip">合并音效：${escHtml((merged.summary || {}).sfx_count || 0)}</span>
            </div>
            ${feedback ? `<div class="result-banner">${escHtml(feedback)}</div>` : ''}
            <div class="explain-box">
              <h5>节点来源解释卡</h5>
              <div style="margin-bottom:8px;">
                <span class="explain-highlight ${escHtml(explanation.sourceTone || 'fallback')}">来源类型：${escHtml(explanation.sourceType || '未标注')}</span>
              </div>
              <ul class="reason-list">
                ${explanation.guidance ? `<li>${escHtml(explanation.guidance || '')}</li>` : ''}
              </ul>
              ${explanation.operatorTip ? `<div class="operator-tip">${escHtml(explanation.operatorTip || '')}</div>` : ''}
            </div>
            ${(commonLayer.exists && (isCommonOnlyNode || inheritedOnlyInGenreView)) ? `
            <div class="section-box" style="margin-top:10px;">
              <h5>从赛道删除与恢复</h5>
              <div class="muted-sm note-prefix">${isCommonOnlyNode ? '当前你打开的是通用动词。可在这里决定它是否继续出现在某个赛道里。对运营来说，这里的动作就是“从赛道删除”或“恢复到赛道”。' : '当前你打开的是赛道目录里的通用动词引用。这里可以直接把它从当前赛道删除，或重新恢复到当前赛道。'}</div>
              ${isCommonOnlyNode ? `
              <div class="row" style="margin-top:8px;">
                <div class="cell">
                  <select id="graphInheritTargetGenre" onchange="setGraphMaintenanceUiState({ inheritTargetGenre: this.value }); renderGraphMaintenance(graphMaintenanceState?.rawData || {});">
                    ${graphManageTargetGenreOptions(inheritTargetGenre)}
                  </select>
                </div>
                <div class="cell"><button onclick="toggleCurrentInheritance(true)">从当前赛道删除</button></div>
                <div class="cell"><button onclick="toggleCurrentInheritance(false)">恢复到当前赛道</button></div>
              </div>
              ` : `
              <div class="row" style="margin-top:8px;">
                <div class="cell"><button onclick="toggleCurrentInheritance(true)">从当前赛道删除</button></div>
                <div class="cell"><button onclick="toggleCurrentInheritance(false)">恢复到当前赛道</button></div>
              </div>
              `}
              <div class="muted-sm note-prefix" style="margin-top:6px;">当前状态：${escHtml(
                inheritBlocked
                  ? (inheritTargetHasGenreNode ? '已从当前赛道删除通用动词引用，但当前赛道仍有真实的赛道特化节点，所以它仍会显示。' : '已从当前赛道删除，当前赛道下拉框和推荐结果里将不再显示这个通用动词。')
                  : '当前赛道仍在使用这个通用动词'
              )}</div>
              <div style="margin-top:8px;">${inlineActionFeedback(graphMaintenanceState?.ui?.inheritFeedbackText || '', graphMaintenanceState?.ui?.feedbackLevel || 'info')}</div>
            </div>
            ` : ''}
            <div class="section-box">
              <h5>前台合并结果（方案 A）</h5>
              ${hasFallbackTerms ? `<div class="result-banner">当前前台结果里包含“保底生成”词，说明这部分词已经被系统兜底用于推荐，但还没有正式沉淀进通用层或赛道层图谱。</div>` : ''}
              <div class="muted-sm">语义扩展词</div>
              <div>${renderTermItems(merged.semantic_term_items || [])}</div>
              <div class="muted-sm" style="margin-top:8px;">直达音效来源(可下载)</div>
              <div>${renderTermItems(merged.direct_sfx_term_items || [])}</div>
              <div class="muted-sm" style="margin-top:8px;">整体音效来源(可下载)</div>
              <div>${renderTermItems(merged.composite_sfx_term_items || [], '（整体）')}</div>
            </div>
            ${isCommonOnlyNode ? `
            <div class="section-box" style="margin-top:10px;">
              <h5>补入通用元数据候选</h5>
              <div class="muted-sm note-prefix">当前节点是通用元数据视角，不适用“补入通用元数据”操作。</div>
            </div>
            ` : `
            <div class="section-box" style="margin-top:10px;">
              <h5>补入通用元数据候选</h5>
              <div class="muted-sm note-prefix">这里列的是“当前只在赛道特化层存在、还没进入通用元数据层”的词。勾选后可补入通用元数据，帮助把历史数据整理成可复用底座。</div>
              ${semanticCandidates.length || sfxCandidates.length ? `
              ${busyAction === 'promote' ? `<div class="result-banner">正在执行迁移，请稍候。页面刷新后会显示是否已迁移成功。</div>` : ''}
              <div class="graph-two-col" style="margin-top:8px;">
                <div>
                  <div class="muted-sm">语义子级候选</div>
                  ${renderCandidateBlock(semanticCandidates, 'semantic', '', 'graph-promote-check', 'promoteSelection')}
                </div>
                <div>
                  <div class="muted-sm">音效子级候选</div>
                  ${renderCandidateBlock(sfxCandidates, 'sfx', '', 'graph-promote-check', 'promoteSelection')}
                </div>
              </div>
              <div class="row" style="margin-top:8px;">
                <div class="cell">
                  <select id="graphPromoteMode">
                    <option value="keep">补入通用元数据，并保留赛道特化</option>
                    <option value="move">补入通用元数据，并从赛道特化移除</option>
                  </select>
                </div>
                <div class="cell"><button id="graphPromoteBtn" onclick="promoteSelectedTermsToCommon()" ${busyAction === 'promote' ? 'disabled' : ''}>${busyAction === 'promote' ? '处理中...' : '补入通用元数据'}</button></div>
                <div class="cell">${inlineActionFeedback(graphMaintenanceState?.ui?.promoteFeedbackText || '', graphMaintenanceState?.ui?.feedbackLevel || 'info')}</div>
              </div>
              ` : `<div class="muted-sm note-prefix">当前没有可补入通用元数据的候选词。</div>`}
            </div>
            `}
            <div class="section-box" style="margin-top:10px;">
              <h5>赛道分配与特化候选</h5>
              <div class="muted-sm note-prefix">${isCommonOnlyNode ? '这里列的是“当前只在通用元数据层存在”的词。请先选择目标赛道，再把这些词分配到对应赛道；如有需要，再继续做赛道特化。' : '这里列的是“当前只在通用元数据层存在、还没进入当前赛道特化层”的词。勾选后可分配到当前赛道，帮助把过于宽泛的通用词下沉成赛道特化边。'}</div>
              ${demoteSemanticCandidates.length || demoteSfxCandidates.length ? `
              ${busyAction === 'demote' ? `<div class="result-banner">正在执行赛道分配，请稍候。页面刷新后会显示是否已分配成功。</div>` : ''}
              <div class="graph-two-col" style="margin-top:8px;">
                <div>
                  <div class="muted-sm">语义子级候选</div>
                  ${renderCandidateBlock(demoteSemanticCandidates, 'semantic', '', 'graph-demote-check', 'demoteSelection')}
                </div>
                <div>
                  <div class="muted-sm">音效子级候选</div>
                  ${renderCandidateBlock(demoteSfxCandidates, 'sfx', '', 'graph-demote-check', 'demoteSelection')}
                </div>
              </div>
              <div class="row" style="margin-top:8px;">
                ${isCommonOnlyNode ? `
                <div class="cell">
                  <select id="graphDemoteTargetGenre" onchange="setGraphMaintenanceUiState({ demoteTargetGenre: this.value });">
                    ${graphManageTargetGenreOptions(String(graphMaintenanceState?.ui?.demoteTargetGenre || ''))}
                  </select>
                </div>
                ` : ''}
                <div class="cell">
                  <select id="graphDemoteMode">
                    <option value="keep">分配到赛道，并保留通用元数据</option>
                    <option value="move">分配到赛道，并从通用元数据移除</option>
                  </select>
                </div>
                <div class="cell"><button id="graphDemoteBtn" onclick="demoteSelectedTermsToGenre()" ${busyAction === 'demote' ? 'disabled' : ''}>${busyAction === 'demote' ? '处理中...' : '分配到赛道'}</button></div>
                <div class="cell">${inlineActionFeedback(graphMaintenanceState?.ui?.demoteFeedbackText || '', graphMaintenanceState?.ui?.feedbackLevel || 'info')}</div>
              </div>
              ` : `<div class="muted-sm note-prefix">当前没有可分配到赛道的候选词。</div>`}
            </div>
            <div class="section-box" style="margin-top:10px;">
              <h5>重叠词清理</h5>
              <div class="muted-sm">${isCommonOnlyNode ? '请先选择目标赛道。' : '可清理当前重叠词。'}</div>
              ${isCommonOnlyNode ? `
              <div class="row" style="margin-top:8px;">
                <div class="cell">
                  <select id="graphOverlapTargetGenre" onchange="setGraphMaintenanceUiState({ overlapTargetGenre: this.value }); loadGraphMaintenance(currentGraphManageNodeKey());">
                    ${graphManageTargetGenreOptions(String(graphMaintenanceState?.ui?.overlapTargetGenre || ''))}
                  </select>
                </div>
              </div>
              ` : ''}
              ${overlapSemanticCandidates.length || overlapSfxCandidates.length ? `
              <div class="graph-two-col" style="margin-top:8px;">
                <div>
                  <div class="muted-sm">语义重叠词</div>
                  ${renderCandidateBlock(overlapSemanticCandidates, 'semantic', '', 'graph-overlap-check', isCommonOnlyNode ? 'overlapCommonSelection' : 'overlapGenreSelection')}
                </div>
                <div>
                  <div class="muted-sm">音效重叠词</div>
                  ${renderCandidateBlock(overlapSfxCandidates, 'sfx', '', 'graph-overlap-check', isCommonOnlyNode ? 'overlapCommonSelection' : 'overlapGenreSelection')}
                </div>
              </div>
              ${isCommonOnlyNode ? `
              <div class="row" style="margin-top:8px;">
                <div class="cell"><button onclick="removeOverlapTerms('common')" ${busyAction === 'overlap-common' ? 'disabled' : ''}>${busyAction === 'overlap-common' ? '处理中...' : '从通用元数据移除重叠词，只保留赛道特化'}</button></div>
                <div class="cell">${inlineActionFeedback(graphMaintenanceState?.ui?.overlapCommonFeedbackText || '', graphMaintenanceState?.ui?.feedbackLevel || 'info')}</div>
              </div>
              ` : `
              <div class="row" style="margin-top:8px;">
                <div class="cell"><button onclick="removeOverlapTerms('genre')" ${busyAction === 'overlap-genre' ? 'disabled' : ''}>${busyAction === 'overlap-genre' ? '处理中...' : '从赛道特化移除重叠词，只保留通用元数据'}</button></div>
                <div class="cell">${inlineActionFeedback(graphMaintenanceState?.ui?.overlapGenreFeedbackText || '', graphMaintenanceState?.ui?.feedbackLevel || 'info')}</div>
              </div>
              `}
              ` : `${isCommonOnlyNode ? `
              <div class="muted-sm note-prefix">当前目标赛道下还没有可清理的重叠词。若你已经选择目标赛道，可直接点击下方按钮尝试执行同步清理。</div>
              <div class="row" style="margin-top:8px;">
                <div class="cell"><button onclick="removeOverlapTerms('common')" ${busyAction === 'overlap-common' ? 'disabled' : ''}>${busyAction === 'overlap-common' ? '处理中...' : '从通用元数据移除重叠词，只保留赛道特化'}</button></div>
                <div class="cell">${inlineActionFeedback(graphMaintenanceState?.ui?.overlapCommonFeedbackText || '', graphMaintenanceState?.ui?.feedbackLevel || 'info')}</div>
              </div>
              ` : `<div class="muted-sm note-prefix">当前没有通用层与赛道层重叠的词。</div>`}`}
            </div>
            <div class="section-box" style="margin-top:10px;">
              <h5>直接删除动作词</h5>
              <div class="muted-sm note-prefix">这里提供直接删除通用动词或赛道动词的入口。删除赛道里的通用引用词，建议优先使用上方“从赛道删除与恢复”；这里更适合删除真正建错的通用节点或赛道特化节点。</div>
              <div class="mini-action-row" style="margin-top:8px;">
                ${commonLayer.exists ? `<button class="ghost-btn" onclick="deleteCurrentCommonVerb()">${isCommonOnlyNode ? '删除通用动词' : '删除通用动词（保留当前赛道）'}</button>` : ''}
                ${(genreLayer.exists || (!isCommonOnlyNode && !Boolean(rawData?.genre_layer?.exists))) ? `<button class="ghost-btn" onclick="deleteCurrentGenreVerb()">${genreLayer.exists ? '删除赛道动词' : '删除未保存赛道草稿'}</button>` : ''}
              </div>
              <div style="margin-top:8px;">${inlineActionFeedback((graphMaintenanceState?.ui?.nodeKeepOtherFeedbackText || graphMaintenanceState?.ui?.nodeDeleteFeedbackText || ''), graphMaintenanceState?.ui?.feedbackLevel || 'info')}</div>
            </div>
            <div class="section-box" style="margin-top:10px;">
              <h5>运营维护顺序</h5>
              <div class="muted-sm note-prefix">建议按这条顺序操作：先确认通用元数据是否成立，再决定当前赛道是否引用，最后才做赛道特化。只有系统里确实没有现成词时，再用“新增自定义词”补充。</div>
            </div>
            <div class="graph-two-col" style="margin-top:10px;">
              <div class="section-box">
                <h5>通用元数据层</h5>
                <div class="muted-sm note-prefix">这里维护跨赛道可复用的基础动作元数据，是所有赛道分配和复用的底座。</div>
                ${renderLayerEditor('common', commonEditor, {
                  mergedSemanticItems: merged.semantic_term_items || [],
                  mergedDirectItems: merged.direct_sfx_term_items || [],
                  mergedCompositeItems: merged.composite_sfx_term_items || [],
                })}
                <div class="row" style="margin-top:8px;">
                  <div class="cell"><button onclick="saveGraphLayer('common')" ${busyAction === 'save-common' ? 'disabled' : ''}>${busyAction === 'save-common' ? '保存中...' : '保存通用元数据'}</button></div>
                  <div class="cell">${inlineActionFeedback(graphMaintenanceState?.ui?.commonSaveFeedbackText || '', graphMaintenanceState?.ui?.feedbackLevel || 'info')}</div>
                </div>
              </div>
              <div class="section-box">
                <h5>赛道特化维护区</h5>
                <div class="muted-sm note-prefix">这里维护当前赛道独有的强化内容。只有确认当前赛道需要不同表达时，再在这里补充。</div>
                ${renderLayerEditor('genre', genreEditor, {
                  mergedSemanticItems: merged.semantic_term_items || [],
                  mergedDirectItems: merged.direct_sfx_term_items || [],
                  mergedCompositeItems: merged.composite_sfx_term_items || [],
                })}
                <div class="row" style="margin-top:8px;">
                  <div class="cell"><button onclick="saveGraphLayer('genre')" ${busyAction === 'save-genre' ? 'disabled' : ''}>${busyAction === 'save-genre' ? '保存中...' : '保存赛道特化'}</button></div>
                  <div class="cell">${inlineActionFeedback(graphMaintenanceState?.ui?.genreSaveFeedbackText || '', graphMaintenanceState?.ui?.feedbackLevel || 'info')}</div>
                </div>
              </div>
            </div>
            <div class="muted note-prefix" style="margin-top:8px;">如果把某一层的标签全部移空再保存，该层配置会被删除。建议先确认是要清理通用元数据，还是只清理当前赛道特化。</div>
          </div>
        `;
      }

      async function promoteSelectedTermsToCommon() {
        const nodeKey = currentGraphManageNodeKey();
        if (!nodeKey) return show('graphManageOut', '请输入节点Key');
        const semantic_terms = getGraphCandidateSelection('promoteSelection', 'semantic').filter(Boolean);
        const sfx_terms = getGraphCandidateSelection('promoteSelection', 'sfx').filter(Boolean);
        if (!semantic_terms.length && !sfx_terms.length) {
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackText: '请先勾选要补入通用元数据的候选词，再执行操作。',
            feedbackLevel: 'warning',
            promoteFeedbackText: '请先勾选候选词',
            demoteFeedbackText: '',
          });
          renderGraphMaintenance(graphMaintenanceState?.rawData || {});
          return show('graphManageOut', '请先勾选要补入通用元数据的候选词');
        }
        const remove_from_genre = (document.getElementById('graphPromoteMode')?.value || 'keep') === 'move';
        setGraphMaintenanceUiState({
          busyAction: 'promote',
          feedbackText: `正在补入通用元数据：${[...semantic_terms, ...sfx_terms].join('、')}。请稍候...`,
          feedbackLevel: 'info',
          promoteFeedbackText: '补入中...',
          demoteFeedbackText: '',
          commonSaveFeedbackText: '',
          genreSaveFeedbackText: '',
        });
        renderGraphMaintenance(graphMaintenanceState.rawData || {});
        show('graphManageOut', {
          ok: true,
          message: '正在补入通用元数据，请稍候…',
          node_key: nodeKey,
          semantic_terms,
          sfx_terms,
          remove_from_genre,
        });
        try {
          const res = await fetch(`${base()}/action-graph/promote-to-common`, {
            method: 'POST',
            headers: h({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ node_key: nodeKey, semantic_terms, sfx_terms, remove_from_genre }),
          });
          const data = await read(res);
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackText: res.ok ? graphMaintenanceFeedback(data) : (data?.detail || data?.message || '补入通用元数据失败，请检查后重试。'),
            feedbackLevel: res.ok ? 'success' : 'error',
            promoteFeedbackText: res.ok ? '补入通用元数据已完成' : '补入通用元数据失败',
            demoteFeedbackText: '',
            promoteSelection: { semantic: [], sfx: [] },
          });
          renderGraphMaintenance(data);
          show('graphManageOut', data);
          if (!res.ok) return;
          await loadGraphMaintenanceCatalog(nodeKey);
          const actionNodeKey = (document.getElementById('actionNodeKey').value || '').trim();
          if (actionNodeKey === nodeKey) await queryActionGraphNode();
          await browseActionGraphNodes();
        } catch (err) {
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackText: `补入通用元数据失败：${err && err.message ? err.message : String(err || 'unknown error')}`,
            feedbackLevel: 'error',
            promoteFeedbackText: '补入通用元数据失败',
            demoteFeedbackText: '',
          });
          renderGraphMaintenance(graphMaintenanceState.rawData || {});
          show('graphManageOut', {
            ok: false,
            detail: `补入通用元数据失败：${err && err.message ? err.message : String(err || 'unknown error')}`,
            node_key: nodeKey,
          });
        }
      }

      async function demoteSelectedTermsToGenre() {
        const nodeKey = currentGraphManageNodeKey();
        if (!nodeKey) return show('graphManageOut', '请输入节点Key');
        const rawData = graphMaintenanceState?.rawData || {};
        const isCommonOnlyNode = !String(rawData.genre || '').trim();
        const targetGenre = String(document.getElementById('graphDemoteTargetGenre')?.value || graphMaintenanceState?.ui?.demoteTargetGenre || rawData.genre || '').trim();
        const semantic_terms = getGraphCandidateSelection('demoteSelection', 'semantic').filter(Boolean);
        const sfx_terms = getGraphCandidateSelection('demoteSelection', 'sfx').filter(Boolean);
        if (isCommonOnlyNode && !targetGenre) {
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackText: '请先选择要分配到的目标赛道，再执行操作。',
            feedbackLevel: 'warning',
            promoteFeedbackText: '',
            demoteFeedbackText: '请先选择目标赛道',
            demoteTargetGenre: '',
          });
          renderGraphMaintenance(graphMaintenanceState?.rawData || {});
          return show('graphManageOut', '请先选择要分配到的目标赛道');
        }
        if (!semantic_terms.length && !sfx_terms.length) {
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackText: '请先勾选要分配到赛道的候选词，再执行操作。',
            feedbackLevel: 'warning',
            promoteFeedbackText: '',
            demoteFeedbackText: '请先勾选候选词',
            demoteTargetGenre: targetGenre,
          });
          renderGraphMaintenance(graphMaintenanceState?.rawData || {});
          return show('graphManageOut', '请先勾选要分配到赛道的候选词');
        }
        const remove_from_common = (document.getElementById('graphDemoteMode')?.value || 'keep') === 'move';
        setGraphMaintenanceUiState({
          busyAction: 'demote',
          feedbackText: `正在分配到赛道：${[...semantic_terms, ...sfx_terms].join('、')}。请稍候...`,
          feedbackLevel: 'info',
          promoteFeedbackText: '',
          demoteFeedbackText: '赛道分配中...',
          demoteTargetGenre: targetGenre,
          commonSaveFeedbackText: '',
          genreSaveFeedbackText: '',
        });
        renderGraphMaintenance(graphMaintenanceState.rawData || {});
        show('graphManageOut', {
          ok: true,
          message: '正在执行赛道分配，请稍候…',
          node_key: nodeKey,
          target_genre: targetGenre,
          semantic_terms,
          sfx_terms,
          remove_from_common,
        });
        try {
          const res = await fetch(`${base()}/action-graph/demote-to-genre`, {
            method: 'POST',
            headers: h({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ node_key: nodeKey, target_genre: targetGenre, semantic_terms, sfx_terms, remove_from_common }),
          });
          const data = await read(res);
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackText: res.ok ? graphMaintenanceFeedback(data) : (data?.detail || data?.message || '赛道分配失败，请检查后重试。'),
            feedbackLevel: res.ok ? 'success' : 'error',
            promoteFeedbackText: '',
            demoteFeedbackText: res.ok ? '赛道分配已完成' : '赛道分配失败',
            demoteTargetGenre: targetGenre,
            demoteSelection: { semantic: [], sfx: [] },
          });
          renderGraphMaintenance(data);
          show('graphManageOut', data);
          if (!res.ok) return;
          const nextNodeKey = String(data?.node_key || (targetGenre ? `${targetGenre}::${String(rawData.verb_head || '').trim()}` : nodeKey) || '').trim();
          if (nextNodeKey) {
            syncGraphManageNodeInput(nextNodeKey);
            await loadGraphMaintenanceCatalog(nextNodeKey);
            await loadGraphMaintenance(nextNodeKey);
          } else {
            await loadGraphMaintenanceCatalog(nodeKey);
          }
          const actionNodeKey = (document.getElementById('actionNodeKey').value || '').trim();
          if (actionNodeKey === nodeKey || actionNodeKey === nextNodeKey) await queryActionGraphNode();
          await browseActionGraphNodes();
        } catch (err) {
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackText: `赛道分配失败：${err && err.message ? err.message : String(err || 'unknown error')}`,
            feedbackLevel: 'error',
            promoteFeedbackText: '',
            demoteFeedbackText: '赛道分配失败',
            demoteTargetGenre: targetGenre,
          });
          renderGraphMaintenance(graphMaintenanceState.rawData || {});
          show('graphManageOut', {
            ok: false,
            detail: `赛道分配失败：${err && err.message ? err.message : String(err || 'unknown error')}`,
            node_key: nodeKey,
          });
        }
      }

      async function removeOverlapTerms(removeFromLayer) {
        const nodeKey = currentGraphManageNodeKey();
        if (!nodeKey) return show('graphManageOut', '请输入节点Key');
        const isCommonOnlyNode = !String(graphMaintenanceState?.rawData?.genre || '').trim();
        const selectionKey = isCommonOnlyNode ? 'overlapCommonSelection' : 'overlapGenreSelection';
        const targetGenre = String(document.getElementById('graphOverlapTargetGenre')?.value || graphMaintenanceState?.ui?.overlapTargetGenre || '').trim();
        const semantic_terms = getGraphCandidateSelection(selectionKey, 'semantic').filter(Boolean);
        const sfx_terms = getGraphCandidateSelection(selectionKey, 'sfx').filter(Boolean);
        if (isCommonOnlyNode && !targetGenre) {
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackText: '请先选择目标赛道，再清理重叠词。',
            feedbackLevel: 'warning',
            overlapCommonFeedbackText: '请先选择目标赛道',
          });
          renderGraphMaintenance(graphMaintenanceState?.rawData || {});
          return show('graphManageOut', '请先选择目标赛道');
        }
        if (!semantic_terms.length && !sfx_terms.length) {
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackText: '请先勾选要清理的重叠词。',
            feedbackLevel: 'warning',
            overlapGenreFeedbackText: removeFromLayer === 'genre' ? '请先勾选重叠词' : '',
            overlapCommonFeedbackText: removeFromLayer === 'common' ? '请先勾选重叠词' : '',
          });
          renderGraphMaintenance(graphMaintenanceState?.rawData || {});
          return show('graphManageOut', '请先勾选要清理的重叠词');
        }
        const busyAction = removeFromLayer === 'genre' ? 'overlap-genre' : 'overlap-common';
        setGraphMaintenanceUiState({
          busyAction,
          feedbackText: `正在清理重叠词：${[...semantic_terms, ...sfx_terms].join('、')}。请稍候...`,
          feedbackLevel: 'info',
          overlapGenreFeedbackText: removeFromLayer === 'genre' ? '处理中...' : '',
          overlapCommonFeedbackText: removeFromLayer === 'common' ? '处理中...' : '',
        });
        renderGraphMaintenance(graphMaintenanceState?.rawData || {});
        try {
          const res = await fetch(`${base()}/action-graph/remove-overlap`, {
            method: 'POST',
            headers: h({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ node_key: nodeKey, target_genre: targetGenre, semantic_terms, sfx_terms, remove_from_layer: removeFromLayer }),
          });
          const data = await read(res);
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackText: res.ok ? graphMaintenanceFeedback(data) : (data?.detail || data?.message || '重叠词清理失败，请检查后重试。'),
            feedbackLevel: res.ok ? 'success' : 'error',
            overlapGenreFeedbackText: removeFromLayer === 'genre' ? (res.ok ? '赛道层清理已完成' : '赛道层清理失败') : '',
            overlapCommonFeedbackText: removeFromLayer === 'common' ? (res.ok ? '通用层清理已完成' : '通用层清理失败') : '',
            overlapGenreSelection: { semantic: [], sfx: [] },
            overlapCommonSelection: { semantic: [], sfx: [] },
          });
          renderGraphMaintenance(data);
          show('graphManageOut', data);
          if (!res.ok) return;
          await loadGraphMaintenanceCatalog(nodeKey);
          const actionNodeKey = (document.getElementById('actionNodeKey').value || '').trim();
          if (actionNodeKey === nodeKey) await queryActionGraphNode();
          await browseActionGraphNodes();
        } catch (err) {
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackText: `重叠词清理失败：${err && err.message ? err.message : String(err || 'unknown error')}`,
            feedbackLevel: 'error',
            overlapGenreFeedbackText: removeFromLayer === 'genre' ? '赛道层清理失败' : '',
            overlapCommonFeedbackText: removeFromLayer === 'common' ? '通用层清理失败' : '',
          });
          renderGraphMaintenance(graphMaintenanceState?.rawData || {});
          show('graphManageOut', {
            ok: false,
            detail: `重叠词清理失败：${err && err.message ? err.message : String(err || 'unknown error')}`,
            node_key: nodeKey,
          });
        }
      }

      async function deleteGraphNode(action) {
        const nodeKey = currentGraphManageNodeKey();
        if (!nodeKey) return show('graphManageOut', '请输入节点Key');
        const rawData = graphMaintenanceState?.rawData || {};
        const isCommonOnlyNode = !String(rawData.genre || '').trim();
        const hasSavedCommonNode = Boolean(rawData?.common_layer?.exists);
        const hasSavedGenreNode = Boolean(rawData?.genre_layer?.exists);
        const targetGenre = String(
          document.getElementById('graphNodeDeleteTargetGenre')?.value
          || graphMaintenanceState?.ui?.nodeDeleteTargetGenre
          || graphMaintenanceState?.ui?.overlapTargetGenre
          || rawData.genre
          || ''
        ).trim();
        const confirmText = action === 'delete_common_keep_genre'
          ? `确认删除通用元数据节点“${String(rawData.verb_head || nodeKey || '').trim()}”吗？该操作会保留你选择赛道里的特化节点。`
          : action === 'delete_genre_keep_common'
            ? `确认删除当前赛道特化节点“${String(rawData.node_key || nodeKey || '').trim()}”吗？该操作会保留通用元数据。`
            : `确认删除当前层节点“${String(rawData.node_key || nodeKey || '').trim()}”吗？这会直接移除这一层下的语义词、直达音效和整体音效。`;
        if (!window.confirm(confirmText)) {
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackText: '已取消删除操作。',
            feedbackLevel: 'info',
            nodeKeepOtherFeedbackText: action === 'delete_current_layer' ? '' : '已取消删除操作',
            nodeDeleteFeedbackText: action === 'delete_current_layer' ? '已取消删除操作' : '',
          });
          renderGraphMaintenance(graphMaintenanceState?.rawData || {});
          return;
        }
        if (action === 'delete_current_layer' && !isCommonOnlyNode && !hasSavedGenreNode) {
          const clearedNodeKey = '';
          graphMaintenanceCatalogState.selectedNodeKey = '';
          syncGraphManageNodeInput('');
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackText: `已撤销未保存的赛道动作词草稿：${String(rawData.node_key || nodeKey || '').trim()}。`,
            feedbackLevel: 'success',
            nodeDeleteFeedbackText: '已撤销未保存草稿',
          });
          renderGraphMaintenance({
            ok: true,
            node_key: '',
            genre: '',
            compare_genre: '',
            verb_head: '',
            common_layer: { exists: false, semantic_terms: [], direct_sfx_terms: [], composite_sfx_terms: [] },
            genre_layer: { exists: false, semantic_terms: [], direct_sfx_terms: [], composite_sfx_terms: [] },
            merged: { summary: { semantic_count: 0, sfx_count: 0 }, semantic_term_items: [], direct_sfx_term_items: [], composite_sfx_term_items: [] },
          });
          show('graphManageOut', {
            ok: true,
            detail: '未保存的赛道动作词草稿已撤销',
            node_key: rawData.node_key || nodeKey,
            node_delete_action: action,
            deleted_layer: 'genre_draft',
          });
          renderGraphMaintenancePicker(clearedNodeKey);
          return;
        }
        if (action === 'delete_common_keep_genre' && !targetGenre) {
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackText: '请先选择要保留的目标赛道，再删除通用元数据节点。',
            feedbackLevel: 'warning',
            nodeKeepOtherFeedbackText: '请先选择目标赛道',
          });
          renderGraphMaintenance(graphMaintenanceState?.rawData || {});
          return show('graphManageOut', '请先选择要保留的目标赛道');
        }

        const isKeepOther = action === 'delete_genre_keep_common' || action === 'delete_common_keep_genre';
        const busyAction = isKeepOther ? 'delete-keep-other' : 'delete-current-layer';
        const runningText = action === 'delete_genre_keep_common'
          ? '正在删除当前赛道特化节点，并保留通用元数据...'
          : action === 'delete_common_keep_genre'
            ? '正在删除通用元数据节点，并保留当前赛道特化...'
            : `正在删除当前${isCommonOnlyNode ? '通用元数据层' : '赛道特化层'}节点...`;
        setGraphMaintenanceUiState({
          busyAction,
          feedbackText: runningText,
          feedbackLevel: 'info',
          nodeKeepOtherFeedbackText: isKeepOther ? '处理中...' : '',
          nodeDeleteFeedbackText: isKeepOther ? '' : '处理中...',
        });
        renderGraphMaintenance(graphMaintenanceState?.rawData || {});
        try {
          const res = await fetch(`${base()}/action-graph/node-delete`, {
            method: 'POST',
            headers: h({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ node_key: nodeKey, action, target_genre: targetGenre }),
          });
          const data = await read(res);
          const successText = graphMaintenanceFeedback(data);
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackText: res.ok ? successText : (data?.detail || data?.message || '节点级清理失败，请检查后重试。'),
            feedbackLevel: res.ok ? 'success' : 'error',
            nodeKeepOtherFeedbackText: isKeepOther ? (res.ok ? successText : '节点级清理失败') : '',
            nodeDeleteFeedbackText: isKeepOther ? '' : (res.ok ? successText : '删除当前层节点失败'),
            nodeDeleteTargetGenre: targetGenre,
          });
          renderGraphMaintenance(data);
          show('graphManageOut', data);
          if (!res.ok) return;
          const nextNodeKey = String(data?.result_node_key || data?.node_key || nodeKey).trim();
          if (nextNodeKey) {
            syncGraphManageNodeInput(nextNodeKey);
            await loadGraphMaintenanceCatalog(nextNodeKey);
            await loadGraphMaintenance(nextNodeKey);
          } else {
            await loadGraphMaintenanceCatalog(nodeKey);
            await loadGraphMaintenance(nodeKey);
          }
          const actionNodeKey = (document.getElementById('actionNodeKey').value || '').trim();
          if (actionNodeKey === nodeKey || actionNodeKey === nextNodeKey) await queryActionGraphNode();
          await browseActionGraphNodes();
        } catch (err) {
          const errorText = `节点级清理失败：${err && err.message ? err.message : String(err || 'unknown error')}`;
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackText: errorText,
            feedbackLevel: 'error',
            nodeKeepOtherFeedbackText: isKeepOther ? '节点级清理失败' : '',
            nodeDeleteFeedbackText: isKeepOther ? '' : '删除当前层节点失败',
            nodeDeleteTargetGenre: targetGenre,
          });
          renderGraphMaintenance(graphMaintenanceState?.rawData || {});
          show('graphManageOut', {
            ok: false,
            detail: errorText,
            node_key: nodeKey,
          });
        }
      }

      async function saveGraphLayer(layer) {
        const nodeKey = currentGraphManageNodeKey();
        if (!nodeKey) return show('graphManageOut', '请输入节点Key');
        const isCommon = layer === 'common';
        setGraphMaintenanceUiState({
          busyAction: isCommon ? 'save-common' : 'save-genre',
          feedbackLevel: 'info',
          commonSaveFeedbackText: isCommon ? '保存通用层中...' : '',
          genreSaveFeedbackText: isCommon ? '' : '保存赛道层中...',
          promoteFeedbackText: graphMaintenanceState?.ui?.promoteFeedbackText || '',
          demoteFeedbackText: graphMaintenanceState?.ui?.demoteFeedbackText || '',
          overlapGenreFeedbackText: graphMaintenanceState?.ui?.overlapGenreFeedbackText || '',
          overlapCommonFeedbackText: graphMaintenanceState?.ui?.overlapCommonFeedbackText || '',
        });
        renderGraphMaintenance(graphMaintenanceState?.rawData || {});
        const payloadData = buildLayerSavePayload(layer);
        const payload = {
          node_key: nodeKey,
          layer,
          semantic_terms: payloadData.semantic_terms,
          sfx_terms: payloadData.sfx_terms,
        };
        try {
          const res = await fetch(`${base()}/action-graph/node-layer`, {
            method: 'POST',
            headers: h({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(payload),
          });
          const data = await read(res);
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackLevel: res.ok ? 'success' : 'error',
            commonSaveFeedbackText: isCommon ? (res.ok ? '保存通用层已完成' : '保存通用层失败') : '',
            genreSaveFeedbackText: isCommon ? '' : (res.ok ? '保存赛道层已完成' : '保存赛道层失败'),
            promoteFeedbackText: graphMaintenanceState?.ui?.promoteFeedbackText || '',
            demoteFeedbackText: graphMaintenanceState?.ui?.demoteFeedbackText || '',
            overlapGenreFeedbackText: graphMaintenanceState?.ui?.overlapGenreFeedbackText || '',
            overlapCommonFeedbackText: graphMaintenanceState?.ui?.overlapCommonFeedbackText || '',
            feedbackText: res.ok ? graphMaintenanceFeedback(data) : (data?.detail || data?.message || '保存失败，请检查后重试。'),
          });
          show('graphManageOut', data);
          if (res.ok) {
            await loadGraphMaintenanceCatalog(nodeKey);
            await loadGraphMaintenance(String(data?.node_key || nodeKey).trim() || nodeKey);
          } else {
            renderGraphMaintenance(graphMaintenanceState?.rawData || {});
          }
        } catch (err) {
          setGraphMaintenanceUiState({
            busyAction: '',
            feedbackLevel: 'error',
            commonSaveFeedbackText: isCommon ? '保存通用层失败' : '',
            genreSaveFeedbackText: isCommon ? '' : '保存赛道层失败',
            promoteFeedbackText: graphMaintenanceState?.ui?.promoteFeedbackText || '',
            demoteFeedbackText: graphMaintenanceState?.ui?.demoteFeedbackText || '',
            overlapGenreFeedbackText: graphMaintenanceState?.ui?.overlapGenreFeedbackText || '',
            overlapCommonFeedbackText: graphMaintenanceState?.ui?.overlapCommonFeedbackText || '',
            feedbackText: `保存失败：${err && err.message ? err.message : String(err || 'unknown error')}`,
          });
          renderGraphMaintenance(graphMaintenanceState?.rawData || {});
          show('graphManageOut', {
            ok: false,
            detail: `保存失败：${err && err.message ? err.message : String(err || 'unknown error')}`,
            node_key: nodeKey,
            layer,
          });
        }
      }

      async function notifySupplementsFromGraph() {
        const checked = Array.from(document.querySelectorAll('.graph-notify-check:checked')).map(x => Number(x.value)).filter(x => Number.isFinite(x) && x > 0);
        const raw = (document.getElementById('graphNotifyIds').value || '').trim();
        const manual = raw.split(',').map(x => Number(x.trim())).filter(x => Number.isFinite(x) && x > 0);
        const item_ids = Array.from(new Set([...checked, ...manual]));
        if (!item_ids.length) return show('actionNeo4jOut', '请先勾选可通知补充单，或手动输入ID');
        const res = await fetch(`${base()}/ops/action-supplements/notify`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ item_ids }),
        });
        const data = await read(res);
        show('actionNeo4jOut', { ...data, message: '已将当前父节点下所选补充单的提醒状态更新为已通知。' });
        const nodeKey = (document.getElementById('actionNodeKey').value || '').trim();
        if (nodeKey) await queryActionGraphNode();
      }

      async function loadSupplements() {
        const days = (document.getElementById('suppDays').value || '30').trim();
        const status = (document.getElementById('suppStatus').value || '').trim();
        updateSupplementStatusHint();
        const qs = new URLSearchParams({ days });
        if (status) qs.set('status', status);
        const res = await fetch(`${base()}/ops/action-supplements?${qs.toString()}`, { headers: h() });
        const data = await read(res);
        latestSupplements = Array.isArray(data && data.items) ? data.items : [];
        const summaryBox = document.getElementById('suppSummary');
        if (summaryBox) summaryBox.innerHTML = renderStats((data && data.summary) || {});
        renderSupplementTable(latestSupplements);
        renderGraphPriorityTodo();
        await loadNotifyTargets();
        show('suppOut', data);
      }

      function renderSupplementTable(items) {
        const box = document.getElementById('suppTable');
        if (!box) return;
        if (!Array.isArray(items) || !items.length) {
          box.innerHTML = '<div class="muted">当前没有可处理的动作补充单。</div>';
          return;
        }
        const groups = new Map();
        for (const item of items) {
          const key = (item.parent_node && item.parent_node.node_key) || `${item.genre || ''}::${item.verb || ''}`;
          const current = groups.get(key) || { key, items: [], genre: item.genre || '', verb: item.verb || '', target_head: item.target_head || '' };
          current.items.push(item);
          groups.set(key, current);
        }
        const renderTermUpload = (item, term, idx, isComposite = false, sourceItem = null) => {
          const source = String(sourceItem && sourceItem.source || '').trim();
          const taskScope = String(item && item.task_scope || '').trim();
          const defaultScope = taskScope || supplementScopeDefault(source);
          const hint = taskScope
            ? {
                text: `当前这条补充单已经明确要求上传${String(item.task_scope_label || '').trim() || '指定版本'}素材。`,
                level: 'info',
              }
            : supplementScopeHint(source);
          const scopeDisabled = taskScope === 'common' || taskScope === 'genre';
          return `
          <div class="term-block">
            <div class="term-title">${escHtml(term || '未命名音效词')}${isComposite ? '（整体）' : ''}</div>
            <div class="muted-sm note-prefix">建议为该音效词单独上传一个可命中的素材文件。</div>
            <div class="status-bar" style="margin-top:6px;">
              <span class="status-chip">来源：${escHtml(sourceLabel(source || 'unknown'))}</span>
              ${item?.task_scope_label ? `<span class="status-chip">补充版本：${escHtml(item.task_scope_label)}</span>` : ''}
            </div>
            <div style="margin-top:6px;">${inlineActionFeedback(hint.text, hint.level)}</div>
            <select id="suppScope_${item.id}_${idx}" style="margin-top:6px;" ${scopeDisabled ? 'disabled' : ''}>
              <option value="" ${defaultScope ? '' : 'selected'}>请选择素材版本</option>
              <option value="common" ${defaultScope === 'common' ? 'selected' : ''}>${escHtml(term || '该词')}（通用）</option>
              <option value="genre" ${defaultScope === 'genre' ? 'selected' : ''}>${escHtml(term || '该词')}（${escHtml(item.target_genre || item.genre || '赛道')}）</option>
            </select>
            <input id="suppLabel_${item.id}_${idx}" placeholder="入库名称" value="${escHtml(term || '')}" style="margin-top:6px;" />
            <input id="suppFile_${item.id}_${idx}" type="file" style="margin-top:6px;" />
            <div class="row" style="margin-top:6px;">
              <div class="cell"><button id="suppMergeBtn_${item.id}_${idx}" onclick="mergeSupplementRow(${item.id}, ${idx}, '${escHtml(term || '')}')">上传并合并入库</button></div>
              <div class="cell" id="suppFeedback_${item.id}_${idx}"></div>
            </div>
          </div>
        `;
        };
        const renderRow = (item) => `
          <tr>
            <td><input type="checkbox" class="supp-check" value="${item.id}" /></td>
            <td>${item.id}</td>
            <td>${escHtml(item.user_phone || '')}</td>
            <td>${escHtml(item.genre || '')}</td>
            <td class="verb-cell">
              <div><strong>${escHtml(item.verb || '')}</strong></div>
              <div class="muted-sm">父级节点：${escHtml((item.parent_node && item.parent_node.node_key) || ((item.genre || '') + '::' + (item.verb || '')))}</div>
              <div class="muted-sm">补充版本：${escHtml(item.task_scope_label || '未标注版本')}</div>
            </td>
            <td>${escHtml(item.target_head || '')}</td>
            <td>${escHtml(item.sentence_excerpt || '')}</td>
            <td>
              <div class="muted-sm note-prefix">语义子级</div>
              <div>${renderPills((item.children && item.children.semantic_terms) || item.semantic_terms || [])}</div>
            </td>
            <td>
              ${(() => {
                const buckets = renderSupplementSfxBuckets(
                  item.children || {},
                  (item.children && item.children.sfx_terms) || item.sfx_terms || [],
                  (item.children && item.children.missing_sfx_terms) || item.missing_sfx_terms || []
                );
                return `
                  <div class="muted-sm note-prefix">音效子级</div>
                  <div>${buckets.allHtml}</div>
                  <div style="margin-top:6px;"><div class="muted-sm note-prefix">待补子级</div><div>${buckets.missingHtml}</div></div>
                  <div style="margin-top:6px;"><div class="muted-sm note-prefix">已补子级</div><div>${buckets.coveredHtml}</div></div>
                `;
              })()}
            </td>
            <td>
              <div>${escHtml(supplementStatusLabel(item.status || ''))}</div>
              <div class="muted-sm">${escHtml(item.status || '')}</div>
              <div class="muted-sm">提醒状态：${escHtml(item.notification_status || '未通知')}</div>
              <div class="progress-box">
                <div class="muted-sm">补齐进度 ${escHtml((item.progress && item.progress.covered_count) || 0)}/${escHtml((item.progress && item.progress.target_count) || 0)}</div>
                <div class="progress-line"><div class="progress-fill" style="width:${Math.round((((item.progress && item.progress.completion_ratio) || 0) * 100))}%"></div></div>
              </div>
            </td>
            <td>
              <div class="supp-actions">
                ${(() => {
                  const directTerms = ((item.children && item.children.missing_direct_sfx_terms) || []);
                  const compositeTerms = ((item.children && item.children.missing_composite_sfx_terms) || []);
                  const directTermItems = ((item.children && item.children.missing_direct_sfx_term_items) || []);
                  const compositeTermItems = ((item.children && item.children.missing_composite_sfx_term_items) || []);
                  const fullDirectTermItems = ((item.children && item.children.direct_sfx_term_items) || []);
                  const fullCompositeTermItems = ((item.children && item.children.composite_sfx_term_items) || []);
                  const directSourceMap = new Map([
                    ...fullDirectTermItems.map(entry => [normalizeCompositeTerm(String(entry && entry.term || '').trim()), entry]),
                    ...directTermItems.map(entry => [normalizeCompositeTerm(String(entry && entry.term || '').trim()), entry]),
                  ]);
                  const compositeSourceMap = new Map([
                    ...fullCompositeTermItems.map(entry => [normalizeCompositeTerm(String(entry && entry.term || '').trim()), entry]),
                    ...compositeTermItems.map(entry => [normalizeCompositeTerm(String(entry && entry.term || '').trim()), entry]),
                  ]);
                  if (directTerms.length || compositeTerms.length) {
                    return [
                      ...directTerms.map((term, idx) => renderTermUpload(item, term, idx, false, directSourceMap.get(normalizeCompositeTerm(String(term || '').trim())) || null)),
                      ...compositeTerms.map((term, idx) => renderTermUpload(item, term, directTerms.length + idx, true, compositeSourceMap.get(normalizeCompositeTerm(String(term || '').trim())) || null)),
                    ].join('');
                  }
                  return '<div class="muted-sm note-prefix">当前这条补充单已经没有待补词。若需替换已入库素材，请使用下方“已入库”信息定位后再做单独替换功能。</div>';
                })()}
                ${Array.isArray(item.merged_assets) && item.merged_assets.length ? `<div class="muted-sm">已入库：${item.merged_assets.map(x => escHtml(x.display_name || x.asset_label || x.file_name || '')).join('、')}</div>` : ''}
              </div>
            </td>
          </tr>
        `;
        const groupsHtml = Array.from(groups.values()).map(group => {
          const groupPending = group.items.reduce((n, x) => n + Number((x.progress && x.progress.pending_count) || 0), 0);
          const groupCovered = group.items.reduce((n, x) => n + Number((x.progress && x.progress.covered_count) || 0), 0);
          const rows = group.items.map(renderRow).join('');
          return `
            <div class="supp-group" id="${nodeKeyToId(group.key)}" data-node-key="${escHtml(group.key)}">
              <div class="supp-group-head">
                <div>
                  <div class="supp-group-title">${escHtml(group.key)}</div>
                  <div class="supp-group-meta">赛道：${escHtml(group.genre)} ｜ 动作词：${escHtml(group.verb)} ｜ 建议头词：${escHtml(group.target_head || group.verb)}</div>
                </div>
                <div>
                  <div class="supp-group-meta">补充单数：${group.items.length} ｜ 已补词：${groupCovered} ｜ 待补词：${groupPending}</div>
                  <button class="link-btn" style="margin-top:6px;" onclick="openGraphNode('${escHtml(group.key)}')">查看该父节点图谱</button>
                  <button class="link-btn" style="margin-top:6px;" onclick="jumpToSupplementGroup('${escHtml(group.key)}', 'pending')">定位当前补充组</button>
                </div>
              </div>
              <div class="supp-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>通知</th>
                      <th>ID</th>
                      <th>用户手机号</th>
                      <th>赛道</th>
                      <th>动作词</th>
                      <th>建议头词</th>
                      <th>原句片段</th>
                      <th>关联语义</th>
                      <th>待补音效词</th>
                      <th>状态 / 进度</th>
                      <th>逐词入库操作</th>
                    </tr>
                  </thead>
                  <tbody>${rows}</tbody>
                </table>
              </div>
            </div>
          `;
        }).join('');
        box.innerHTML = groupsHtml;
      }

      async function mergeSupplementRow(itemId, termIdx, fallbackLabel) {
        const current = latestSupplements.find(x => Number(x.id) === Number(itemId)) || null;
        const labelInput = document.getElementById(`suppLabel_${itemId}_${termIdx}`);
        const scopeInput = document.getElementById(`suppScope_${itemId}_${termIdx}`);
        const fileInput = document.getElementById(`suppFile_${itemId}_${termIdx}`);
        const button = document.getElementById(`suppMergeBtn_${itemId}_${termIdx}`);
        const assetLabel = (labelInput?.value || '').trim() || fallbackLabel || '';
        const assetScope = (scopeInput?.value || '').trim();
        const file = fileInput?.files?.[0];
        if (!assetLabel) {
          setSupplementInlineFeedback(itemId, termIdx, '请先填写入库名称', 'warning');
          return show('suppOut', '请输入入库名称');
        }
        if (!assetScope) {
          setSupplementInlineFeedback(itemId, termIdx, '请先选择素材版本', 'warning');
          return show('suppOut', '请先选择素材版本：通用版或赛道版');
        }
        if (!file) {
          setSupplementInlineFeedback(itemId, termIdx, '请先选择音效文件', 'warning');
          return show('suppOut', '请先选择音效文件');
        }
        if (button) {
          button.disabled = true;
          button.textContent = '上传中...';
        }
        setSupplementInlineFeedback(itemId, termIdx, '上传并合并中...', 'info');
        const form = new FormData();
        form.append('item_id', itemId);
        form.append('asset_label', assetLabel);
        form.append('asset_scope', assetScope);
        form.append('notify_after_merge', '1');
        form.append('file', file);
        try {
          const res = await fetch(`${base()}/ops/action-supplements/merge`, {
            method: 'POST',
            headers: h(),
            body: form,
          });
          const data = await read(res);
          if (!res.ok) {
            setSupplementInlineFeedback(itemId, termIdx, data?.detail || data?.message || '上传失败', 'error');
            show('suppOut', data);
            return;
          }
          setSupplementInlineFeedback(itemId, termIdx, `已按${data.asset_scope_label || '指定版本'}入库`, 'success');
          const nextStatus = String((data && data.status) || '').trim();
          const sel = document.getElementById('suppStatus');
          if (sel && nextStatus) sel.value = nextStatus;
          show('suppOut', {
            ok: true,
            message: `上传成功：已按${data.asset_scope_label || '指定版本'}入库，这条补充单已从 pending 流转到 ${supplementStatusLabel(nextStatus)}。系统已自动切换到新状态视图。`,
            item_id: data.item_id,
            next_status: nextStatus,
            asset_label: data.asset_label,
            asset_scope: data.asset_scope,
            asset_scope_label: data.asset_scope_label,
            asset_file_path: data.asset_file_path,
            covered_term_count: data.covered_term_count,
            target_term_count: data.target_term_count,
          });
          await loadSupplements();
          if (current && current.parent_node && current.parent_node.node_key) {
            setTimeout(() => jumpToSupplementGroup(current.parent_node.node_key, nextStatus || 'partial'), 120);
          }
        } catch (err) {
          setSupplementInlineFeedback(itemId, termIdx, '上传失败', 'error');
          show('suppOut', {
            ok: false,
            detail: `上传失败：${err && err.message ? err.message : String(err || 'unknown error')}`,
            item_id: itemId,
          });
        } finally {
          if (button) {
            button.disabled = false;
            button.textContent = '上传并合并入库';
          }
        }
      }

      async function notifySupplements() {
        const checked = Array.from(document.querySelectorAll('.supp-check:checked')).map(x => Number(x.value)).filter(x => Number.isFinite(x) && x > 0);
        const selected = (document.getElementById('notifyTarget').value || '').trim();
        let item_ids = checked;
        let phone = '';
        if (!item_ids.length && selected) {
          try {
            const parsed = JSON.parse(selected);
            item_ids = Array.isArray(parsed.item_ids) ? parsed.item_ids : [];
            phone = parsed.phone || '';
          } catch (_) {}
        }
        if (!item_ids.length) return show('suppOut', '请先从下拉框选择单条补充单，或勾选下方补充单后再批量通知');
        const res = await fetch(`${base()}/ops/action-supplements/notify`, {
          method: 'POST',
          headers: h({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ item_ids }),
        });
        const data = await read(res);
        const mode = checked.length ? '批量通知' : '单条通知';
        show('suppOut', {
          ...data,
          mode,
          message: checked.length
            ? '已将勾选补充单的提醒状态批量更新为已通知。'
            : (phone ? `已将单条补充单提醒状态更新为已通知：${phone}` : '已将所选单条补充单的提醒状态更新为已通知。'),
        });
        await loadSupplements();
      }

      async function loadBundle() {
        const pid = (document.getElementById('bundlePid').value || '').trim();
        if (!pid) return show('bundleOut', '请输入项目ID');
        const res = await fetch(`${base()}/ops/project/${encodeURIComponent(pid)}/flow-bundle`, { headers: h() });
        const data = await read(res);
        show('bundleOut', data);
        if (res.ok && data.assets && data.assets.audio_file) {
          document.getElementById('filePath').value = data.assets.audio_file;
        }
      }

      async function downloadFile() {
        const path = (document.getElementById('filePath').value || '').trim();
        if (!path) return show('fileOut', '请输入文件路径');
        const url = `${base()}/ops/file?path=${encodeURIComponent(path)}`;
        const res = await fetch(url, { headers: h() });
        if (!res.ok) return show('fileOut', await read(res));
        const blob = await res.blob();
        const a = document.createElement('a');
        const href = URL.createObjectURL(blob);
        a.href = href;
        a.download = path.split('/').pop() || 'download.bin';
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(href);
        show('fileOut', { ok: true, file: a.download });
      }

      setState();
      renderDocAnchors('actionDocsOut', ACTION_DOC_ANCHORS);
      updateSupplementStatusHint();
      if (token) {
        loadFrontendSecurity().catch(err => show('frontendSecurityOut', String(err?.message || err)));
        loadActionFallbackMonitor().catch(err => show('fallbackMonitorOut', String(err?.message || err)));
        loadActionFallbackAlerts().catch(err => show('fallbackMonitorOut', String(err?.message || err)));
        loadCreatorShowcases().catch(err => show('eventsOut', String(err?.message || err)));
        loadCopyrightAdsAdmin().catch(err => show('assetFeedbackOut', String(err?.message || err)));
        loadRechargeOrders().catch(err => show('usersOut', String(err?.message || err)));
        startFallbackAlertPolling();
      }
      loadInheritanceDashboard();
      loadGraphMaintenanceCatalog('');
    