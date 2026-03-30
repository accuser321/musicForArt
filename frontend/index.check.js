
      let projectId = null;
      let latestLexiconDraft = null;
      let latestActionVerbData = null;
      let latestActionSfxData = null;
      let latestActionGraphDraftData = null;
      let latestActionGraphExplanation = null;
      let latestSceneBuildingData = null;
      let latestSceneSfxData = null;
      let latestSceneSupplementData = null;
      let uiBusy = false;
      let authToken = localStorage.getItem('mfa_auth_token') || '';
      let authPhone = localStorage.getItem('mfa_auth_phone') || '';
      let authUser = null;
      let authSettings = { invite_only_enabled: true, invite_code_required: true, referral_code_supported: true, uid_supported: true, home_leaderboards_enabled: false };
      let leaderboardWindowKey = '10d';
      const ACTION_PROMPT_BY_GENRE = {
        '玄幻': 'V3-action_verbs_xuanhuan_task.txt',
        '言情': 'V3-action_verbs_yanqing_task.txt',
        '悬疑': 'V3-action_verbs_xuanyi_task.txt',
        '科幻': 'V3-action_verbs_kehuan_task.txt',
      };
      const SCENE_PROMPT_BY_GENRE = {
        '玄幻': 'V3-scene_building_task.txt',
        '言情': 'V3-scene_building_task.txt',
        '悬疑': 'V3-scene_building_task.txt',
        '科幻': 'V3-scene_building_task.txt',
      };

      function currentLlmProviderOverride() {
        const el = document.getElementById('llmProvider');
        return String(el && el.value ? el.value : '').trim();
      }

      function ensureLoggedInForFeature() {
        if (authToken) return true;
        show('请先完成测试用户登录后再使用创作功能。测试期间仅邀请码用户可激活使用。');
        return false;
      }

      function resetSceneFlowState() {
        latestSceneBuildingData = null;
        latestSceneSfxData = null;
        latestSceneSupplementData = null;
        refreshSceneActionButtons();
      }

      function clearSceneFlowFrom(stage) {
        const key = String(stage || '').trim();
        if (key === 'building') {
          latestSceneBuildingData = null;
          latestSceneSfxData = null;
          latestSceneSupplementData = null;
        } else if (key === 'sfx') {
          latestSceneSfxData = null;
          latestSceneSupplementData = null;
        } else if (key === 'supplements') {
          latestSceneSupplementData = null;
        }
        refreshSceneActionButtons();
      }

      function showScenePendingState(stage) {
        const key = String(stage || '').trim();
        if (key === 'building') {
          return show('正在重跑 9) 场景搭建分析。\n系统已清空上一轮的场景分析、场景音效推荐和场景补充单结果，请稍候新的场景结果返回。');
        }
        if (key === 'sfx') {
          return show('正在重跑 10) 场景音效推荐。\n系统已清空上一轮的场景音效推荐和场景补充单结果，请稍候新的音效推荐结果返回。');
        }
        if (key === 'supplements') {
          return show('正在重跑 11) 场景补充单。\n系统已清空上一轮的场景补充单结果，请稍候新的补充单结果返回。');
        }
      }

      function refreshSceneActionButtons() {
        const sceneBuildingBtn = document.getElementById('sceneBuildingBtn');
        const sceneSfxBtn = document.getElementById('sceneSfxBtn');
        const sceneSupplementBtn = document.getElementById('sceneSupplementBtn');
        const sceneStageHint = document.getElementById('sceneStageHint');
        if (!sceneBuildingBtn || !sceneSfxBtn || !sceneSupplementBtn || !sceneStageHint) return;
        const loggedIn = !!authToken;
        const hasProject = !!projectId;
        const hasSceneBuilding = !!(latestSceneBuildingData && Array.isArray(latestSceneBuildingData.scene_items));
        const hasSceneSfx = !!(latestSceneSfxData && Array.isArray(latestSceneSfxData.scene_items));
        const hasSceneSupplements = !!(
          latestSceneSupplementData
          && (
            Array.isArray(latestSceneSupplementData.tasks)
            || Array.isArray(latestSceneSupplementData.task_items)
            || typeof latestSceneSupplementData.task_count === 'number'
          )
        );

        sceneBuildingBtn.disabled = !loggedIn || !hasProject;
        sceneSfxBtn.disabled = !loggedIn || !hasSceneBuilding;
        sceneSupplementBtn.disabled = !loggedIn || !hasSceneSfx;
        sceneBuildingBtn.textContent = hasProject ? '9) 场景搭建分析' : '9) 场景搭建分析（先创建项目）';
        sceneSfxBtn.textContent = hasSceneBuilding
          ? `10) 场景音效推荐${hasSceneSfx ? '（可重跑）' : ''}`
          : '10) 场景音效推荐（待第9步）';
        sceneSupplementBtn.textContent = hasSceneSfx
          ? `11) 场景补充单${hasSceneSupplements ? '（可重跑）' : ''}`
          : '11) 场景补充单（待第10步）';

        if (!loggedIn) {
          sceneStageHint.innerHTML = '场景模块链路：请先完成测试用户登录，登录后才能执行 <code>9) 场景搭建分析</code>。';
          return;
        }
        if (!hasProject) {
          sceneStageHint.innerHTML = '场景模块链路：请先创建项目，再执行 <code>9) 场景搭建分析</code>。';
          return;
        }
        if (!hasSceneBuilding) {
          sceneStageHint.innerHTML = '场景模块链路：当前可先执行 <code>9) 场景搭建分析</code>；完成后会自动解锁 <code>10) 场景音效推荐</code>。';
          return;
        }
        if (!hasSceneSfx) {
          sceneStageHint.innerHTML = '场景模块链路：已完成 <code>9) 场景搭建分析</code>，现在可执行 <code>10) 场景音效推荐</code>。';
          return;
        }
        if (!hasSceneSupplements) {
          sceneStageHint.innerHTML = '场景模块链路：已完成 <code>10) 场景音效推荐</code>，现在可生成 <code>11) 场景补充单</code>。';
          return;
        }
        sceneStageHint.innerHTML = '场景模块链路：<code>9) 场景搭建分析</code>、<code>10) 场景音效推荐</code>、<code>11) 场景补充单</code> 已完成一轮，可按需要重新执行任一步。';
      }

      function sanitizeText(s) {
        return String(s ?? '')
          .replace(/\u0000/g, '')
          .replace(/\r\n/g, '\n')
          .trim();
      }

      function parseJsonLoose(v) {
        if (typeof v !== 'string') return null;
        const text = sanitizeText(v);
        if (!text) return null;
        try {
          return JSON.parse(text);
        } catch (_) {}
        const match = text.match(/```json\s*([\s\S]*?)```/i) || text.match(/\{[\s\S]*\}$/);
        if (!match) return null;
        const raw = sanitizeText(match[1] || match[0]);
        try {
          return JSON.parse(raw);
        } catch (_) {
          return null;
        }
      }

      function extractFirstJsonObject(text) {
        const s = sanitizeText(text);
        if (!s) return null;
        const n = s.length;
        for (let i = 0; i < n; i += 1) {
          if (s[i] !== '{') continue;
          let depth = 0;
          let inStr = false;
          let esc = false;
          for (let j = i; j < n; j += 1) {
            const ch = s[j];
            if (inStr) {
              if (esc) {
                esc = false;
              } else if (ch === '\\') {
                esc = true;
              } else if (ch === '"') {
                inStr = false;
              }
              continue;
            }
            if (ch === '"') { inStr = true; continue; }
            if (ch === '{') depth += 1;
            if (ch === '}') {
              depth -= 1;
              if (depth === 0) {
                const cand = s.slice(i, j + 1);
                try {
                  const obj = JSON.parse(cand);
                  if (obj && typeof obj === 'object' && !Array.isArray(obj)) return obj;
                } catch (_) {}
                break;
              }
            }
          }
        }
        return null;
      }

      function deepClean(value) {
        if (Array.isArray(value)) {
          return value
            .map(deepClean)
            .filter(v => v !== null && v !== undefined && !(typeof v === 'string' && !v.trim()));
        }
        if (value && typeof value === 'object') {
          const out = {};
          for (const [k, v] of Object.entries(value)) {
            const next = deepClean(v);
            if (next === null || next === undefined) continue;
            if (typeof next === 'string' && !next.trim()) continue;
            if (Array.isArray(next) && next.length === 0) continue;
            out[k] = next;
          }
          return out;
        }
        if (typeof value === 'string') return sanitizeText(value);
        return value;
      }

      function normalizePayload(input) {
        if (!input || typeof input !== 'object') return input;
        const data = deepClean({ ...input });
        const collectTraces = (obj, path = 'root', out = []) => {
          if (!obj || typeof obj !== 'object') return out;
          if (Array.isArray(obj.llm_trace) && obj.llm_trace.length) {
            obj.llm_trace.forEach(t => out.push({ ...(t || {}), _source_path: path }));
          }
          for (const [k, v] of Object.entries(obj)) {
            if (!v || typeof v !== 'object') continue;
            if (k === 'llm_trace') continue;
            collectTraces(v, `${path}.${k}`, out);
          }
          return out;
        };
        if (data.title === '动作图谱推荐' || (data.graph_model && data.graph_model.parent === '赛道+动作词')) {
          if (!Array.isArray(data.graph_items)) data.graph_items = [];
          if (!data.asset_gap_summary || typeof data.asset_gap_summary !== 'object') {
            data.asset_gap_summary = { gap_count: 0, gap_items: [] };
          } else if (!Array.isArray(data.asset_gap_summary.gap_items)) {
            data.asset_gap_summary.gap_items = [];
          }
          if (!data.summary || typeof data.summary !== 'object') {
            data.summary = { verb_count: 0, asset_count: 0, gap_count: 0 };
          }
        }
        if (typeof data.report_json === 'string') {
          const parsed = parseJsonLoose(data.report_json);
          if (parsed && typeof parsed === 'object') data.report_json = parsed;
        }
        if ((!data.report_json || typeof data.report_json !== 'object') && typeof data.report_markdown === 'string') {
          const parsedFromMd = parseJsonLoose(data.report_markdown);
          if (parsedFromMd && typeof parsedFromMd === 'object') data.report_json = parsedFromMd;
        }
        if (!Array.isArray(data.llm_trace) && data.debug && Array.isArray(data.debug.llm_trace)) {
          data.llm_trace = data.debug.llm_trace;
        }
        if ((!Array.isArray(data.llm_trace) || !data.llm_trace.length) && data.text && typeof data.text === 'object') {
          data.scenes = data.scenes || data.text.scenes;
          data.sfx_requirements = data.sfx_requirements || data.text.sfx_requirements;
          data.quick_download_list = data.quick_download_list || data.text.quick_download_list;
          data.report_json = data.report_json || data.text.report_json;
          data.report_markdown = data.report_markdown || data.text.report_markdown;
          data.prompt_guard = data.prompt_guard || data.text.prompt_guard;
        }
        if (data.narration && typeof data.narration === 'object') {
          data.clause_timeline = data.clause_timeline || data.narration.clause_timeline;
        }
        if (!Array.isArray(data.llm_trace) || !data.llm_trace.length) {
          const mergedTraces = collectTraces(data);
          if (mergedTraces.length) data.llm_trace = mergedTraces;
        }
        if (data.report_json && typeof data.report_json === 'object') {
          if (!Array.isArray(data.report_json.key_points) && typeof data.report_json.key_points === 'string') {
            data.report_json.key_points = data.report_json.key_points
              .split(/\n+/)
              .map(x => x.replace(/^[-*]\s*/, '').trim())
              .filter(Boolean);
          }
          if ((!Array.isArray(data.clause_timeline) || !data.clause_timeline.length) && Array.isArray(data.report_json.clause_timeline)) {
            data.clause_timeline = data.report_json.clause_timeline;
          }
        }
        return data;
      }

      function show(data) {
        const normalized = normalizePayload(data);
        syncAuthUsage(normalized);
        renderUsagePanel(normalized);
        renderDebugPayload(normalized);
        renderPretty(normalized);
      }

      function renderDebugPayload(data) {
        const wrap = document.getElementById('debugJsonWrap');
        const box = document.getElementById('debugOutput');
        if (!wrap || !box) return;
        const enabled = isFrontendDebugEnabled(data);
        if (!enabled) {
          wrap.style.display = 'none';
          box.textContent = '';
          return;
        }
        wrap.style.display = 'block';
        box.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
      }

      function isFrontendDebugEnabled(data = null) {
        return Boolean(
          (authSettings && authSettings.frontend_debug_expose_enabled)
          || (data && data.frontend_debug_expose_enabled)
        );
      }

      function syncAuthUsage(data) {
        if (!data || typeof data !== 'object') return;
        if (!authUser || typeof authUser !== 'object') authUser = {};
        if (data.user && typeof data.user === 'object') {
          authUser = { ...authUser, ...data.user };
        }
        if (data.quota && typeof data.quota === 'object') {
          authUser.quota = data.quota;
        } else if (data.usage && data.usage.quota && typeof data.usage.quota === 'object') {
          authUser.quota = data.usage.quota;
        }
      }

      function syncUsageFromDownloadResponse(res) {
        if (!res || typeof res.headers?.get !== 'function') return;
        const ymd = res.headers.get('X-Quota-Ymd');
        if (!ymd) return;
        if (!authUser || typeof authUser !== 'object') authUser = {};
        authUser.quota = {
          ymd,
          text_chars_used: Number(res.headers.get('X-Quota-Text-Chars-Used') || 0),
          text_chars_limit: Number(res.headers.get('X-Quota-Text-Chars-Limit') || 0),
          text_chars_remaining: Number(res.headers.get('X-Quota-Text-Chars-Remaining') || 0),
          sfx_download_used: Number(res.headers.get('X-Quota-Sfx-Download-Used') || 0),
          sfx_download_limit: Number(res.headers.get('X-Quota-Sfx-Download-Limit') || 0),
          sfx_download_remaining: Number(res.headers.get('X-Quota-Sfx-Download-Remaining') || 0),
        };
        renderUsagePanel(authUser);
      }

      function renderUsagePanel(data) {
        const box = document.getElementById('usagePanel');
        if (!box) return;
        const usage = data && typeof data === 'object' ? data.usage : null;
        const quota = (usage && usage.quota) || (data && data.quota) || (authUser && authUser.quota) || null;
        const tierCode = (usage && usage.user_tier_code) || (data && data.user && data.user.user_tier_code) || (authUser && authUser.user_tier_code) || '';
        const isAuthorized = Boolean(
          (usage && usage.authorized)
          || (data && data.user && data.user.is_authorized)
          || (authUser && authUser.is_authorized)
        );
        const phone = (data && data.user && data.user.phone) || (authUser && authUser.phone) || authPhone || '';
        if (!quota || typeof quota !== 'object') {
          box.style.display = 'none';
          box.innerHTML = '';
          return;
        }
        const tierLabel = tierCode === '22' ? '种子用户' : (tierCode === '33' ? '普通用户' : '当前用户');
        const textRemaining = Number(quota.text_chars_remaining || 0);
        const sfxRemaining = Number(quota.sfx_download_remaining || 0);
        const overLimitHint = (!isAuthorized && (textRemaining <= 0 || sfxRemaining <= 0))
          ? '<div class="usage-alert">用量超额，请联系管理员。文本分析支持单次最多超额 1000 字的缓冲；缓冲使用完后，当天将暂停继续使用。</div>'
          : '<div class="usage-alert">如今日用量达到上限，请联系管理员协助调整额度。</div>';
        box.style.display = 'block';
        box.innerHTML = `
          <h4>今日用量</h4>
          <div class="usage-grid">
            <div class="usage-stat">
              <div class="k">文本分析字符</div>
              <div class="v">${escHtml(quota.text_chars_used || 0)} / ${escHtml(quota.text_chars_limit || 0)}</div>
              <div class="k">剩余 ${escHtml(quota.text_chars_remaining || 0)} 字</div>
            </div>
            <div class="usage-stat">
              <div class="k">音效下载数量</div>
              <div class="v">${escHtml(quota.sfx_download_used || 0)} / ${escHtml(quota.sfx_download_limit || 0)}</div>
              <div class="k">剩余 ${escHtml(quota.sfx_download_remaining || 0)} 个</div>
            </div>
          </div>
          <div class="usage-meta">
            账号：${escHtml(phone || '未登录')}<br/>
            级别：${escHtml(tierLabel)}${tierCode ? `（${escHtml(tierCode)}）` : ''}<br/>
            统计日期：${escHtml(quota.ymd || '今日')}<br/>
            ${isAuthorized ? '当前账号为授权用户：默认不扣减每日额度。' : '当前账号按每日额度计数。'}
          </div>
          ${overLimitHint}
        `;
      }

      function leaderboardWindowLabel(key) {
        return ({
          '1d': '过去1天',
          '10d': '过去10天',
          '30d': '过去30天',
          '90d': '过去3个月',
        })[String(key || '').trim()] || '过去10天';
      }

      function truncateLeaderboardName(value, maxChars = 8) {
        const chars = Array.from(String(value || '').trim());
        if (!chars.length) return '-';
        if (chars.length <= maxChars) return chars.join('');
        return `${chars.slice(0, maxChars).join('')}...`;
      }

      function leaderboardBoardTheme(boardKey) {
        const key = String(boardKey || '').trim();
        if (key === 'overall') {
          return { cls: 'theme-action', badge: '动作', note: '动作总热度' };
        }
        if (key === 'common') {
          return { cls: 'theme-common', badge: '通用', note: '通用素材热度' };
        }
        if (key === 'genre_玄幻') {
          return { cls: 'theme-xuanhuan', badge: '玄幻', note: '渐变黄' };
        }
        if (key === 'genre_言情') {
          return { cls: 'theme-yanqing', badge: '言情', note: '渐变红' };
        }
        if (key === 'genre_悬疑') {
          return { cls: 'theme-xuanyi', badge: '悬疑', note: '渐变黑' };
        }
        if (key === 'genre_科幻') {
          return { cls: 'theme-kehuan', badge: '科幻', note: '渐变蓝' };
        }
        return { cls: 'theme-common', badge: '榜单', note: '下载热度' };
      }

      function updateLeaderboardTabs() {
        ['1d', '10d', '30d', '90d'].forEach((key) => {
          const node = document.getElementById(`lbTab_${key}`);
          if (!node) return;
          node.classList.toggle('active', key === leaderboardWindowKey);
        });
      }

      function renderHomeLeaderboards(data) {
        const box = document.getElementById('leaderboardSections');
        if (!box) return;
        const sections = Array.isArray(data && data.sections) ? data.sections : [];
        if (!sections.length) {
          box.innerHTML = '<div class="leaderboard-section"><div class="leaderboard-empty">当前时间范围内还没有可展示的下载排行。</div></div>';
          return;
        }
        box.innerHTML = sections.map((section) => `
          <div class="leaderboard-section">
            <h3>${escHtml(section.title || '')}</h3>
            <div class="leaderboard-board-grid">
              ${(section.boards || []).map((board) => {
                const theme = leaderboardBoardTheme(board.board_key);
                return `
                  <div class="leaderboard-board ${theme.cls}">
                    <div class="leaderboard-head">
                      <div class="leaderboard-title-wrap">
                        <h4>${escHtml(board.title || '')}</h4>
                      </div>
                      <span class="leaderboard-badge ${theme.cls}">${escHtml(theme.badge)}</span>
                    </div>
                    ${Array.isArray(board.items) && board.items.length ? `
                      <div class="leaderboard-list">
                        ${board.items.map((item, idx) => `
                          <div class="leaderboard-item">
                            <div class="leaderboard-rank">${idx + 1}</div>
                            <div class="leaderboard-name" title="${escHtml(item.display_name || item.item_key || '-')}">${escHtml(truncateLeaderboardName(item.display_name || item.item_key || '-', 8))}</div>
                            <div class="leaderboard-count">${escHtml(item.count || 0)}</div>
                          </div>
                        `).join('')}
                      </div>
                    ` : '<div class="leaderboard-empty">当前时间范围内暂无下载数据。</div>'}
                  </div>
                `;
              }).join('')}
            </div>
          </div>
        `).join('');
      }

      async function loadHomeLeaderboards() {
        updateHomepageVisibility();
        if (!(authSettings && authSettings.home_leaderboards_enabled)) {
          return;
        }
        const box = document.getElementById('leaderboardSections');
        if (box) {
          box.innerHTML = '<div class="leaderboard-section">排行榜加载中...</div>';
        }
        try {
          const res = await window.fetch(`${apiBase()}/home/leaderboards?window=${encodeURIComponent(leaderboardWindowKey)}`);
          const data = await readApiResponse(res);
          renderHomeLeaderboards(data);
        } catch (err) {
          if (box) {
            box.innerHTML = `<div class="leaderboard-section"><div class="leaderboard-empty">排行榜加载失败：${escHtml(err && err.message ? err.message : String(err || 'unknown error'))}</div></div>`;
          }
        }
      }

      function setLeaderboardWindow(key) {
        leaderboardWindowKey = key || '10d';
        updateLeaderboardTabs();
        loadHomeLeaderboards();
      }

      function renderLlmDebug(data) {
        const box = document.getElementById('llmDebugBox');
        if (!box) return;
        const trace = data && data.llm_trace;
        if (!Array.isArray(trace) || !trace.length) {
          box.textContent = '本次没有LLM调试追踪（可能命中规则降级或LLM未启用）。';
          return;
        }
        box.innerHTML = trace.map((t, idx) => {
          const cm = t.call_meta || {};
          const contractText = t.contract_valid === true
            ? '通过'
            : (t.contract_valid === false ? `不通过：${escHtml(t.contract_reason || '')}` : '未校验');
          const rawTxt = (t.raw_response || '');
          const rawBlock = rawTxt
            ? `<div><strong>Raw Response</strong><pre>${escHtml(rawTxt)}</pre></div>`
            : `<div class="error"><strong>Raw Response 为空</strong><div>status=${escHtml(cm.status || '')} ${cm.error ? `| error=${escHtml(cm.error)}` : ''}</div>${cm.body ? `<details><summary>错误体</summary><pre>${escHtml(cm.body)}</pre></details>` : ''}</div>`;
          const srcPath = t._source_path ? `<div><strong>来源</strong> ${escHtml(t._source_path)}</div>` : '';
          return `
            <details style="margin-bottom:8px;">
              <summary>调用${idx + 1} | status=${escHtml(cm.status || '')} | request_id=${escHtml(cm.request_id || '')}</summary>
              ${srcPath}
              <div><strong>Task Prompt</strong><pre>${escHtml(t.task_prompt || '')}</pre></div>
              <div><strong>System Prompt</strong><pre>${escHtml(t.system_prompt || '')}</pre></div>
              <div><strong>User Prompt</strong><pre>${escHtml(t.user_prompt || '')}</pre></div>
              <div class="hint" style="margin:6px 0;">以下 Raw Response 为 <strong>LLM 原始输出</strong>，未经过系统补充与派生。</div>
              ${rawBlock}
              <div><strong>Contract</strong> ${contractText}</div>
            </details>
          `;
        }).join('');
      }

      function mdToHtml(md) {
        const lines = String(md || '').split('\n');
        const out = [];
        let inList = false;
        for (const line of lines) {
          const s = line.trim();
          if (!s) {
            if (inList) { out.push('</ul>'); inList = false; }
            continue;
          }
          if (s.startsWith('### ')) { if (inList) { out.push('</ul>'); inList = false; } out.push(`<h3>${escHtml(s.slice(4))}</h3>`); continue; }
          if (s.startsWith('## ')) { if (inList) { out.push('</ul>'); inList = false; } out.push(`<h2>${escHtml(s.slice(3))}</h2>`); continue; }
          if (s.startsWith('# ')) { if (inList) { out.push('</ul>'); inList = false; } out.push(`<h1>${escHtml(s.slice(2))}</h1>`); continue; }
          if (s.startsWith('- ')) {
            if (!inList) { out.push('<ul>'); inList = true; }
            out.push(`<li>${escHtml(s.slice(2))}</li>`);
            continue;
          }
          if (inList) { out.push('</ul>'); inList = false; }
          out.push(`<p>${escHtml(s)}</p>`);
        }
        if (inList) out.push('</ul>');
        return out.join('');
      }

      function extractRawResponsePayload(data) {
        const trace = Array.isArray(data && data.llm_trace) ? data.llm_trace : [];
        for (let i = trace.length - 1; i >= 0; i -= 1) {
          const t = trace[i] || {};
          const raw = sanitizeText(t.raw_response || '');
          const parsed = raw ? (parseJsonLoose(raw) || extractFirstJsonObject(raw)) : null;
          return {
            parsed: (parsed && typeof parsed === 'object') ? parsed : null,
            raw,
            prompt_file: t.prompt_file || '',
            status: (t.call_meta || {}).status || '',
          };
        }
        return { parsed: null, raw: '', prompt_file: '', status: '' };
      }

      function extractRawResponsePayloads(data) {
        const trace = Array.isArray(data && data.llm_trace) ? data.llm_trace : [];
        const out = [];
        for (let i = 0; i < trace.length; i += 1) {
          const t = trace[i] || {};
          const raw = sanitizeText(t.raw_response || '');
          const parsed = raw ? (parseJsonLoose(raw) || extractFirstJsonObject(raw)) : null;
          out.push({
            parsed: (parsed && typeof parsed === 'object') ? parsed : null,
            raw,
            prompt_file: t.prompt_file || '',
            status: (t.call_meta || {}).status || '',
            contract_valid: t.contract_valid,
          });
        }
        return out;
      }

      function getBestActionVerbReport(data) {
        const mergedReport = data && data.report_json && typeof data.report_json === 'object' ? data.report_json : null;
        const trace = Array.isArray(data && data.llm_trace) ? data.llm_trace : [];
        const providerUsed = ((trace[0] && trace[0].call_meta) || {}).provider || (data && data.llm_provider_used) || '';
        const modelUsed = ((trace[0] && trace[0].call_meta) || {}).model || (data && data.llm_model_used) || '';
        if (mergedReport && Array.isArray(mergedReport.action_candidates)) {
          const traceContracts = trace
            .map(t => (t && Object.prototype.hasOwnProperty.call(t, 'contract_valid')) ? t.contract_valid : null)
            .filter(v => v !== null);
          const mergedContractValid = traceContracts.length ? traceContracts.every(v => v === true) : null;
          const mergedContractReason = mergedContractValid === false
            ? trace
                .map(t => (t && t.contract_valid === false ? String(t.contract_reason || '').trim() : ''))
                .filter(Boolean)
                .join('；')
            : '';
          return {
            report: mergedReport,
            source: (data.segmentation && data.segmentation.mode === 'segmented') ? '系统合并结果' : '接口最终report_json',
            prompt_file: data.effective_prompt_file || '',
            status: 'ok',
            contract_valid: mergedContractValid,
            contract_reason: mergedContractReason,
            raw: '',
            effective_prompt_file: data && data.effective_prompt_file ? data.effective_prompt_file : '',
            genre: data && data.genre ? data.genre : (mergedReport && mergedReport.genre ? mergedReport.genre : ''),
            segmentation: data && data.segmentation ? data.segmentation : null,
            segment_reports: Array.isArray(data && data.segment_reports) ? data.segment_reports : [],
            provider_used: providerUsed,
            model_used: modelUsed,
          };
        }
        const payloads = extractRawResponsePayloads(data);
        for (let i = 0; i < payloads.length; i += 1) {
          const item = payloads[i];
          const rawObj = item && item.parsed && typeof item.parsed === 'object' ? item.parsed : null;
          if (rawObj) {
            return {
              report: rawObj,
              source: `LLM调用${i + 1}`,
              prompt_file: item.prompt_file || '',
              status: item.status || '',
              contract_valid: item.contract_valid,
              contract_reason: (trace[i] && trace[i].contract_reason) ? trace[i].contract_reason : '',
              raw: item.raw || '',
              effective_prompt_file: data && data.effective_prompt_file ? data.effective_prompt_file : '',
              genre: data && data.genre ? data.genre : (rawObj && rawObj.genre ? rawObj.genre : ''),
              segmentation: data && data.segmentation ? data.segmentation : null,
              segment_reports: Array.isArray(data && data.segment_reports) ? data.segment_reports : [],
              provider_used: ((trace[i] && trace[i].call_meta) || {}).provider || providerUsed,
              model_used: ((trace[i] && trace[i].call_meta) || {}).model || modelUsed,
            };
          }
        }
        return null;
      }

      function renderKvValue(v) {
        if (v === null || v === undefined) return '<span class="hint">null</span>';
        if (typeof v === 'string') return escHtml(v);
        if (typeof v === 'number' || typeof v === 'boolean') return escHtml(String(v));
        if (Array.isArray(v)) {
          if (!v.length) return '<span class="hint">[]</span>';
          const allPrimitive = v.every(x => x === null || ['string', 'number', 'boolean'].includes(typeof x));
          if (allPrimitive) return escHtml(v.map(x => String(x)).join('、'));
          return `<pre>${escHtml(JSON.stringify(v, null, 2))}</pre>`;
        }
        if (typeof v === 'object') {
          return `<pre>${escHtml(JSON.stringify(v, null, 2))}</pre>`;
        }
        return escHtml(String(v));
      }

      function renderRawUserView(data) {
        const box = document.getElementById('rawUserView');
        if (!box) return;
        const reportObj = (data && data.report_json && typeof data.report_json === 'object')
          ? data.report_json
          : (data && data.text && data.text.report_json && typeof data.text.report_json === 'object'
              ? data.text.report_json
              : null);
        const payload = extractRawResponsePayload(data);
        const hasCoreFromReport = !!(
          reportObj
          && (reportObj.title || reportObj.text_theme
            || (reportObj.fit_with_music && typeof reportObj.fit_with_music === 'object' && Object.keys(reportObj.fit_with_music).length)
            || (Array.isArray(reportObj.scene_units) && reportObj.scene_units.length))
        );
        const parsed = hasCoreFromReport ? reportObj : (payload && payload.parsed);
        if (!parsed || typeof parsed !== 'object') {
          const raw = sanitizeText((payload && payload.raw) || '');
          if (!raw) {
            box.innerHTML = '<div class="hint">当前没有可展示的 Raw Response（本次调用未返回文本）。</div>';
            return;
          }
          box.innerHTML = `
            <div class="hint" style="margin-bottom:8px;">本次未解析为 JSON，以下为模型原始返回文本（用于排查）。</div>
            <div class="hint" style="margin-bottom:8px;">status=${escHtml(payload.status || '')} / prompt=${escHtml(payload.prompt_file || '')}</div>
            <pre style="white-space:pre-wrap;max-height:460px;overflow:auto;">${escHtml(raw)}</pre>
          `;
          return;
        }
        const picked = {
          title: parsed.title ?? '',
          text_theme: parsed.text_theme ?? '',
          fit_with_music: parsed.fit_with_music ?? {},
          scene_units: Array.isArray(parsed.scene_units) ? parsed.scene_units : [],
        };
        const rows = Object.entries(picked).map(([k, v]) => `
          <tr>
            <td style="white-space:nowrap;"><strong>${escHtml(k)}</strong></td>
            <td>${renderKvValue(v)}</td>
          </tr>
        `).join('');
        box.innerHTML = `
          <div class="hint" style="margin-bottom:8px;">展示字段：title / text_theme / fit_with_music / scene_units（优先使用接口最终 report_json）</div>
          <table class="table-lite"><thead><tr><th>JSON Key</th><th>Value（来自最终结构化结果）</th></tr></thead><tbody>${rows}</tbody></table>
        `;
      }

      function fmtSec(v) {
        const n = Number(v);
        return Number.isFinite(n) ? `${n.toFixed(2)} 秒` : '-';
      }

      function renderPills(items) {
        const arr = Array.isArray(items) ? items.filter(Boolean) : [];
        if (!arr.length) return '<span class="hint">无</span>';
        return arr.map(x => `<span class="pill">${escHtml(String(x))}</span>`).join('');
      }
      function renderScenePills(items, kind = 'text', matchedTerms = null) {
        const arr = Array.isArray(items) ? items.filter(Boolean) : [];
        if (!arr.length) return '<span class="hint">无</span>';
        if (kind === 'sfx' || kind === 'sfx-missing') {
          if (kind === 'sfx-missing') {
            return arr.map(x => `<span class="pill scene-pill sfx missing">${escHtml(String(x))}</span>`).join('');
          }
          const matched = matchedTerms instanceof Set ? matchedTerms : new Set();
          return arr.map(x => {
            const term = String(x);
            const hitCls = matched.has(term) ? ' hit' : '';
            return `<span class="pill scene-pill sfx${hitCls}">${escHtml(term)}</span>`;
          }).join('');
        }
        return renderPills(arr);
      }
      function buildSceneMatchedSfxSet(assets) {
        const out = new Set();
        (Array.isArray(assets) ? assets : []).forEach(asset => {
          const label = String(asset?.label || asset?.asset_label || '').trim();
          if (label) out.add(label);
        });
        return out;
      }
      function renderSceneFieldLabel(title, kind = 'text') {
        return `<span class="scene-field-tag ${escAttr(kind)}">${escHtml(title)}</span>`;
      }
      function middleEllipsis(text, head = 28, tail = 18) {
        const value = String(text || '').trim();
        if (!value) return '无';
        if (value.length <= head + tail + 3) return value;
        return `${value.slice(0, head)}..........${value.slice(-tail)}`;
      }

      function renderSourceBadge(source) {
        const key = String(source || '').trim();
        const label = ({
          'common': '通用层',
          'genre': '赛道层',
          'common+genre': '通用+赛道',
          'fallback': '保底生成',
        })[key] || '未标注';
        return `<span class="pill">${escHtml(label)}</span>`;
      }

      function renderTermItemsWithSource(items, suffix = '') {
        const arr = Array.isArray(items) ? items.filter(Boolean) : [];
        if (!arr.length) return '<span class="hint">无</span>';
        const currentGenreLabel = String(
          document.getElementById('projectGenreState')?.textContent
          || document.getElementById('projectGenre')?.value
          || '赛道'
        ).trim() || '赛道';
        const expanded = [];
        arr.forEach(item => {
          const term = String(item && item.term || '').trim();
          const source = String(item && item.source || '').trim();
          if (!term) return;
          if (source === 'common+genre') {
            expanded.push({ term: `${term}（通用）`, source: 'common' });
            expanded.push({ term: `${term}（${currentGenreLabel}）`, source: 'genre' });
            return;
          }
          expanded.push({ term, source });
        });
        return `<div class="term-state-list">${expanded.map(item => `
          <span class="term-state">
            ${escHtml(String(item.term || ''))}${suffix}
            ${renderSourceBadge(item.source)}
          </span>
        `).join('')}</div>`;
      }

      function filterUserVisibleActionTerms(terms, actionHead = '') {
        const head = String(actionHead || '').trim();
        const arr = Array.isArray(terms) ? terms : [];
        if (!head) return arr.filter(Boolean);
        return arr
          .map(term => String(term || '').trim())
          .filter(term => {
            if (!term) return false;
            if (term === head) return true;
            if (head.includes(term) && term.length < head.length) return false;
            return true;
          });
      }

      function filterUserVisibleActionTermItems(items, actionHead = '') {
        const head = String(actionHead || '').trim();
        const arr = Array.isArray(items) ? items : [];
        if (!head) return arr.filter(Boolean);
        return arr.filter(item => {
          const term = String(item?.term || '').trim();
          const source = String(item?.source || '').trim();
          if (!term) return false;
          if (source !== 'fallback') return true;
          if (term === head) return true;
          if (head.includes(term) && term.length < head.length) return false;
          return true;
        });
      }

      function renderSfxBuckets(children, fallbackTerms, fallbackMissingTerms) {
        const direct = Array.isArray(children && children.direct_sfx_terms) ? children.direct_sfx_terms : [];
        const composite = Array.isArray(children && children.composite_sfx_terms) ? children.composite_sfx_terms : [];
        const missingDirect = Array.isArray(children && children.missing_direct_sfx_terms) ? children.missing_direct_sfx_terms : [];
        const missingComposite = Array.isArray(children && children.missing_composite_sfx_terms) ? children.missing_composite_sfx_terms : [];
        const covered = Array.isArray(children && children.covered_sfx_terms) ? children.covered_sfx_terms : [];
        const coveredSet = new Set(covered.map(x => String(x).trim()).filter(Boolean));
        const renderStateTerms = (arr, compositeMode = false) => {
          if (!arr.length) return '<span class="hint">无</span>';
          return `<div class="term-state-list">${arr.map(term => {
            const t = String(term || '').trim();
            const isCovered = coveredSet.has(t);
            return `<span class="term-state ${isCovered ? 'covered' : 'pending'}"><span class="term-icon">${isCovered ? '✓' : '?'}</span>${escHtml(t)}${compositeMode ? '（整体）' : ''}</span>`;
          }).join('')}</div>`;
        };
        const directHtml = direct.length ? `<div><span class="hint">直达音效</span><div>${renderPills(direct)}</div></div>` : '';
        const compositeHtml = composite.length ? `<div style="margin-top:6px;"><span class="hint">整体音效</span><div>${renderPills(composite.map(x => `${x}（整体）`))}</div></div>` : '';
        const missingDirectHtml = direct.length ? `<div><span class="pending-sfx-alert">待补直达音效</span>${renderStateTerms(direct, false)}</div>` : '';
        const missingCompositeHtml = composite.length ? `<div style="margin-top:6px;"><span class="pending-sfx-alert">待补整体音效</span>${renderStateTerms(composite, true)}</div>` : '';
        return {
          all: directHtml || compositeHtml ? `${directHtml}${compositeHtml}` : renderPills(fallbackTerms || []),
          missing: missingDirectHtml || missingCompositeHtml ? `${missingDirectHtml}${missingCompositeHtml}` : renderPills(fallbackMissingTerms || []),
        };
      }

      function renderMusicAnalysisPanels(data, report, panel) {
        const blocks = [];
        const safeReport = report && typeof report === 'object' ? report : {};
        const title = safeReport.title || '音乐分析结果';
        const summary = safeReport.summary || safeReport.markdown || '';
        blocks.push(`
          <section class="music-hero">
            <h4>${escHtml(title)}</h4>
            <div style="font-size:14px;line-height:1.6;">${escHtml(summary || '已生成音乐分析结论')}</div>
            <div class="music-grid" style="margin-top:10px;">
              <div class="music-stat"><div class="k">音频时长</div><div class="v">${fmtSec(data.duration_sec)}</div></div>
              <div class="music-stat"><div class="k">估计节奏</div><div class="v">${Number(data.bpm || 0).toFixed(1)} BPM</div></div>
              <div class="music-stat"><div class="k">乐段数量</div><div class="v">${Array.isArray(safeReport.sections) ? safeReport.sections.length : 0}</div></div>
              <div class="music-stat"><div class="k">命中点数量</div><div class="v">${Array.isArray(safeReport.hit_points) ? safeReport.hit_points.length : 0}</div></div>
            </div>
          </section>
        `);

        blocks.push(panel('题材适配建议', `
          <div class="kv"><div>适配题材</div><div>${renderPills(safeReport.fit_genres)}</div></div>
          <div class="kv"><div>风险题材</div><div>${renderPills(safeReport.risk_genres)}</div></div>
          <div class="kv"><div>基础标签</div><div>${renderPills(data.tags || [])}</div></div>
        `));

        const sections = Array.isArray(safeReport.sections) ? safeReport.sections : [];
        if (sections.length) {
          const cards = sections.map(s => `
            <article class="section-card">
              <div class="section-head">
                <strong>${escHtml(s.label || `乐段${s.section_no ?? ''}`)}</strong>
                <span class="badge">${escHtml(s.energy_level || '未标注')}</span>
              </div>
              <div class="kv-lite">时间区间：${fmtSec(s.start_sec)} - ${fmtSec(s.end_sec)}</div>
              <div class="kv-lite">主层次：${escHtml((s.main_layers || []).join('、') || '无')}</div>
              <div class="kv-lite">配器推测：${escHtml((s.instrument_guess || []).join('、') || '无')}</div>
              <div class="kv-lite">进入建议：${escHtml(s.entry_suggestion || '无')}</div>
              <div class="kv-lite">退出建议：${escHtml(s.exit_suggestion || '无')}</div>
            </article>
          `).join('');
          blocks.push(panel('乐段拆解（导演可读）', `<div class="section-cards">${cards}</div>`));
        }

        const hitPoints = Array.isArray(safeReport.hit_points) ? safeReport.hit_points : [];
        if (hitPoints.length) {
          const rows = hitPoints.map((h, idx) => `<tr>
            <td>${idx + 1}</td>
            <td>${fmtSec(h.time_sec)}</td>
            <td>${escHtml(h.type || '-')}</td>
            <td>${escHtml(h.usage || '-')}</td>
          </tr>`).join('');
          blocks.push(panel('命中点（动作/台词对齐）', `
            <table class="table-lite">
              <thead><tr><th>#</th><th>时间点</th><th>类型</th><th>创作用法</th></tr></thead>
              <tbody>${rows}</tbody>
            </table>
          `));
        }

        if (Array.isArray(safeReport.mix_notes) && safeReport.mix_notes.length) {
          blocks.push(panel('混音与后期建议', `<ul>${safeReport.mix_notes.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul>`));
        }
        if (Array.isArray(safeReport.key_points) && safeReport.key_points.length) {
          blocks.push(panel('关键结论', `<ul>${safeReport.key_points.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul>`));
        }
        if (safeReport.structure_logic && typeof safeReport.structure_logic === 'object') {
          const sl = safeReport.structure_logic;
          blocks.push(panel('结构规律', `
            <div class="kv"><div>结构猜测</div><div>${escHtml(sl.pattern_guess || '未识别')}</div></div>
            <div class="kv"><div>重复组</div><div>${escHtml((sl.repeat_groups || []).join('；') || '无')}</div></div>
            <div class="kv"><div>递进说明</div><div>${escHtml(sl.progression_comment || '无')}</div></div>
          `));
        }

        if (isFrontendDebugEnabled(data)) {
          const mapping = [
            ['title', '音乐标题/主题', safeReport.title || '-'],
            ['summary', '一句话创作定位', safeReport.summary || '-'],
            ['fit_genres', '适配题材', Array.isArray(safeReport.fit_genres) ? safeReport.fit_genres.join('、') : '-'],
            ['risk_genres', '不建议题材', Array.isArray(safeReport.risk_genres) ? safeReport.risk_genres.join('、') : '-'],
            ['sections', '乐段拆解', `${sections.length} 段`],
            ['hit_points', '命中点', `${hitPoints.length} 个`],
            ['mix_notes', '混音建议', `${Array.isArray(safeReport.mix_notes) ? safeReport.mix_notes.length : 0} 条`],
            ['key_points', '关键结论', `${Array.isArray(safeReport.key_points) ? safeReport.key_points.length : 0} 条`],
          ];
          const rows = mapping.map(([k, name, val]) => `<tr><td>${escHtml(k)}</td><td>${escHtml(name)}</td><td>${escHtml(String(val))}</td></tr>`).join('');
          blocks.push(panel('字段中文释义（便于创作者阅读）', `
            <table class="map-table">
              <thead><tr><th>原始 Key</th><th>中文含义</th><th>当前值摘要</th></tr></thead>
              <tbody>${rows}</tbody>
            </table>
          `));
        }
        return blocks;
      }

      function renderMusicMatchPanels(data, panel) {
        const blocks = [];
        const score = Number(data.score || 0);
        const verdict = data.verdict || '未判定';
        const summary = data.summary || '系统已根据当前赛道、文本和演绎证据生成音乐匹配判断。';
        const genreMatch = data.genre_match && typeof data.genre_match === 'object' ? data.genre_match : {};
        const textMatch = data.text_match && typeof data.text_match === 'object' ? data.text_match : {};
        const narrationMatch = data.narration_match && typeof data.narration_match === 'object' ? data.narration_match : {};
        const editingAdvice = data.editing_advice && typeof data.editing_advice === 'object' ? data.editing_advice : {};
        const replaceAdvice = data.replace_advice && typeof data.replace_advice === 'object' ? data.replace_advice : {};
        blocks.push(`
          <section class="music-hero">
            <h4>音乐是否适合作品</h4>
            <div style="font-size:14px;line-height:1.6;">${escHtml(summary)}</div>
            <div class="music-grid" style="margin-top:10px;">
              <div class="music-stat"><div class="k">综合结论</div><div class="v">${escHtml(verdict)}</div></div>
              <div class="music-stat"><div class="k">综合评分</div><div class="v">${score}/100</div></div>
              <div class="music-stat"><div class="k">赛道匹配</div><div class="v">${escHtml(genreMatch.verdict || '-')}</div></div>
              <div class="music-stat"><div class="k">文本匹配</div><div class="v">${escHtml(textMatch.verdict || '-')}</div></div>
            </div>
          </section>
        `);
        blocks.push(panel('赛道匹配判断', `
          <div class="kv"><div>当前项目赛道</div><div>${escHtml(genreMatch.current_genre || document.getElementById('projectGenreState')?.textContent || '-')}</div></div>
          <div class="kv"><div>音乐偏向赛道</div><div>${renderPills(genreMatch.music_bias_genres || [])}</div></div>
          <div class="kv"><div>匹配结论</div><div>${escHtml(genreMatch.verdict || '未判定')}（${Number(genreMatch.score || 0)}/100）</div></div>
          ${Array.isArray(genreMatch.reasons) && genreMatch.reasons.length ? `<ul>${genreMatch.reasons.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul>` : ''}
        `));
        blocks.push(panel('文本匹配判断', `
          <div class="kv"><div>匹配结论</div><div>${escHtml(textMatch.verdict || '未判定')}（${Number(textMatch.score || 0)}/100）</div></div>
          ${Array.isArray(textMatch.reasons) && textMatch.reasons.length ? `<ul>${textMatch.reasons.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul>` : ''}
        `));
        blocks.push(panel('演绎节奏匹配', `
          <div class="kv"><div>匹配结论</div><div>${escHtml(narrationMatch.verdict || '未判定')}${narrationMatch.available ? `（${Number(narrationMatch.score || 0)}/100）` : ''}</div></div>
          ${Array.isArray(narrationMatch.reasons) && narrationMatch.reasons.length ? `<ul>${narrationMatch.reasons.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul>` : ''}
        `));
        blocks.push(panel('剪辑建议', `
          <div class="kv"><div>建议进入点</div><div>${fmtSec(editingAdvice.intro_start_sec)}</div></div>
          <div class="kv"><div>建议高潮点</div><div>${fmtSec(editingAdvice.highlight_start_sec)}</div></div>
          <div class="kv"><div>建议淡出点</div><div>${fmtSec(editingAdvice.fade_out_sec)}</div></div>
          ${Array.isArray(editingAdvice.advice) && editingAdvice.advice.length ? `<ul>${editingAdvice.advice.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul>` : ''}
        `));
        if (replaceAdvice.need_replace) {
          blocks.push(panel('更换建议', `
            <div class="error" style="margin-bottom:10px;">当前音乐不建议作为该作品的主音乐继续使用。</div>
            ${Array.isArray(replaceAdvice.reasons) && replaceAdvice.reasons.length ? `<ul>${replaceAdvice.reasons.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul>` : ''}
            <div class="kv"><div>建议寻找的音乐方向</div><div>${renderPills(replaceAdvice.target_music_traits || [])}</div></div>
          `));
        }
        if (Array.isArray(data.key_points) && data.key_points.length) {
          blocks.push(panel('关键结论', `<ul>${data.key_points.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul>`));
        }
        if (Array.isArray(data.risks) && data.risks.length) {
          blocks.push(panel('使用提醒', `<ul>${data.risks.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul>`));
        }
        return blocks;
      }

      function renderLoginPanels(data, panel) {
        const blocks = [];
        const user = (data && data.user && typeof data.user === 'object') ? data.user : {};
        const usage = (data && data.usage && typeof data.usage === 'object') ? data.usage : {};
        const phone = String(user.phone || authPhone || '').trim();
        const uid = String(user.uid || '').trim();
        const tierCode = String(user.user_tier_code || usage.user_tier_code || '').trim();
        const tierLabel = tierCode === '22' ? '种子用户' : (tierCode === '33' ? '普通用户' : '当前用户');
        const inviteActivated = Boolean(Number(user.invite_activated || 0));
        blocks.push(panel('登录成功', `
          <div class="kv"><div>当前账号</div><div>${escHtml(phone || '未标注')}</div></div>
          <div class="kv"><div>账号 UID</div><div>${escHtml(uid || '系统自动生成')}</div></div>
          <div class="kv"><div>用户级别</div><div>${escHtml(tierLabel)}${tierCode ? `（${escHtml(tierCode)}）` : ''}</div></div>
          <div class="kv"><div>邀请码状态</div><div>${inviteActivated ? '已激活，可继续使用系统' : '当前未完成邀请码激活'}</div></div>
          <div class="kv"><div>建议下一步</div><div>现在可以继续创建项目，并依次体验音乐分析、动作提取、场景搭建与文本音乐执行单功能。</div></div>
        `));
        return blocks;
      }

      function renderRequestCodePanels(data, panel) {
        const blocks = [];
        const phone = String(data && data.phone || document.getElementById('loginPhone')?.value || '').trim();
        blocks.push(panel('验证码已发送', `
          <div class="kv"><div>手机号</div><div>${escHtml(phone || '未填写')}</div></div>
          <div class="kv"><div>下一步</div><div>请填写收到的验证码后登录。测试期间首次激活如需邀请码，请一并填写邀请码。</div></div>
        `));
        return blocks;
      }

      function renderActionVerbPanels(report, panel, meta) {
        const blocks = [];
        const candidates = Array.isArray(report.action_candidates) ? report.action_candidates : [];
        const qualified = Array.isArray(report.qualified_actions) ? report.qualified_actions : [];
        const rules = Array.isArray(report.rule_summary) ? report.rule_summary : [];
        const showDebugPanels = isFrontendDebugEnabled(report);
        if (showDebugPanels && meta && typeof meta === 'object') {
          const contractText = meta.contract_valid === true
            ? '通过'
            : (meta.contract_valid === false ? `不通过：${escHtml(meta.contract_reason || '')}` : '未校验');
          const modelText = meta.provider_used
            ? `${meta.provider_used}${meta.model_used ? ` / ${meta.model_used}` : ''}`
            : '未标注';
          blocks.push(panel('当前展示来源', `
            <div class="kv"><div>数据来源</div><div>${escHtml(meta.source || '-')}</div></div>
            <div class="kv"><div>提示词文件</div><div>${escHtml(meta.prompt_file || '-')}</div></div>
            <div class="kv"><div>赛道</div><div>${escHtml((report && report.genre) || meta.genre || '-')}</div></div>
            <div class="kv"><div>最终命中Prompt</div><div>${escHtml(meta.effective_prompt_file || meta.prompt_file || '-')}</div></div>
            <div class="kv"><div>实际调用模型</div><div>${escHtml(modelText)}</div></div>
            <div class="kv"><div>调用状态</div><div>${escHtml(meta.status || '-')}</div></div>
            <div class="kv"><div>Contract</div><div>${escHtml(contractText)}</div></div>
          `));
          if (meta.segmentation && typeof meta.segmentation === 'object') {
            const sg = meta.segmentation;
            blocks.push(panel('分段分析状态', `
              <div class="kv"><div>模式</div><div>${escHtml(sg.mode === 'segmented' ? '自动分段分析' : '单次分析')}</div></div>
              <div class="kv"><div>总字数</div><div>${sg.total_text_length ?? '-'}</div></div>
              <div class="kv"><div>分段数</div><div>${sg.segment_count ?? '-'}</div></div>
              <div class="kv"><div>单次安全阈值</div><div>${sg.single_pass_safe_chars ?? '-'} 字</div></div>
              <div class="kv"><div>目标分段长度</div><div>${sg.segment_target_chars ?? '-'} 字</div></div>
            `));
          }
        }
        blocks.push(panel('人物动作判定规则', `<ul>${rules.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul>`));
        blocks.push(panel('提取结果概览', `
          <div class="kv"><div>候选动词数</div><div>${candidates.length}</div></div>
          <div class="kv"><div>需做动作音效</div><div>${qualified.length}</div></div>
          <div class="kv"><div>功能标题</div><div>${escHtml(report.title || '人物动作动词提取')}</div></div>
        `));
        if (candidates.length) {
          const rows = candidates.map(r => `<tr>
            <td>${r.candidate_no ?? '-'}</td>
            <td>${escHtml(r.verb || '')}</td>
            <td>${escHtml(r.sentence_excerpt || '')}</td>
          </tr>`).join('');
          blocks.push(panel('动作提取明细列表', `<table class="table-lite"><thead><tr><th>#</th><th>动词</th><th>原句片段</th></tr></thead><tbody>${rows}</tbody></table>`));
        }
        if (Array.isArray(report.key_points) && report.key_points.length) {
          blocks.push(panel('关键结论', `<ul>${report.key_points.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul>`));
        }
        if (Array.isArray(report.risks) && report.risks.length) {
          blocks.push(panel('复核提醒', `<ul>${report.risks.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul>`));
        }
        if (report.markdown) {
          blocks.push(panel('模型完整说明', `<div class="md">${mdToHtml(report.markdown)}</div>`));
        }
        const segReports = meta && Array.isArray(meta.segment_reports) ? meta.segment_reports : [];
        if (showDebugPanels && segReports.length > 1) {
          const rows = segReports.map(seg => {
            const segReport = seg.report_json || {};
            const count = Array.isArray(segReport.action_candidates) ? segReport.action_candidates.length : 0;
            const excerpt = middleEllipsis(seg.segment_text || '', 20, 16);
            const contractText = seg.contract_valid === true
              ? '通过'
              : (seg.contract_valid === false ? `不通过：${String(seg.contract_reason || '未说明')}` : '未校验');
            const fallbackText = seg.fallback_applied ? '是' : '否';
            return `<tr>
              <td>${seg.segment_no ?? '-'}</td>
              <td>${seg.start_char ?? '-'} ~ ${seg.end_char ?? '-'}</td>
              <td>${seg.text_length ?? '-'}</td>
              <td>${escHtml(excerpt)}</td>
              <td>${count}</td>
              <td>${escHtml(contractText)}</td>
              <td>${escHtml(fallbackText)}</td>
              <td>${escHtml(seg.effective_prompt_file || '-')}</td>
            </tr>`;
          }).join('');
          blocks.push(panel('分段明细（系统逐段调用后合并）', `<table class="table-lite"><thead><tr><th>段号</th><th>字符区间</th><th>段字数</th><th>该段缩略原文</th><th>候选数</th><th>Contract</th><th>Fallback</th><th>Prompt</th></tr></thead><tbody>${rows}</tbody></table>`));
        }
        if (showDebugPanels && !candidates.length && !qualified.length) {
          blocks.push(panel('LLM 原始结构化结果', `<pre>${escHtml(JSON.stringify(report, null, 2))}</pre>`));
        }
        return blocks;
      }

      function renderActionGraphPanels(data, panel) {
        const blocks = [];
        const items = Array.isArray(data.graph_items) ? data.graph_items : [];
        const summary = data.summary || {};
        const gapSummary = data.asset_gap_summary || {};
        const gapItems = Array.isArray(gapSummary.gap_items) ? gapSummary.gap_items : [];
        const groupAssetsByLabel = (assets) => {
          const groups = [];
          const byLabel = new Map();
          (Array.isArray(assets) ? assets : []).forEach(asset => {
            const label = String((asset && asset.label) || (asset && asset.asset_label) || '').trim();
            const key = String((asset && asset.variant_key) || '').trim()
              || String((asset && asset.display_name) || '').trim()
              || label
              || '未命名音效词';
            if (!byLabel.has(key)) {
              const bucket = {
                key,
                label,
                display_name: String((asset && asset.display_name) || label || key).trim(),
                assets: [],
              };
              byLabel.set(key, bucket);
              groups.push(bucket);
            }
            byLabel.get(key).assets.push(asset);
          });
          return groups;
        };
        const renderGraphNodes = (arr, kind, suffix = '') => {
          if (!Array.isArray(arr) || !arr.length) return '<div class="hint">暂无</div>';
          return arr.map(x => `
            <div class="graph-node ${kind}">
              <div class="node-name">${escHtml(x)}${suffix}</div>
              <div class="node-meta">${kind === 'direct' ? '直达音效' : kind === 'composite' ? '整体音效' : '语义关联'}</div>
            </div>
          `).join('');
        };
        blocks.push(panel('动作图谱总览', `
          <div class="kv"><div>赛道</div><div>${escHtml(data.genre || '-')}</div></div>
          <div class="kv"><div>动作词数</div><div>${summary.verb_count ?? items.length}</div></div>
          <div class="kv"><div>素材候选数</div><div>${summary.asset_count ?? 0}</div></div>
          <div class="kv"><div>素材缺口词数</div><div>${summary.gap_count ?? gapItems.length}</div></div>
        `));
        const cards = items.map(item => {
          const assets = Array.isArray(item.assets) ? item.assets : [];
          const assetGroups = groupAssetsByLabel(assets);
          const parentNode = item.parent_node || {};
          const childNode = item.children || {};
          const graphSource = item.graph_source || {};
          const actionHead = String(item.verb || parentNode.verb_head || '').trim();
          const semanticTermItems = filterUserVisibleActionTermItems(childNode.semantic_term_items, actionHead);
          const directTermItems = filterUserVisibleActionTermItems(childNode.direct_sfx_term_items, actionHead);
          const compositeTermItems = filterUserVisibleActionTermItems(childNode.composite_sfx_term_items, actionHead);
          const visibleSemanticTerms = filterUserVisibleActionTerms(childNode.semantic_terms || item.semantic_terms || [], actionHead);
          const visibleDirectTerms = filterUserVisibleActionTerms(childNode.direct_sfx_terms || [], actionHead);
          const visibleCompositeTerms = filterUserVisibleActionTerms(childNode.composite_sfx_terms || [], actionHead);
          const visibleMissingTerms = filterUserVisibleActionTerms(childNode.missing_sfx_terms || item.missing_sfx_terms || [], actionHead);
          const sfxBuckets = renderSfxBuckets(
            childNode,
            filterUserVisibleActionTerms(childNode.sfx_terms || item.sfx_terms || [], actionHead),
            visibleMissingTerms
          );
          const assetRows = assetGroups.length
            ? assetGroups.map((group, idx) => {
                const primary = group.assets[0] || {};
                const extraAssets = group.assets.slice(1);
                const extraRows = extraAssets.map((a, extraIdx) => `
                  <tr>
                    <td>${extraIdx + 1}</td>
                    <td>${escHtml(a.file_display_name || a.file_name || '')}</td>
                    <td>${a.score ?? '-'}</td>
                    <td><button onclick="downloadActionSfxAsset('${escAttr(item.verb || '')}','${escAttr(a.label || '')}','${escAttr(a.file_name || 'sfx.bin')}','${escAttr(a.download_api || '')}','action','${escAttr(data.genre || '')}','${escAttr(a.scope_label || '')}','${escAttr(a.display_name || a.label || '')}')">下载</button></td>
                  </tr>
                `).join('');
                return `
                  <tr>
                    <td>${idx + 1}</td>
                    <td>${escHtml(primary.display_name || group.display_name || group.label || '')}</td>
                    <td>${escHtml(primary.file_display_name || primary.file_name || '')}</td>
                    <td>${primary.score ?? '-'}</td>
                    <td><button onclick="downloadActionSfxAsset('${escAttr(item.verb || '')}','${escAttr(primary.label || '')}','${escAttr(primary.file_name || 'sfx.bin')}','${escAttr(primary.download_api || '')}','action','${escAttr(data.genre || '')}','${escAttr(primary.scope_label || '')}','${escAttr(primary.display_name || primary.label || '')}')">下载</button></td>
                  </tr>
                  ${extraAssets.length ? `
                    <tr>
                      <td></td>
                      <td colspan="4">
                        <details class="asset-candidate-details">
                          <summary>展开另外 ${extraAssets.length} 条候选</summary>
                          <table class="table-lite">
                            <thead><tr><th>#</th><th>素材</th><th>分数</th><th>下载</th></tr></thead>
                            <tbody>${extraRows}</tbody>
                          </table>
                        </details>
                      </td>
                    </tr>
                  ` : ''}
                `;
              }).join('')
            : `<tr><td colspan="5" class="hint">当前没有命中素材</td></tr>`;
          const graphHtml = `
            <div class="graph-browser">
              <div class="graph-stage">
                <div class="graph-center">
                  <div style="font-size:17px; font-weight:700;">${escHtml((parentNode.verb_head || item.verb || '动作词'))}</div>
                  <div class="asset-meta" style="margin-top:6px;">父节点：${escHtml((parentNode.genre || data.genre || '-') + '::' + (parentNode.verb_head || item.verb || '-'))}</div>
                  <div class="asset-meta" style="margin-top:6px;">原句：${escHtml(item.sentence_excerpt || '无')}</div>
                  <div class="status-bar" style="justify-content:center; margin-top:10px;">
                    <span class="badge">直达 ${escHtml(visibleDirectTerms.length)}</span>
                    <span class="badge">整体 ${escHtml(visibleCompositeTerms.length)}</span>
                    <span class="badge">缺口 ${escHtml(visibleMissingTerms.length)}</span>
                  </div>
                </div>
                <div class="graph-lane-row">
                  <div class="graph-lane">
                    <h5>语义子级</h5>
                    ${renderGraphNodes(visibleSemanticTerms, 'semantic')}
                  </div>
                  <div class="graph-lane">
                    <h5>直达音效</h5>
                    ${renderGraphNodes(visibleDirectTerms, 'direct')}
                    <h5 style="margin-top:10px;">整体音效</h5>
                    ${renderGraphNodes(visibleCompositeTerms, 'composite', '（整体）')}
                  </div>
                </div>
              </div>
            </div>
          `;
          return `
            <article class="section-card">
              <div class="section-head">
                <strong>${escHtml(item.verb || '动作词')}</strong>
                <span class="badge">${escHtml(item.reason || '图谱推荐')}</span>
              </div>
              <div class="view-switch">
                <button class="view-chip active" type="button" onclick="toggleActionGraphView('${escAttr(item.verb || '')}', 'list', this)">列表视图</button>
                <button class="view-chip" type="button" onclick="toggleActionGraphView('${escAttr(item.verb || '')}', 'graph', this)">图谱视图</button>
              </div>
              <div id="actionGraphList_${escAttr(item.verb || '')}">
                <div class="kv-lite"><span class="kv-title">父级节点</span>${escHtml((parentNode.genre || data.genre || '-') + ' -> ' + (parentNode.verb_head || item.verb || '-'))}</div>
                <div class="kv-lite"><span class="kv-title">原句片段</span>${escHtml(item.sentence_excerpt || '无')}</div>
                <div class="kv-lite"><span class="kv-title">图谱命中</span>${escHtml(
                  graphSource
                    ? `通用层=${graphSource.common_hit ? '是' : '否'} / 赛道层=${graphSource.genre_hit ? '是' : '否'}`
                    : '未标注'
                )}</div>
                <div class="kv-lite"><span class="kv-title">语义扩展词</span>${semanticTermItems.length ? renderTermItemsWithSource(semanticTermItems) : renderPills(visibleSemanticTerms)}</div>
                <div class="kv-lite"><span class="kv-title">直达音效来源(可下载)</span>${directTermItems.length ? renderTermItemsWithSource(directTermItems) : renderPills(visibleDirectTerms)}</div>
                <div class="kv-lite"><span class="kv-title">整体音效来源(可下载)</span>${compositeTermItems.length ? renderTermItemsWithSource(compositeTermItems, '（整体）') : renderPills(visibleCompositeTerms.map(x => `${x}（整体）`))}</div>
                <div class="kv-lite"><span class="kv-title">待补子级音效</span>${sfxBuckets.missing}</div>
                <div class="kv-lite"><span class="kv-title">素材覆盖</span>已覆盖 ${escHtml((childNode.covered_sfx_terms || []).length)} 个音效词，当前命中 ${escHtml(assetGroups.length)} 个词、共 ${escHtml(assets.length)} 条素材候选</div>
                <table class="table-lite" style="margin-top:8px;">
                  <thead><tr><th>#</th><th>音效词</th><th>最佳素材</th><th>分数</th><th>下载</th></tr></thead>
                  <tbody>${assetRows}</tbody>
                </table>
              </div>
              <div id="actionGraphGraph_${escAttr(item.verb || '')}" style="display:none;">
                ${graphHtml}
              </div>
            </article>
          `;
        }).join('');
        blocks.push(panel('动作词 -> 语义扩展 -> 音效素材', `<div class="action-graph-cards">${cards || '<div class="hint">暂无图谱结果</div>'}</div>`));
        if (gapItems.length) {
          const rows = gapItems.map((g, idx) => `<tr>
            <td>${idx + 1}</td>
            <td>${escHtml(g.sfx_term || '')}</td>
            <td>${escHtml((g.verbs || []).join('、') || '无')}</td>
            <td>${escHtml((g.examples || []).slice(0, 2).join('；') || '无')}</td>
            <td>${g.missing_count ?? 0}</td>
          </tr>`).join('');
          blocks.push(panel('素材缺口面板', `<table class="table-lite"><thead><tr><th>#</th><th>缺口音效词</th><th>关联动作词</th><th>示例片段</th><th>出现次数</th></tr></thead><tbody>${rows}</tbody></table>`));
        }
        return blocks;
      }

      function toggleActionGraphView(key, mode, btn) {
        const safe = String(key || '');
        const list = document.getElementById(`actionGraphList_${safe}`);
        const graph = document.getElementById(`actionGraphGraph_${safe}`);
        if (!list || !graph) return;
        list.style.display = mode === 'list' ? '' : 'none';
        graph.style.display = mode === 'graph' ? '' : 'none';
        const root = btn && btn.parentElement;
        if (root) root.querySelectorAll('.view-chip').forEach(x => x.classList.remove('active'));
        if (btn) btn.classList.add('active');
      }

      function renderActionGraphDraftPanels(data, panel) {
        const blocks = [];
        const items = Array.isArray(data.draft_items) ? data.draft_items : [];
        const scopeLabel = (item) => {
          const scope = String(item && item.task_scope || '').trim();
          const genre = String(item && (item.task_scope_genre || data.genre) || '').trim();
          if (scope === 'common') return '通用版';
          if (scope === 'genre') return `${genre || '赛道'}版`;
          return '未标注版本';
        };
        blocks.push(panel('动作补充单总览', `
          <div class="kv"><div>赛道</div><div>${escHtml(data.genre || '-')}</div></div>
          <div class="kv"><div>草稿数量</div><div>${data.draft_count ?? items.length}</div></div>
        `));
        if (items.length) {
          const rows = items.map((it, idx) => `<tr>
            <td>${idx + 1}</td>
            <td>${escHtml(it.verb || '')}</td>
            <td>${escHtml(scopeLabel(it))}</td>
            <td>${escHtml((it.semantic_terms || []).join('、') || '无')}</td>
            <td>${escHtml((((it.missing_sfx_terms_classified || {}).display_terms) || it.missing_sfx_terms || []).join('、') || '无')}</td>
          </tr>`).join('');
          blocks.push(panel('动作补充单明细', `<table class="table-lite"><thead><tr><th>#</th><th>动作词</th><th>目标版本</th><th>关联语义</th><th>待补充音效词</th></tr></thead><tbody>${rows}</tbody></table><div class="hint" style="margin-top:10px; font-weight:700; color:#9f2f2a;">补充单已经提交系统，待系统完成补充后，会给您手机号发送补足提醒，敬请期待。</div>`));
        }
        return blocks;
      }

      function renderSceneBuildingPanels(data, panel) {
        const blocks = [];
        const items = Array.isArray(data.scene_items) ? data.scene_items : [];
        const summary = data.summary || {};
        const getSceneBuildQuality = (item) => {
          const graphSource = item?.scene_graph_source || {};
          if (graphSource.has_fallback_terms) {
            return { label: '结果层级：保底结果', cls: 'warn', note: '当前主要依赖系统保底结果，后续仍建议继续完善 scene 图谱。' };
          }
          if (graphSource.genre_hit) {
            return { label: '结果层级：稳定图谱', cls: 'source', note: '当前已命中赛道层图谱，场景基础较稳定。' };
          }
          if (graphSource.common_hit) {
            return { label: '结果层级：通用图谱', cls: 'collection', note: '当前主要命中通用层图谱，适合继续确认是否需要赛道特化。' };
          }
          if (graphSource.template_hit) {
            return { label: '结果层级：模板过渡', cls: 'template', note: '当前主要依赖模板命中，适合继续确认映射出的场景音效是否准确。' };
          }
          return { label: '结果层级：待完善', cls: 'warn', note: '当前还没有稳定图谱支撑，建议优先补 scene 图谱。' };
        };
        const commonHitCount = items.filter(item => item?.scene_graph_source?.common_hit).length;
        const genreHitCount = items.filter(item => item?.scene_graph_source?.genre_hit).length;
        const templateHitCount = items.filter(item => String(item?.scene_graph_source?.template_hit || '').trim()).length;
        const fallbackCount = items.filter(item => item?.scene_graph_source?.has_fallback_terms).length;
        const sceneChangeCount = items.filter(item => item?.is_scene_change).length;
        const stableCount = items.filter(item => {
          const q = getSceneBuildQuality(item);
          return q.label === '结果层级：稳定图谱' || q.label === '结果层级：通用图谱';
        }).length;
        blocks.push(panel('场景搭建总览', `
          <div class="scene-badge-row" style="margin-bottom:10px;">
            <span class="scene-badge source">当前阶段：场景识别</span>
            <span class="scene-badge template">下一步：4.5 场景音效推荐</span>
          </div>
          <div class="kv"><div>赛道</div><div>${escHtml(data.genre || '-')}</div></div>
          <div class="kv"><div>场景数</div><div>${summary.scene_count ?? items.length}</div></div>
          <div class="kv"><div>素材候选数</div><div>${summary.asset_count ?? 0}</div></div>
          <div class="kv"><div>缺口词数</div><div>${summary.gap_count ?? 0}</div></div>
          <div class="kv"><div>分析模式</div><div>${escHtml(data.analysis_mode || '未标注')}</div></div>
          <div class="kv"><div>场景切换点</div><div>${sceneChangeCount}</div></div>
          <div class="kv"><div>结果较稳定场景</div><div>${stableCount}</div></div>
          <div class="kv"><div>模板命中场景</div><div>${templateHitCount}</div></div>
          <div class="kv"><div>通用层命中</div><div>${commonHitCount}</div></div>
          <div class="kv"><div>赛道层命中</div><div>${genreHitCount}</div></div>
          <div class="kv"><div>保底生成</div><div>${fallbackCount}</div></div>
          <div class="hint" style="margin-top:10px;">这一步先回答“文本里有哪些场景、哪些地方需要换场景、每个场景由哪些元素和环境音效构成”；确认无误后，再进入 4.5 步做场景音效推荐。</div>
        `));
        if (fallbackCount > 0) {
          const fallbackNames = items
            .filter(item => item?.scene_graph_source?.has_fallback_terms)
            .map(item => String(item.scene_name || '').trim())
            .filter(Boolean)
            .slice(0, 8);
          blocks.push(panel('保底生成提醒', `
            <div class="hint">
              当前有 <strong>${fallbackCount}</strong> 个场景主要依赖系统保底结果，并非稳定图谱命中。
              ${fallbackNames.length ? `涉及：${escHtml(fallbackNames.join('、'))}。` : ''}
              这类场景后续可能随着 scene 图谱维护继续变化。
            </div>
          `));
        }
        const getSceneBuildNextStep = (item) => {
          const graphSource = item?.scene_graph_source || {};
          if (graphSource.has_fallback_terms) {
            return '当前场景主要依赖保底结果，建议后续优先在 scene 图谱里补齐这个场景的模板、集合和场景音效词。';
          }
          if (graphSource.template_hit && !(graphSource.common_hit || graphSource.genre_hit)) {
            return '当前场景主要命中模板层，建议继续进入 4.5 步确认模板映射出来的场景音效是否足够准确。';
          }
          if (graphSource.genre_hit) {
            return '当前场景已经命中赛道层图谱，可以继续进入 4.5 步检查可下载素材和缺口。';
          }
          if (graphSource.common_hit) {
            return '当前场景主要命中通用层图谱，适合继续进入 4.5 步确认是否需要赛道特化补充。';
          }
          return '当前场景还没有稳定图谱支撑，建议后续优先补 scene 图谱，再进入音效推荐。';
        };
        const cards = items.map((item, idx) => {
          const graphSource = item.scene_graph_source || {};
          const quality = getSceneBuildQuality(item);
          const sourceType = graphSource.common_hit && graphSource.genre_hit
            ? '通用层+赛道层'
            : graphSource.genre_hit
              ? '赛道层'
              : graphSource.common_hit
                ? '通用层'
                : (graphSource.template_hit ? '模板命中' : (graphSource.has_fallback_terms ? '保底生成' : '未命中图谱'));
          const sourceText = [
            `来源类型：${sourceType}`,
            `模板命中：${graphSource.template_hit ? graphSource.template_hit : '未命中'}`,
            graphSource.collection_name ? `场景集合：${graphSource.collection_name}` : '',
            graphSource.has_fallback_terms ? '含保底词' : '',
          ].filter(Boolean).join(' / ');
          const sourceBadges = [
            `<span class="scene-badge ${escAttr(quality.cls)}">${escHtml(quality.label)}</span>`,
            `<span class="scene-badge source">来源：${escHtml(sourceType)}</span>`,
            graphSource.template_hit ? `<span class="scene-badge template">模板：${escHtml(graphSource.template_hit)}</span>` : '',
            graphSource.collection_name ? `<span class="scene-badge collection">集合：${escHtml(graphSource.collection_name)}</span>` : '',
            graphSource.has_fallback_terms ? `<span class="scene-badge warn">含保底词</span>` : '',
          ].filter(Boolean).join('');
          return `
            <article class="section-card">
              <div class="section-head">
                <strong>${escHtml(item.scene_name || `场景${idx + 1}`)}</strong>
                <span class="badge">${escHtml(item.is_scene_change ? '场景切换点' : '沿用上个场景')}</span>
              </div>
              <div class="scene-badge-row">${sourceBadges}</div>
              <div class="kv-lite"><span class="kv-title">原句片段</span>${escHtml(middleEllipsis(item.sentence_excerpt || '无'))}</div>
              <div class="kv-lite"><span class="kv-title">时间词</span>${renderPills(item.time_terms)}</div>
              <div class="kv-lite"><span class="kv-title">地点词</span>${renderPills(item.location_terms)}</div>
              <div class="kv-lite">${renderSceneFieldLabel('背景元素', 'text')}${renderScenePills(item.background_elements, 'text')}</div>
              <div class="kv-lite">${renderSceneFieldLabel('特征元素', 'text')}${renderScenePills(item.feature_elements, 'text')}</div>
              <div class="kv-lite">${renderSceneFieldLabel('细节元素', 'text')}${renderScenePills(item.detail_elements, 'text')}</div>
              <div class="kv-lite">${renderSceneFieldLabel('支撑场景音效', 'sfx')}${renderScenePills(item.supporting_sfx_terms, 'sfx')}</div>
              <div class="kv-lite">${renderSceneFieldLabel('补充层次音效', 'sfx')}${renderScenePills(item.detail_sfx_terms, 'sfx')}</div>
              <div class="kv-lite"><span class="kv-title">不归场景模块</span>${renderPills(item.excluded_action_terms)}</div>
              <div class="explain-box">
                <h5>场景判断</h5>
                <ul class="reason-list">
                  <li>${escHtml(quality.note)}</li>
                  <li>${escHtml(item.scene_change_reason || '未标注')}</li>
                  <li>${escHtml(sourceText || '当前未标注图谱来源')}</li>
                </ul>
              </div>
              <div class="hint" style="margin-top:8px;">建议下一步：${escHtml(getSceneBuildNextStep(item))}</div>
            </article>
          `;
        }).join('');
        blocks.push(panel('场景卡片', `<div class="action-graph-cards">${cards || '<div class="hint">暂无场景结果</div>'}</div>`));
        if (Array.isArray(data.key_points) && data.key_points.length) {
          blocks.push(panel('关键结论', `<ul>${data.key_points.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul>`));
        }
        if (Array.isArray(data.risks) && data.risks.length) {
          blocks.push(panel('复核提醒', `<ul>${data.risks.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul>`));
        }
        return blocks;
      }

      function renderSceneSfxPanels(data, panel) {
        const blocks = [];
        const items = Array.isArray(data.scene_items) ? data.scene_items : [];
        const summary = data.summary || {};
        const gapSummary = data.asset_gap_summary || {};
        const gapItems = Array.isArray(gapSummary.gap_items) ? gapSummary.gap_items : [];
        const getSceneSfxQuality = (item) => {
          const missingCount = Array.isArray(item?.missing_scene_sfx_terms) ? item.missing_scene_sfx_terms.length : 0;
          const assetCount = Array.isArray(item?.assets) ? item.assets.length : 0;
          const graphSource = item?.scene_graph_source || {};
          if (assetCount > 0 && missingCount === 0) {
            return { label: '结果层级：可直接使用', cls: 'source', note: '当前场景已命中可下载素材，可以直接进入试听与使用。' };
          }
          if (assetCount > 0 && missingCount > 0) {
            return { label: '结果层级：部分命中', cls: 'collection', note: '当前已命中部分素材，但仍有场景音效缺口。' };
          }
          if (missingCount > 0 && graphSource.has_fallback_terms) {
            return { label: '结果层级：保底待补', cls: 'warn', note: '当前同时依赖保底结果和待补词，建议优先回到 scene 图谱校准。' };
          }
          if (missingCount > 0) {
            return { label: '结果层级：待补素材', cls: 'template', note: '当前图谱方向基本可用，但还需要继续补素材。' };
          }
          return { label: '结果层级：待确认', cls: 'warn', note: '当前还没有形成稳定可下载结果，建议继续检查来源和模板映射。' };
        };
        const commonHitCount = items.filter(item => item?.scene_graph_source?.common_hit).length;
        const genreHitCount = items.filter(item => item?.scene_graph_source?.genre_hit).length;
        const templateHitCount = items.filter(item => String(item?.scene_graph_source?.template_hit || '').trim()).length;
        const assetSceneCount = items.filter(item => Array.isArray(item.assets) && item.assets.length).length;
        const readyUseCount = items.filter(item => getSceneSfxQuality(item).label === '结果层级：可直接使用').length;
        blocks.push(panel('场景音效推荐总览', `
          <div class="scene-badge-row" style="margin-bottom:10px;">
            <span class="scene-badge source">当前阶段：图谱命中与素材召回</span>
            <span class="scene-badge template">下一步：4.6 场景补充单</span>
          </div>
          <div class="kv"><div>赛道</div><div>${escHtml(data.genre || '-')}</div></div>
          <div class="kv"><div>场景数</div><div>${summary.scene_count ?? items.length}</div></div>
          <div class="kv"><div>素材候选数</div><div>${summary.asset_count ?? 0}</div></div>
          <div class="kv"><div>缺口词数</div><div>${summary.gap_count ?? gapItems.length}</div></div>
          <div class="kv"><div>已有素材场景</div><div>${assetSceneCount}</div></div>
          <div class="kv"><div>可直接使用场景</div><div>${readyUseCount}</div></div>
          <div class="kv"><div>模板命中场景</div><div>${templateHitCount}</div></div>
          <div class="kv"><div>通用层命中</div><div>${commonHitCount}</div></div>
          <div class="kv"><div>赛道层命中</div><div>${genreHitCount}</div></div>
          <div class="hint" style="margin-top:10px;">这一步回答“这些场景已经命中了哪些可下载音效、还缺哪些场景音效词”；如果缺口仍多，再进入 4.6 步生成场景补充单。</div>
        `));
        const getSceneSfxNextStep = (item) => {
          const missingCount = Array.isArray(item?.missing_scene_sfx_terms) ? item.missing_scene_sfx_terms.length : 0;
          const assetCount = Array.isArray(item?.assets) ? item.assets.length : 0;
          const graphSource = item?.scene_graph_source || {};
          if (missingCount > 0 && graphSource.has_fallback_terms) {
            return '当前场景同时存在保底结果和素材缺口，建议先在 scene 图谱中校准场景，再进入 4.6 步生成补充单。';
          }
          if (missingCount > 0) {
            return '当前场景已有部分推荐，但仍有缺口，建议进入 4.6 步写入场景补充单，由 scene 后台继续补库。';
          }
          if (assetCount > 0) {
            return '当前场景已经命中可下载素材，可优先使用这些结果；如果听感仍不理想，再回到 scene 图谱微调。';
          }
          return '当前场景还没有命中可下载素材，建议继续检查图谱来源和模板映射，再决定是否生成补充单。';
        };
        const cards = items.map((item, idx) => {
          const assets = Array.isArray(item.assets) ? item.assets : [];
          const matchedSceneSfxTerms = buildSceneMatchedSfxSet(assets);
          const graphSource = item.scene_graph_source || {};
          const quality = getSceneSfxQuality(item);
          const sourceType = graphSource.common_hit && graphSource.genre_hit
            ? '通用层+赛道层'
            : graphSource.genre_hit
              ? '赛道层'
              : graphSource.common_hit
                ? '通用层'
                : (graphSource.template_hit ? '模板命中' : (graphSource.has_fallback_terms ? '保底生成' : '未命中图谱'));
          const assetRows = assets.length
            ? assets.map((a, assetIdx) => `
              <tr class="asset-hit">
                <td>${assetIdx + 1}</td>
                <td>${escHtml(a.display_name || a.file_name || a.label || '')}</td>
                <td>${escHtml(a.scope_label || '未标注')}</td>
                <td>${a.score ?? '-'}</td>
                <td>${a.download_api ? `<button onclick="downloadActionSfxAsset('${escAttr(item.scene_name || '')}','${escAttr(a.label || '')}','${escAttr(a.file_name || 'scene_sfx.bin')}','${escAttr(a.download_api || '')}','scene','${escAttr(data.genre || '')}','${escAttr(a.scope_label || '')}','${escAttr(a.display_name || a.label || '')}')">下载</button>` : '暂无'}</td>
              </tr>
            `).join('')
            : '<tr><td colspan="5" class="hint">当前没有命中素材</td></tr>';
          const sourceBadges = [
            `<span class="scene-badge ${escAttr(quality.cls)}">${escHtml(quality.label)}</span>`,
            `<span class="scene-badge source">来源：${escHtml(sourceType)}</span>`,
            graphSource.template_hit ? `<span class="scene-badge template">模板：${escHtml(graphSource.template_hit)}</span>` : '',
            graphSource.collection_name ? `<span class="scene-badge collection">集合：${escHtml(graphSource.collection_name)}</span>` : '',
            graphSource.has_fallback_terms ? `<span class="scene-badge warn">含保底词</span>` : '',
          ].filter(Boolean).join('');
          return `
            <article class="section-card">
              <div class="section-head">
                <strong>${escHtml(item.scene_name || `场景${idx + 1}`)}</strong>
                <span class="badge">${escHtml(item.node_key || '未生成节点')}</span>
              </div>
              <div class="scene-badge-row">${sourceBadges}</div>
              <div class="kv-lite"><span class="kv-title">原句片段</span>${escHtml(middleEllipsis(item.sentence_excerpt || '无'))}</div>
              <div class="kv-lite"><span class="kv-title">时间词</span>${renderPills(item.time_terms)}</div>
              <div class="kv-lite"><span class="kv-title">地点词</span>${renderPills(item.location_terms)}</div>
              <div class="kv-lite">${renderSceneFieldLabel('背景元素', 'text')}${renderScenePills(item.background_elements, 'text')}</div>
              <div class="kv-lite">${renderSceneFieldLabel('特征元素', 'text')}${renderScenePills(item.feature_elements, 'text')}</div>
              <div class="kv-lite">${renderSceneFieldLabel('细节元素', 'text')}${renderScenePills(item.detail_elements, 'text')}</div>
              <div class="kv-lite">${renderSceneFieldLabel('支撑场景音效(可下载)', 'sfx')}${renderScenePills(item.supporting_sfx_terms, 'sfx', matchedSceneSfxTerms)}</div>
              <div class="kv-lite">${renderSceneFieldLabel('补充层次音效(可下载)', 'sfx')}${renderScenePills(item.detail_sfx_terms, 'sfx', matchedSceneSfxTerms)}</div>
              <div class="kv-lite">${renderSceneFieldLabel('待补场景音效', 'sfx')}${renderScenePills(item.missing_scene_sfx_terms, 'sfx-missing')}</div>
              <div class="hint" style="margin-top:8px;">${escHtml(quality.note)}</div>
              <table class="table-lite" style="margin-top:8px;">
                <thead><tr><th>#</th><th>素材</th><th>版本</th><th>分数</th><th>下载</th></tr></thead>
                <tbody>${assetRows}</tbody>
              </table>
              <div class="hint" style="margin-top:8px;">建议下一步：${escHtml(getSceneSfxNextStep(item))}</div>
            </article>
          `;
        }).join('');
        blocks.push(panel('场景 -> 环境层 -> 音效素材', `<div class="action-graph-cards">${cards || '<div class="hint">暂无场景音效结果</div>'}</div>`));
        if (gapItems.length) {
          const rows = gapItems.map((g, idx) => `<tr>
            <td>${idx + 1}</td>
            <td>${escHtml(g.sfx_term || '')}</td>
            <td>${escHtml((g.scenes || []).join('、') || '无')}</td>
            <td>${escHtml(((g.examples || []).slice(0, 2).map(x => middleEllipsis(x, 18, 12))).join('；') || '无')}</td>
            <td>${g.missing_count ?? 0}</td>
          </tr>`).join('');
          blocks.push(panel('场景素材缺口面板', `<table class="table-lite"><thead><tr><th>#</th><th>缺口音效词</th><th>关联场景</th><th>示例片段</th><th>影响场景数</th></tr></thead><tbody>${rows}</tbody></table><div class="hint" style="margin-top:8px;">“影响场景数”表示这个缺口词在多少个场景结果项中被判定为待补，不是原文逐字出现次数。</div>`));
        }
        return blocks;
      }

      function renderSceneSupplementPanels(data, panel) {
        const blocks = [];
        const items = Array.isArray(data.items) ? data.items : [];
        const getSceneSupplementStatusLabel = (status) => {
          const key = String(status || '').trim();
          if (key === 'pending') return '待后台处理';
          if (key === 'ready_to_notify') return '待补素材';
          if (key === 'merged') return '处理中';
          if (key === 'notified') return '已通知';
          return key || '未标注';
        };
        const getSceneSupplementQuality = (item) => {
          const missingCount = Number(item?.missing_count ?? item?.progress?.pending_count ?? 0);
          const status = String(item?.status || '').trim();
          if (missingCount <= 0) {
            return { label: '结果层级：已补齐', cls: 'source' };
          }
          if (status === 'ready_to_notify') {
            return { label: '结果层级：待补素材', cls: 'collection' };
          }
          if (status === 'merged' || status === 'notified') {
            return { label: '结果层级：处理中', cls: 'template' };
          }
          return { label: '结果层级：待系统后台确认', cls: 'warn' };
        };
        const backendReadyCount = items.filter(item => getSceneSupplementQuality(item).label === '结果层级：待补素材').length;
        const reviewCount = items.filter(item => getSceneSupplementQuality(item).label === '结果层级：待系统后台确认').length;
        blocks.push(panel('场景补充单总览', `
          <div class="scene-badge-row" style="margin-bottom:10px;">
            <span class="scene-badge source">当前阶段：缺口写入补充单</span>
            <span class="scene-badge warn">后续在 scene 后台补库</span>
          </div>
          <div class="kv"><div>项目</div><div>${escHtml(data.project_id ?? '-')}</div></div>
          <div class="kv"><div>新建补充单</div><div>${escHtml(data.created_count ?? 0)}</div></div>
          <div class="kv"><div>更新补充单</div><div>${escHtml(data.updated_count ?? 0)}</div></div>
          <div class="kv"><div>补充单总数</div><div>${escHtml(items.length)}</div></div>
          <div class="kv"><div>待补素材</div><div>${escHtml(backendReadyCount)}</div></div>
          <div class="kv"><div>待系统后台确认</div><div>${escHtml(reviewCount)}</div></div>
          <div class="kv"><div>说明</div><div>这一步只负责把缺口场景音效写入补充单，不直接上传素材。</div></div>
          <div class="hint" style="margin-top:10px;">这一步生成的缺口会进入 scene 后台继续处理。运营应先核对场景图谱，再决定是直接补素材，还是先调整场景推荐。</div>
          <div class="hint" style="margin-top:8px;">后续处理入口：<a href="/scene.html" style="color:#7de8ff;">打开 scene 后台</a>，进入“场景补充单运营”和“场景图谱维护”。</div>
        `));
        if (items.length) {
          const rows = items.map((item, idx) => {
            const quality = getSceneSupplementQuality(item);
            return `
              <tr>
                <td>${idx + 1}</td>
                <td>${escHtml(item.scene_name || item.node_key || '')}</td>
                <td>${escHtml(item.node_key || '')}</td>
                <td>${escHtml(getSceneSupplementStatusLabel(item.status))}</td>
                <td>${escHtml(item.missing_count ?? 0)}</td>
                <td><span class="scene-badge ${escAttr(quality.cls)}">${escHtml(quality.label)}</span></td>
              </tr>
            `;
          }).join('');
          blocks.push(panel('场景补充单明细', `<table class="table-lite"><thead><tr><th>#</th><th>场景</th><th>节点</th><th>状态</th><th>缺口词数</th><th>结果层级</th></tr></thead><tbody>${rows}</tbody></table>`));
        }
        blocks.push(panel('三段关系说明', `
          <div class="md">
            <p><strong>第 4 步：场景搭建分析</strong> 负责识别文本中的主场景、时间词、地点词、背景元素、特征元素和细节元素。</p>
            <p><strong>第 4.5 步：场景音效推荐</strong> 负责基于场景结果命中图谱、召回支撑场景音效和补充层次音效，并尝试匹配可下载素材。</p>
            <p><strong>第 4.6 步：场景补充单</strong> 负责把仍然缺素材的场景音效写入补充单，交给 scene 后台继续补库。</p>
            <p><strong>用户端到后台的闭环</strong>：用户端只负责识别、推荐和写入补充单；真正的补素材、改图谱、调模板与集合，都在 <a href="/scene.html">scene 后台</a> 继续完成。</p>
          </div>
        `));
        return blocks;
      }

      function parseServerDate(value) {
        const raw = String(value || '').trim();
        if (!raw) return null;
        let normalized = raw.replace(/\.\d+$/, '');
        if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(normalized)) normalized = normalized.replace(' ', 'T');
        const hasTimezone = /(?:Z|[+\-]\d{2}:\d{2})$/.test(normalized);
        const candidate = hasTimezone ? normalized : `${normalized}Z`;
        const d = new Date(candidate);
        if (Number.isNaN(d.getTime())) return null;
        return d;
      }

      function formatReadableShortTime(value) {
        const d = parseServerDate(value);
        if (!d) {
          const raw = String(value || '').trim();
          return raw ? raw.replace('T', ' ').replace(/\.\d+$/, '') : '-';
        }
        const parts = new Intl.DateTimeFormat('zh-CN', {
          timeZone: 'Asia/Shanghai',
          hour12: false,
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
        }).formatToParts(d);
        const map = Object.fromEntries(parts.map(p => [p.type, p.value]));
        const yyyy = map.year || '0000';
        const mm = map.month || '00';
        const dd = map.day || '00';
        const hh = map.hour || '00';
        const mi = map.minute || '00';
        return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
      }

      function renderProjectCreatedPanels(data, panel) {
        const blocks = [];
        const title = String(data.title || '').trim() || '未命名项目';
        const genre = String(data.genre || '').trim() || '-';
        const createdAt = formatReadableShortTime(data.created_at || '');
        const nextSteps = [
          '先上传音乐，执行音乐分析',
          '再输入文本，继续动作提取或场景搭建分析',
          '如果已有演绎音频，可继续文本演绎分析和文本音乐执行单',
        ];
        blocks.push(`
          <section class="music-hero">
            <h4>项目已创建</h4>
            <div style="font-size:14px; line-height:1.55;">当前项目已可继续进入声音制作流程。你可以直接开始音乐分析、动作提取、场景搭建或文本演绎分析。</div>
            <div class="music-grid" style="margin-top:10px;">
              <div class="music-stat"><div class="k">项目名称</div><div class="v" style="font-size:16px;">${escHtml(title)}</div></div>
              <div class="music-stat"><div class="k">赛道</div><div class="v" style="font-size:16px;">${escHtml(genre)}</div></div>
              <div class="music-stat"><div class="k">项目编号</div><div class="v" style="font-size:16px;">#${escHtml(data.id ?? '-')}</div></div>
              <div class="music-stat"><div class="k">创建时间</div><div class="v" style="font-size:15px;">${escHtml(createdAt)}</div></div>
            </div>
          </section>
        `);
        blocks.push(panel('建议下一步', `<ul>${nextSteps.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul>`));
        return blocks;
      }

      function renderPretty(data) {
        const box = document.getElementById('prettyOutput');
        if (typeof data === 'string') {
          box.innerHTML = `<div>${escHtml(data)}</div>`;
          return;
        }
        if (!data || typeof data !== 'object') {
          box.innerHTML = '<div>暂无可展示内容</div>';
          return;
        }
        if (data.detail) {
          const miss = Array.isArray(data.missing) && data.missing.length ? `（缺少：${data.missing.join('、')}）` : '';
          const hint = data.hint ? `<div style="margin-top:6px;">${escHtml(data.hint)}</div>` : '';
          box.innerHTML = `<div class="error">${escHtml(data.detail)}${escHtml(miss)}${hint}</div>`;
          return;
        }
        const blocks = [];
        const panel = (title, inner) => `<section class="panel"><h4>${escHtml(title)}</h4>${inner}</section>`;
        const report = data.report_json && typeof data.report_json === 'object' ? data.report_json : null;
        const actionVerbSelection = getBestActionVerbReport(data);
        const actionVerbReport = actionVerbSelection ? actionVerbSelection.report : null;
        const userView = (report && Object.keys(report).length) ? report : null;
        const isFusionResult = Boolean(
          data.evidence_summary
          || Array.isArray(data.cues)
          || (data.fusion && typeof data.fusion === 'object')
        );
        const isTextNarrationResult = Boolean(data.text && data.narration);
        const isActionVerbResult = Boolean(actionVerbReport && typeof actionVerbReport === 'object');
        const isActionGraphResult = Boolean(
          Array.isArray(data.graph_items)
          || data.title === '动作图谱推荐'
          || (data.graph_model && data.graph_model.parent === '赛道+动作词')
        );
        const isActionGraphDraftResult = Boolean(Array.isArray(data.draft_items));
        const isSceneSfxResult = Boolean(Array.isArray(data.scene_items) && data.graph_model && data.asset_gap_summary);
        const isSceneBuildingResult = Boolean(Array.isArray(data.scene_items) && !isSceneSfxResult);
        const isSceneSupplementResult = Boolean(Array.isArray(data.items) && typeof data.created_count !== 'undefined' && typeof data.updated_count !== 'undefined');
        const isLoginResult = Boolean(
          typeof data.token === 'string'
          && data.user
          && typeof data.user === 'object'
          && data.user.phone
        );
        const isRequestCodeResult = Boolean(
          !isLoginResult
          && typeof data.code === 'string'
          && (data.phone || document.getElementById('loginPhone')?.value)
        );
        const fallbackMusicReport = {
          title: '音乐分析结果',
          summary: typeof data.report_markdown === 'string' ? data.report_markdown.split('\n').slice(0, 3).join(' ').trim() : '',
          fit_genres: [],
          risk_genres: [],
          sections: [],
          hit_points: Array.isArray(data.markers)
            ? data.markers.map(m => ({
                time_sec: m.time_sec,
                type: m.type,
                usage: m.label || '',
              }))
            : [],
          mix_notes: [],
          key_points: [],
          structure_logic: null,
        };
        const isMusicResult = Boolean(
          typeof data.duration_sec === 'number'
          && Array.isArray(data.markers)
        );
        const isMusicMatchResult = Boolean(
          typeof data.verdict === 'string'
          && typeof data.score !== 'undefined'
          && data.genre_match
          && data.text_match
        );
        const isProjectCreatedResult = Boolean(
          typeof data.id !== 'undefined'
          && data.title
          && data.genre
          && data.created_at
          && !report
          && !isMusicResult
          && !isActionVerbResult
          && !isActionGraphResult
          && !isSceneBuildingResult
          && !isSceneSfxResult
          && !isSceneSupplementResult
        );

        if (isProjectCreatedResult) {
          blocks.push(...renderProjectCreatedPanels(data, panel));
        }
        if (isLoginResult) {
          blocks.push(...renderLoginPanels(data, panel));
        }
        if (isRequestCodeResult) {
          blocks.push(...renderRequestCodePanels(data, panel));
        }
        if (isMusicResult) {
          blocks.push(...renderMusicAnalysisPanels(data, userView || fallbackMusicReport, panel));
        }
        if (isMusicMatchResult) {
          blocks.push(...renderMusicMatchPanels(data, panel));
        }
        if (isActionVerbResult && !isFusionResult && !isTextNarrationResult && !isSceneBuildingResult) {
          blocks.push(...renderActionVerbPanels(actionVerbReport, panel, actionVerbSelection));
        }
        if (isActionGraphResult) {
          blocks.push(...renderActionGraphPanels(data, panel));
        }
        if (isActionGraphDraftResult) {
          blocks.push(...renderActionGraphDraftPanels(data, panel));
        }
        if (isSceneBuildingResult) {
          blocks.push(...renderSceneBuildingPanels(data, panel));
        }
        if (isSceneSfxResult) {
          blocks.push(...renderSceneSfxPanels(data, panel));
        }
        if (isSceneSupplementResult) {
          blocks.push(...renderSceneSupplementPanels(data, panel));
        }
        if (data.text && data.narration) {
          const sceneCount = Array.isArray(data.text.scenes) ? data.text.scenes.length : 0;
          const clauseCount = Array.isArray(data.narration.clause_timeline) ? data.narration.clause_timeline.length : 0;
          const narrSec = Number(data.narration.duration_sec || 0);
          blocks.push(panel('文本+演绎联合分析', `
            <div class="kv"><div>文本数</div><div>${sceneCount}</div></div>
            <div class="kv"><div>语句时间线</div><div>${clauseCount} 条</div></div>
            <div class="kv"><div>演绎音频时长</div><div>${narrSec ? narrSec.toFixed(2) : '-'} 秒</div></div>
          `));
        }
        if (data.evidence_summary && typeof data.evidence_summary === 'object') {
          const es = data.evidence_summary;
          const m = es.music || {};
          const t = es.text || {};
          const n = es.narration || {};
          blocks.push(panel('执行单证据来源', `
            <div class="kv"><div>证据链</div><div>${escHtml(describeFusionEvidenceChain(es.chain || ''))}</div></div>
            <div class="kv"><div>音乐证据</div><div>${m.has_data ? '已加载' : '缺失'}（锚点${m.marker_count ?? 0}个，标签${m.tag_count ?? 0}个）</div></div>
            <div class="kv"><div>文本证据</div><div>${t.has_data ? '已加载' : '缺失'}（场景${t.scene_count ?? 0}条，文本${t.text_len ?? 0}字）</div></div>
            <div class="kv"><div>演绎证据</div><div>${n.has_data ? '已加载' : '缺失'}（场景时间线${n.scene_timeline_count ?? 0}条，语句时间线${n.clause_timeline_count ?? 0}条）</div></div>
          `));
        }
        if (isFrontendDebugEnabled(data) && data.prompt_guard && typeof data.prompt_guard === 'object') {
          const ok = !!data.prompt_guard.passed;
          blocks.push(`
            <div class="${ok ? 'ok' : 'error'}">
              Prompt校验：${ok ? '通过' : '未通过'}
              （expected=${escHtml(data.prompt_guard.expected || '')} / actual=${escHtml(data.prompt_guard.actual || '空')}）
            </div>
          `);
        }
        if (!isMusicResult && !isActionVerbResult && userView && userView.title) {
          blocks.push(panel('文本分析标题', `<div class="kv"><div>标题</div><div>${escHtml(userView.title)}</div></div>`));
        }
        if (!isMusicResult && !isActionVerbResult && userView && userView.text_theme) {
          blocks.push(panel('文本主情绪与叙事方向', `<div>${escHtml(userView.text_theme)}</div>`));
        }
        const fitTextFromReport = !isMusicResult && !isActionVerbResult && userView && typeof userView.fit_with_music === 'object' ? userView.fit_with_music : null;
        if (fitTextFromReport) {
          const score = Number(fitTextFromReport.score || 0);
          const verdict = fitTextFromReport.verdict || '未判定';
          const reasons = Array.isArray(fitTextFromReport.reasons) ? fitTextFromReport.reasons.map(x => `<li>${escHtml(x)}</li>`).join('') : '';
          blocks.push(panel('文本与音乐适配结论', `
            <div class="kv"><div>结论</div><div><strong>${escHtml(verdict)}</strong></div></div>
            <div class="kv"><div>适配评分</div><div>${score}/100</div></div>
            ${reasons ? `<ul>${reasons}</ul>` : ''}
          `));
        }
        const fitFromReport = userView ? userView.fit_verdict : null;
        if (fitFromReport && typeof fitFromReport === 'object') {
          const score = Number(fitFromReport.score || 0);
          const verdict = fitFromReport.verdict || '未判定';
          const reasons = Array.isArray(fitFromReport.reasons) ? fitFromReport.reasons.map(x => `<li>${escHtml(x)}</li>`).join('') : '';
          blocks.push(panel('音乐与文本适配结论', `
            <div class="kv"><div>结论</div><div><strong>${escHtml(verdict)}</strong></div></div>
            <div class="kv"><div>适配评分</div><div>${score}/100</div></div>
            ${reasons ? `<ul>${reasons}</ul>` : ''}
          `));
        }
        if (data.fit && typeof data.fit === 'object') {
          const score = Number(data.fit.fit_score || 0);
          const verdict = data.fit.verdict || '未判定';
          const reasons = Array.isArray(data.fit.reasons) ? data.fit.reasons.map(x => `<li>${escHtml(x)}</li>`).join('') : '';
          blocks.push(panel('音乐与文本适配结论', `
            <div class="kv"><div>结论</div><div><strong>${escHtml(verdict)}</strong></div></div>
            <div class="kv"><div>适配评分</div><div>${score}/100</div></div>
            ${reasons ? `<ul>${reasons}</ul>` : ''}
          `));
        }

        const sceneUnits = !isMusicResult && !isActionVerbResult && userView && Array.isArray(userView.scene_units) ? userView.scene_units : [];
        if (sceneUnits.length) {
          const rows = sceneUnits.map(s => `<tr>
            <td>${s.scene_no ?? '-'}</td>
            <td>${s.text_start_char ?? '-'} ~ ${s.text_end_char ?? '-'}</td>
            <td>${escHtml(s.text_excerpt || '')}</td>
            <td>${escHtml(s.emotion || '')}</td>
            <td>${escHtml(s.emotion_change || '')}</td>
            <td>${escHtml((s.action_tags || []).join('、') || '无')}</td>
            <td>${s.intensity ?? '-'}</td>
            <td>${escHtml(s.music_need || '')}</td>
            <td>${escHtml(s.entry_hint || '')}</td>
            <td>${escHtml(s.exit_hint || '')}</td>
            <td>${escHtml((s.sfx_terms || []).join('、') || '无')}</td>
          </tr>`).join('');
          blocks.push(panel('场景分析（面向创作）', `<table class="table-lite"><thead><tr><th>场景</th><th>文字区间</th><th>原文片段</th><th>主情绪</th><th>情绪变化</th><th>动作标签</th><th>强度</th><th>音乐需求</th><th>进入建议</th><th>退出建议</th><th>音效词</th></tr></thead><tbody>${rows}</tbody></table>`));
        }

        const keyPoints = (!isMusicResult && !isActionVerbResult && userView && Array.isArray(userView.key_points)) ? userView.key_points : [];
        if (keyPoints.length) {
          blocks.push(panel('关键结论', `<ul>${keyPoints.map(x => `<li>${escHtml(x)}</li>`).join('')}</ul>`));
        }
        if (!isMusicResult && !isActionVerbResult && userView && userView.structure_logic && typeof userView.structure_logic === 'object') {
          const sl = userView.structure_logic;
          blocks.push(panel('乐段结构规律', `
            <div class="kv"><div>结构猜测</div><div>${escHtml(sl.pattern_guess || '未识别')}</div></div>
            <div class="kv"><div>重复组</div><div>${escHtml((sl.repeat_groups || []).join('；') || '无')}</div></div>
            <div class="kv"><div>证据</div><div>${escHtml((sl.evidence || []).join('；') || '无')}</div></div>
          `));
        }
        if (!isMusicResult && !isActionVerbResult && userView && Array.isArray(userView.sections) && userView.sections.length) {
          const rows = userView.sections.map(s => `<tr>
            <td>${s.section_no ?? '-'}</td>
            <td>${escHtml(s.section_type || '')}</td>
            <td>${escHtml(s.label || '')}</td>
            <td>${s.start_sec ?? '-'} ~ ${s.end_sec ?? '-'}</td>
            <td>${escHtml((s.instruments || []).join('、') || '无')}</td>
            <td>${escHtml((s.new_instruments_vs_prev || []).join('、') || '无')}</td>
            <td>${s.energy_level ?? '-'}</td>
            <td>${escHtml(s.layer_progression || '')}</td>
          </tr>`).join('');
          blocks.push(panel('乐段与配器递进', `<table class="table-lite"><thead><tr><th>序号</th><th>类型</th><th>标签</th><th>时间(秒)</th><th>配器</th><th>新增配器</th><th>能量级</th><th>递进说明</th></tr></thead><tbody>${rows}</tbody></table>`));
        }

        if (!isMusicResult && !isActionVerbResult && typeof data.duration_sec === 'number') {
          blocks.push(panel('音频基本信息', `
            <div class="kv"><div>时长</div><div>${data.duration_sec.toFixed(2)} 秒</div></div>
            <div class="kv"><div>估计节奏</div><div>${Number(data.bpm || 0).toFixed(1)} BPM</div></div>
            <div class="kv"><div>标签</div><div>${(data.tags || []).map(escHtml).join(' / ')}</div></div>
          `));
        }
        if (Array.isArray(data.scenes) && data.scenes.length && !(data.text && data.narration)) {
          const rows = data.scenes.map(s => `<tr><td>${s.scene_no}</td><td>${escHtml(s.text || '')}</td><td>${escHtml((s.actions || []).join('、') || '无')}</td><td>${escHtml((s.sfx || []).join('、') || '无')}</td><td>${s.intensity ?? ''}</td></tr>`).join('');
          blocks.push(panel('文本场景分析', `<table class="table-lite"><thead><tr><th>场景</th><th>文本</th><th>动作</th><th>建议音效</th><th>强度</th></tr></thead><tbody>${rows}</tbody></table>`));
        }
        if (Array.isArray(data.sfx_requirements) && data.sfx_requirements.length && !(data.text && data.narration)) {
          const rows = data.sfx_requirements.map(r => {
            const cands = (r.candidates || []).map(c => `${c.file_name}(${c.score})`).join('、') || '暂无';
            return `<tr>
              <td>${escHtml(r.term || '')}</td>
              <td>${escHtml((r.scene_nos || []).join('、'))}</td>
              <td>${escHtml(cands)}</td>
              <td>${r.download_ready ? '可下载' : '待补素材'}</td>
            </tr>`;
          }).join('');
          blocks.push(panel('音效需求清单（可直接找素材）', `<table class="table-lite"><thead><tr><th>需求词</th><th>涉及场景</th><th>候选文件</th><th>状态</th></tr></thead><tbody>${rows}</tbody></table>`));
        }
        const clauseTimeline = (Array.isArray(data.clause_timeline) && data.clause_timeline.length)
          ? data.clause_timeline
          : (userView && Array.isArray(userView.clause_timeline) ? userView.clause_timeline : []);
        if (Array.isArray(clauseTimeline) && clauseTimeline.length) {
          const rows = clauseTimeline.map(c => `<tr>
            <td>${c.clause_no ?? '-'}</td>
            <td>${c.text_start_char ?? '-'} ~ ${c.text_end_char ?? '-'}</td>
            <td>${escHtml(c.text || '')}</td>
            <td>${escHtml(c.punct || '无')}</td>
            <td>${c.start_sec ?? '-'} ~ ${c.end_sec ?? '-'}</td>
          </tr>`).join('');
          blocks.push(panel('标点级语句时间线（核心时间证据）', `<table class="table-lite"><thead><tr><th>语句</th><th>字符区间</th><th>内容</th><th>标点</th><th>时间(秒)</th></tr></thead><tbody>${rows}</tbody></table>`));
        }
        if (Array.isArray(data.quick_download_list) && data.quick_download_list.length) {
          blocks.push(panel('快速下载清单', `<div>${data.quick_download_list.map(x => `<span class="pill">${escHtml(x)}</span>`).join('')}</div>`));
        }
        if (Array.isArray(data.cues) && data.cues.length) {
          const rows = data.cues.map(c => `<tr>
            <td>${c.scene_no}</td>
            <td>${c.text_start_char ?? '-'} ~ ${c.text_end_char ?? '-'}</td>
            <td>${escHtml(c.text_excerpt || '')}</td>
            <td>${c.narration_start_sec ?? '-'} ~ ${c.narration_end_sec ?? '-'}</td>
            <td>${c.music_segment_start_sec ?? c.target_time_sec} ~ ${c.music_segment_end_sec ?? c.target_time_sec}</td>
            <td>${escHtml(c.marker_label || '')}</td>
          </tr>`).join('');
          blocks.push(panel('文字-旁白-音乐对位执行单', `<table class="table-lite"><thead><tr><th>场景</th><th>文字区间</th><th>文字片段</th><th>旁白区间(秒)</th><th>音乐区间(秒)</th><th>锚点</th></tr></thead><tbody>${rows}</tbody></table>`));
        }
        if (isFusionResult) {
          blocks.push(panel('执行建议', `<div class="md">${buildFusionUsageGuidance(data)}</div>`));
        }
        if (userView && Array.isArray(userView.scene_alignment) && userView.scene_alignment.length) {
          const rows = userView.scene_alignment.map(r => `<tr>
            <td>${r.scene_no ?? '-'}</td>
            <td>${r.text_start_char ?? '-'} ~ ${r.text_end_char ?? '-'}</td>
            <td>${escHtml(r.text_excerpt || '')}</td>
            <td>${r.music_start_sec ?? '-'} ~ ${r.music_end_sec ?? '-'}</td>
            <td>${escHtml(r.entry_reason || '')}</td>
          </tr>`).join('');
          blocks.push(panel('文本分析阶段的对位建议', `<table class="table-lite"><thead><tr><th>场景</th><th>文字区间</th><th>文字片段</th><th>音乐区间(秒)</th><th>进入理由</th></tr></thead><tbody>${rows}</tbody></table>`));
        }
        if (userView && Array.isArray(userView.music_entry_plan) && userView.music_entry_plan.length) {
          const rows = userView.music_entry_plan.map(r => `<tr>
            <td>${r.scene_no ?? '-'}</td>
            <td>${r.text_start_char ?? '-'} ~ ${r.text_end_char ?? '-'}</td>
            <td>${escHtml(r.text_excerpt || '')}</td>
            <td>${r.music_start_sec ?? '-'} ~ ${r.music_end_sec ?? '-'}</td>
            <td>${escHtml(r.entry_reason || '')}</td>
          </tr>`).join('');
          blocks.push(panel('融合阶段对位计划（模型输出）', `<table class="table-lite"><thead><tr><th>场景</th><th>文字区间</th><th>文字片段</th><th>音乐区间(秒)</th><th>进入理由</th></tr></thead><tbody>${rows}</tbody></table>`));
        }
        if (data.report_markdown && isFrontendDebugEnabled(data)) {
          blocks.push(panel('内部调试说明', `<div class="md">${mdToHtml(data.report_markdown)}</div>`));
        }
        if (!blocks.length) {
          if (isFrontendDebugEnabled(data)) {
            blocks.push(`<div>${escHtml(JSON.stringify(data, null, 2))}</div>`);
          } else {
            blocks.push('<div class="hint">当前结果已生成，系统已按用户可读方式展示主要内容；如需查看开发结构，请在后台开启调试开关。</div>');
          }
        }
        box.innerHTML = blocks.join('');
        // 当前页面不向用户展示 Raw Response 用户观测区，保留能力供后续页面复用
        renderRawUserView(data);
        renderLlmDebug(data);
      }

      function setBusy(busy, label) {
        uiBusy = busy;
        const buttons = document.querySelectorAll('button');
        buttons.forEach(btn => { btn.disabled = busy; });
        const tip = document.getElementById('busyTip');
        if (busy) {
          tip.style.display = 'block';
          tip.textContent = label ? `处理中：${label}` : '处理中，请稍候...';
        } else {
          tip.style.display = 'none';
          tip.textContent = '处理中，请稍候...';
        }
      }

      async function readApiResponse(res) {
        const text = await res.text();
        let data = text;
        try {
          data = JSON.parse(text);
        } catch (_) {}
        if (!res.ok) {
          const detail = typeof data === 'object' && data ? (data.detail || JSON.stringify(data)) : String(data);
          throw new Error(`HTTP ${res.status}: ${detail}`);
        }
        return data;
      }

      async function runAction(label, fn, options = {}) {
        const requiresLogin = options.requiresLogin !== false;
        if (requiresLogin && !ensureLoggedInForFeature()) return;
        if (uiBusy) return show('已有任务在执行，请稍候完成');
        setBusy(true, label);
        show(`正在执行：${label}`);
        try {
          await fn();
        } catch (e) {
          show(`执行失败：${label}\n${e.message || e}`);
        } finally {
          setBusy(false);
        }
      }

      function apiBase() {
        const el = document.getElementById('apiBase');
        const raw = (el && el.value ? el.value : 'http://127.0.0.1:8010/api').trim();
        return raw.replace(/\/$/, '');
      }

      function updateLoginState() {
        const box = document.getElementById('loginState');
        if (!box) return;
        const uid = authUser && authUser.uid ? ` / UID:${authUser.uid}` : '';
        box.textContent = authPhone ? `${authPhone}${uid}（已登录）` : '未登录';
        const policy = document.getElementById('loginPolicyHint');
        if (policy) {
          policy.textContent = authSettings && authSettings.invite_only_enabled
            ? '测试期间仅邀请码用户可激活使用。邀请码激活后即刻失效；推荐码可填写 UID 或手机号。'
            : '邀请码测试已关闭，可正常注册登录；推荐码可填写 UID 或手机号。';
        }
        updateFeatureGateUI();
      }

      function updateFeatureGateUI() {
        const loggedIn = !!authToken;
        document.querySelectorAll('button[data-auth-required="1"]').forEach((btn) => {
          btn.disabled = !loggedIn;
          btn.title = loggedIn ? '' : '请先完成登录';
        });
      }

      function updateHomepageVisibility() {
        const shell = document.getElementById('homeLeaderboardShell');
        if (!shell) return;
        shell.style.display = authSettings && authSettings.home_leaderboards_enabled ? '' : 'none';
      }

      function authHeaders(extra = {}) {
        const h = { ...(extra || {}) };
        if (authToken) h.Authorization = `Bearer ${authToken}`;
        return h;
      }

      async function apiFetch(url, options = {}) {
        const opts = { ...(options || {}) };
        opts.headers = authHeaders(opts.headers || {});
        return window.fetch(url, opts);
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

      function describeFusionEvidenceChain(chain) {
        const raw = String(chain || '').trim().toLowerCase();
        if (!raw) return '未标注';
        if (raw === 'music+text+narration') return '音乐分析 + 文本分析 + 演绎时间轴';
        return chain;
      }

      function buildFusionUsageGuidance(data) {
        const cues = Array.isArray(data.cues) ? data.cues : [];
        const evidence = data.evidence_summary && typeof data.evidence_summary === 'object' ? data.evidence_summary : {};
        const music = evidence.music || {};
        const text = evidence.text || {};
        const narration = evidence.narration || {};
        const cueCount = cues.length;
        const markerCount = Number(music.marker_count || 0);
        const sceneCount = Number(text.scene_count || 0);
        const clauseCount = Number(narration.clause_timeline_count || 0);
        const lines = [
          `本执行单仅基于文本分析、音乐分析和演绎时间轴生成，不包含动作模块和场景模块结果。`,
          cueCount
            ? `当前已生成 ${cueCount} 条文本与音乐对位建议，可先按表格中的文字区间、旁白区间和音乐区间完成首轮粗对位。`
            : '当前未生成可执行的对位条目，建议先检查音乐分析与文本演绎分析结果是否完整。',
          markerCount
            ? `可优先以音乐锚点为主做进入点和转折点定位，再根据实际朗读节奏微调衔接。`
            : '当前音乐锚点较少，建议先以文本节奏和旁白时间轴为主做基础对位。',
          sceneCount && clauseCount
            ? `如某一段文字情绪变化明显，但音乐进入点不顺，可优先回看对应语句时间线，再微调音乐起落。`
            : `如对位不顺，建议先补齐文本分析和演绎时间轴，再重新生成执行单。`
        ];
        return `<ul>${lines.map((line) => `<li>${escHtml(line)}</li>`).join('')}</ul>`;
      }

      function normalizePhoneInput(raw) {
        let phone = String(raw || '').replace(/\D/g, '');
        if (phone.startsWith('86') && phone.length === 13) phone = phone.slice(2);
        return phone;
      }

      function isValidPhoneInput(raw) {
        return /^1\d{10}$/.test(normalizePhoneInput(raw));
      }

      async function requestCode() {
        const input = (document.getElementById('loginPhone').value || '').trim();
        const phone = normalizePhoneInput(input);
        document.getElementById('loginPhone').value = phone;
        if (!phone) return show('请先输入手机号');
        if (!isValidPhoneInput(phone)) return show('请输入有效的11位手机号');
        await runAction('发送验证码', async () => {
          const res = await apiFetch(`${apiBase()}/auth/request-code`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ phone }),
          });
          const data = await readApiResponse(res);
          if (data && data.code) {
            document.getElementById('loginCode').value = data.code;
          }
          show(data);
        }, { requiresLogin: false });
      }

      async function loginByCode() {
        const phone = normalizePhoneInput((document.getElementById('loginPhone').value || '').trim());
        const code = (document.getElementById('loginCode').value || '').trim();
        const invite_code = (document.getElementById('inviteCode').value || '').trim();
        const uid = (document.getElementById('userUid').value || '').trim();
        const referral_code = (document.getElementById('referralCode').value || '').trim();
        document.getElementById('loginPhone').value = phone;
        if (!phone || !code) return show('请输入手机号和验证码');
        if (!isValidPhoneInput(phone)) return show('请输入有效的11位手机号');
        await runAction('手机号登录', async () => {
          const res = await apiFetch(`${apiBase()}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ phone, code, invite_code, uid, referral_code }),
          });
          const data = await readApiResponse(res);
          authToken = data.token || '';
          authPhone = data.user?.phone || phone;
          authUser = data.user || null;
          localStorage.setItem('mfa_auth_token', authToken);
          localStorage.setItem('mfa_auth_phone', authPhone);
          updateLoginState();
          show(data);
        }, { requiresLogin: false });
      }

      async function syncAuthBootstrap() {
        try {
          const res = await window.fetch(`${apiBase()}/auth/settings`);
          if (res.ok) authSettings = await res.json();
        } catch (_) {}
        if (!authToken) {
          updateLoginState();
          updateHomepageVisibility();
          loadHomeLeaderboards();
          return;
        }
        try {
          const res = await apiFetch(`${apiBase()}/auth/me`);
          if (!res.ok) throw new Error('auth/me failed');
          authUser = await res.json();
          authPhone = authUser.phone || authPhone;
          localStorage.setItem('mfa_auth_phone', authPhone);
        } catch (_) {
          authToken = '';
          authPhone = '';
          authUser = null;
          localStorage.removeItem('mfa_auth_token');
          localStorage.removeItem('mfa_auth_phone');
        }
        updateLoginState();
        renderUsagePanel(authUser || {});
        updateHomepageVisibility();
        loadHomeLeaderboards();
      }

      function renderCreatorShowcases(items) {
        const box = document.getElementById('creatorShowcaseView');
        if (!box) return;
        const list = Array.isArray(items) ? items : [];
        if (!list.length) {
          box.innerHTML = '<div class="hint">当前还没有已发布的创作者展示。</div>';
          return;
        }
        box.innerHTML = list.map((item) => `
          <div class="panel">
            <div style="font-weight:700;">${escHtml(item.title || '-')}</div>
            <div class="hint" style="margin-top:4px;">${escHtml(item.genre || '')} ｜ ${escHtml(item.role_label || '创作者')} ｜ ${escHtml(item.user_phone || '')}</div>
            <div style="margin-top:6px; line-height:1.7;">${escHtml(item.summary || '')}</div>
            <div style="margin-top:6px;">${(Array.isArray(item.skills) ? item.skills : []).map((skill) => `<span class="pill">${escHtml(skill)}</span>`).join('')}</div>
            ${(item.sample_link || item.sample_file_name) ? `<div class="hint" style="margin-top:6px;">样片：${escHtml(item.sample_link || item.sample_file_name)}</div>` : ''}
          </div>
        `).join('');
      }

      function renderCopyrightAds(items) {
        const box = document.getElementById('copyrightAdsView');
        if (!box) return;
        const list = Array.isArray(items) ? items : [];
        if (!list.length) {
          box.innerHTML = '<div class="hint">当前还没有已发布的版权书广告。</div>';
          return;
        }
        box.innerHTML = list.map((item) => `
          <div class="panel">
            <div style="font-weight:700;">${escHtml(item.title || '-')}</div>
            <div class="hint" style="margin-top:4px;">${escHtml(item.genre || '')} ｜ 参考预算：${escHtml(item.budget_text || '待沟通')} ｜ 发布保证金：${escHtml(item.deposit_amount || 0)} 元</div>
            <div style="margin-top:6px; line-height:1.7;">${escHtml(item.description || '')}</div>
            ${item.contact_note ? `<div class="hint" style="margin-top:6px;">联系说明：${escHtml(item.contact_note)}</div>` : ''}
          </div>
        `).join('');
      }

      function renderRecruitmentNeeds(items) {
        const box = document.getElementById('recruitmentNeedsView');
        if (!box) return;
        const list = Array.isArray(items) ? items : [];
        if (!list.length) {
          box.innerHTML = '<div class="hint">当前还没有已发布的招聘需求。</div>';
          return;
        }
        box.innerHTML = list.map((item) => `
          <div class="panel">
            <div style="font-weight:700;">${escHtml(item.title || '-')}</div>
            <div class="hint" style="margin-top:4px;">${escHtml(item.genre || '')} ｜ 预算：${escHtml(item.budget_text || '待沟通')} ｜ 截止：${escHtml(item.deadline_text || '长期有效')}</div>
            <div style="margin-top:6px; line-height:1.7;">${escHtml(item.description || '')}</div>
            ${item.contact_note ? `<div class="hint" style="margin-top:6px;">报名说明：${escHtml(item.contact_note)}</div>` : ''}
          </div>
        `).join('');
      }

      function renderRechargeSummary(data) {
        const box = document.getElementById('rechargeSummaryView');
        if (!box) return;
        if (!authToken) {
          box.innerHTML = '<div class="hint">请先登录后查看可用余额和最近充值申请。</div>';
          return;
        }
        const balances = data && data.balances ? data.balances : {};
        const orders = Array.isArray(data && data.orders) ? data.orders : [];
        box.innerHTML = `
          <div class="panel">
            <div class="kv"><div>文字包余额</div><div>${escHtml(balances.text_char_pack_balance || 0)} 字</div></div>
            <div class="kv"><div>音效下载包</div><div>${escHtml(balances.sfx_download_pack_balance || 0)} 次</div></div>
            <div class="kv"><div>保证金余额</div><div>${escHtml(Number(balances.deposit_balance || 0).toFixed(2))} 元</div></div>
          </div>
          <div class="hint" style="margin:6px 0 4px;">最近充值申请</div>
          ${orders.length ? `
            <table class="table-lite">
              <thead><tr><th>时间</th><th>类型</th><th>套餐</th><th>数量</th><th>状态</th></tr></thead>
              <tbody>
                ${orders.map((item) => `<tr><td>${escHtml(formatReadableTime(item.created_at || '-'))}</td><td>${escHtml(item.order_type_label || item.order_type || '-')}</td><td>${escHtml(item.package_name || '-')}</td><td>${escHtml(item.units || 0)}</td><td>${escHtml(item.status_label || item.status || '-')}</td></tr>`).join('')}
              </tbody>
            </table>
          ` : '<div class="hint">当前没有充值申请记录。</div>'}
        `;
      }

      async function loadBusinessHomeModules() {
        try {
          const [showcasesRes, copyrightRes, recruitmentRes] = await Promise.all([
            window.fetch(`${apiBase()}/home/creator-showcases`),
            window.fetch(`${apiBase()}/home/copyright-ads`),
            window.fetch(`${apiBase()}/home/recruitment-needs`),
          ]);
          renderCreatorShowcases((await readApiResponse(showcasesRes)).items || []);
          renderCopyrightAds((await readApiResponse(copyrightRes)).items || []);
          renderRecruitmentNeeds((await readApiResponse(recruitmentRes)).items || []);
        } catch (_) {
          renderCreatorShowcases([]);
          renderCopyrightAds([]);
          renderRecruitmentNeeds([]);
        }
        if (authToken) {
          try {
            const res = await apiFetch(`${apiBase()}/home/recharge-summary`);
            renderRechargeSummary(await readApiResponse(res));
          } catch (_) {
            renderRechargeSummary(null);
          }
        } else {
          renderRechargeSummary(null);
        }
      }

      async function submitCreatorShowcase() {
        if (!ensureLoggedInForFeature()) return;
        const fd = new FormData();
        fd.set('title', (document.getElementById('showcaseTitle').value || '').trim());
        fd.set('genre', (document.getElementById('showcaseGenre').value || '').trim());
        fd.set('role_label', (document.getElementById('showcaseRole').value || '').trim());
        fd.set('summary', (document.getElementById('showcaseSummary').value || '').trim());
        fd.set('skills', (document.getElementById('showcaseSkills').value || '').trim());
        fd.set('sample_link', (document.getElementById('showcaseLink').value || '').trim());
        const file = document.getElementById('showcaseFile').files[0];
        if (file) fd.set('sample_file', file);
        await runAction('提交成品展示', async () => {
          const res = await apiFetch(`${apiBase()}/home/creator-showcases`, { method: 'POST', body: fd });
          const data = await readApiResponse(res);
          show(data);
          await loadBusinessHomeModules();
        });
      }

      async function submitRechargeOrder() {
        if (!ensureLoggedInForFeature()) return;
        const payload = {
          order_type: (document.getElementById('rechargeOrderType').value || '').trim(),
          package_name: (document.getElementById('rechargePackageName').value || '').trim(),
          units: Number(document.getElementById('rechargeUnits').value || 0),
          payable_amount: Number(document.getElementById('rechargeAmount').value || 0),
          deposit_offset: Number(document.getElementById('rechargeDepositOffset').value || 0),
          note: (document.getElementById('rechargeNote').value || '').trim(),
        };
        await runAction('提交充值申请', async () => {
          const res = await apiFetch(`${apiBase()}/home/recharge-orders`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          const data = await readApiResponse(res);
          show(data);
          await loadBusinessHomeModules();
        });
      }

      function renderDraftReview(data) {
        const tableBox = document.getElementById('draftReviewTable');
        if (!tableBox) return;
        const items = (data && data.draft_items) || [];
        if (!items.length) {
          tableBox.innerHTML = '<div class="hint">暂无候选词。</div>';
          return;
        }
        const rows = items.map((it, idx) => {
          const conf = Number(it.confidence || 0);
          const checked = conf >= 0.45 ? 'checked' : '';
          const lowMatch = (it.sources && it.sources.low_match) || 0;
          const missing = (it.sources && it.sources.missing_sfx) || 0;
          return `
            <tr>
              <td><input type="checkbox" class="draft-item-check" data-idx="${idx}" ${checked} /></td>
              <td>${escHtml(it.candidate)}</td>
              <td>${escHtml(it.target_head)}</td>
              <td>${conf.toFixed(3)}</td>
              <td>low_match=${lowMatch}, missing_sfx=${missing}</td>
              <td>${escHtml(it.review_status || 'pending')}</td>
            </tr>
          `;
        }).join('');
        tableBox.innerHTML = `
          <table class="review-table">
            <thead>
              <tr>
                <th>选择</th><th>候选词</th><th>建议归并头词</th><th>置信度</th><th>来源</th><th>审核状态</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        `;
      }

      function selectAllDraftItems(checked) {
        const checks = document.querySelectorAll('.draft-item-check');
        checks.forEach(ch => { ch.checked = checked; });
      }

      async function createProject() {
        await runAction('创建项目', async () => {
          const title = (document.getElementById('projectTitle').value || '').trim();
          const genre = document.getElementById('projectGenre').value;
          if (!title) return show('请先填写项目标题');
          const res = await apiFetch(`${apiBase()}/projects`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, genre }),
          });
          const data = await readApiResponse(res);
          projectId = data.id;
          resetSceneFlowState();
          document.getElementById('projectId').textContent = projectId;
          document.getElementById('projectGenreState').textContent = data.genre || genre || '-';
          show(data);
        });
      }

      async function uploadAudio() {
        if (!projectId) return show('请先创建项目');
        const f = document.getElementById('audioFile').files[0];
        if (!f) return show('请先选择音频文件');
        await runAction('音乐分析', async () => {
          const form = new FormData();
          form.append('file', f);
          const llmProviderOverride = currentLlmProviderOverride();
          if (llmProviderOverride) form.append('llm_provider_override', llmProviderOverride);
          const res = await apiFetch(`${apiBase()}/analysis/${projectId}/audio`, { method: 'POST', body: form });
          show(await readApiResponse(res));
        });
      }

      async function analyzeMusicMatch() {
        if (!projectId) return show('请先创建项目');
        await runAction('判断是否适合作品', async () => {
          const res = await apiFetch(`${apiBase()}/analysis/${projectId}/music-match`, { method: 'POST' });
          show(await readApiResponse(res));
        });
      }

      async function analyzeTextNarration() {
        if (!projectId) return show('请先创建项目');
        const text = (document.getElementById('textInput').value || '').trim();
        const f = document.getElementById('narrationFile').files[0];
        if (!text) return show('请先输入文本');
        if (!f) return show('请先选择演绎音频');
        await runAction('文本演绎分析（含时间轴）', async () => {
          const form = new FormData();
          form.append('text', text);
          form.append('file', f);
          const llmProviderOverride = currentLlmProviderOverride();
          if (llmProviderOverride) form.append('llm_provider_override', llmProviderOverride);
          const res = await apiFetch(`${apiBase()}/analysis/${projectId}/text-narration`, { method: 'POST', body: form });
          show(await readApiResponse(res));
        });
      }

      async function analyzeActionVerbs() {
        if (!projectId) return show('请先创建项目');
        const text = (document.getElementById('textInput').value || '').trim();
        const genre = (document.getElementById('projectGenre').value || '').trim();
        const promptFile = ACTION_PROMPT_BY_GENRE[genre] || 'V3-action_verbs_xuanhuan_task.txt';
        const llmProviderOverride = currentLlmProviderOverride();
        if (!text) return show('请先输入文本');
        await runAction('人物动作提取', async () => {
          const res = await apiFetch(`${apiBase()}/analysis/${projectId}/action-verbs`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, genre, prompt_file: promptFile, llm_provider_override: llmProviderOverride }),
          });
          const data = await readApiResponse(res);
          latestActionVerbData = data;
          show(data);
        });
      }

      async function buildActionSfxGraph() {
        if (!projectId) return show('请先创建项目');
        if (!latestActionVerbData || !latestActionVerbData.report_json) {
          return show('请先执行第3步：人物动作提取');
        }
        await runAction('动作图谱推荐', async () => {
          try {
            const explainRes = await apiFetch(`${apiBase()}/action-graph/explanation`);
            if (explainRes.ok) latestActionGraphExplanation = await readApiResponse(explainRes);
          } catch (_) {}
          const res = await apiFetch(`${apiBase()}/analysis/${projectId}/action-sfx`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action_report: latestActionVerbData.report_json }),
          });
          const data = await readApiResponse(res);
          if (latestActionGraphExplanation && !data.business_explanation) {
            data.business_explanation = latestActionGraphExplanation;
          }
          latestActionSfxData = data;
          show(data);
        });
      }

      async function buildActionGraphDraft() {
        if (!projectId) return show('请先创建项目');
        if (!latestActionSfxData || !Array.isArray(latestActionSfxData.graph_items)) {
          return show('请先执行第3.5步：动作图谱推荐');
        }
        await runAction('动作补充单', async () => {
          const res = await apiFetch(`${apiBase()}/analysis/${projectId}/action-graph-draft`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action_sfx_result: latestActionSfxData }),
          });
          const data = await readApiResponse(res);
          latestActionGraphDraftData = data;
          show(data);
        });
      }

      async function buildSceneBuilding() {
        if (!projectId) return show('请先创建项目');
        const text = (document.getElementById('textInput').value || '').trim();
        const genre = (document.getElementById('projectGenre').value || '').trim();
        const promptFile = SCENE_PROMPT_BY_GENRE[genre] || 'V3-scene_building_task.txt';
        const llmProviderOverride = currentLlmProviderOverride();
        if (!text) return show('请先输入文本');
        clearSceneFlowFrom('building');
        showScenePendingState('building');
        await runAction('场景搭建分析', async () => {
          const res = await apiFetch(`${apiBase()}/analysis/${projectId}/scene-building`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, genre, prompt_file: promptFile, llm_provider_override: llmProviderOverride }),
          });
          const data = await readApiResponse(res);
          latestSceneBuildingData = data;
          latestSceneSfxData = null;
          latestSceneSupplementData = null;
          refreshSceneActionButtons();
          show(data);
        });
      }

      async function buildSceneSfxGraph() {
        if (!projectId) return show('请先创建项目');
        if (!latestSceneBuildingData || !Array.isArray(latestSceneBuildingData.scene_items)) {
          return show('请先执行第6步：场景搭建分析');
        }
        clearSceneFlowFrom('sfx');
        showScenePendingState('sfx');
        await runAction('场景音效推荐', async () => {
          const sceneReport = latestSceneBuildingData.report_json && typeof latestSceneBuildingData.report_json === 'object'
            ? latestSceneBuildingData.report_json
            : latestSceneBuildingData;
          const res = await apiFetch(`${apiBase()}/analysis/${projectId}/scene-sfx`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scene_report: sceneReport }),
          });
          const data = await readApiResponse(res);
          latestSceneSfxData = data;
          latestSceneSupplementData = null;
          refreshSceneActionButtons();
          show(data);
        });
      }

      async function buildSceneSupplements() {
        if (!projectId) return show('请先创建项目');
        if (!latestSceneSfxData || !Array.isArray(latestSceneSfxData.scene_items)) {
          return show('请先执行第7步：场景音效推荐');
        }
        clearSceneFlowFrom('supplements');
        showScenePendingState('supplements');
        await runAction('场景补充单', async () => {
          const res = await apiFetch(`${apiBase()}/analysis/${projectId}/scene-supplements`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scene_sfx_result: latestSceneSfxData }),
          });
          const data = await readApiResponse(res);
          latestSceneSupplementData = data;
          refreshSceneActionButtons();
          show(data);
        });
      }

      async function applyActionGraphDraft() {
        if (!projectId) return show('请先创建项目');
        if (!latestActionGraphDraftData || !Array.isArray(latestActionGraphDraftData.draft_items)) {
          return show('请先执行第3.6步：动作补充单');
        }
        await runAction('应用动作草稿', async () => {
          const res = await apiFetch(`${apiBase()}/analysis/${projectId}/action-graph-draft/apply`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ draft_result: latestActionGraphDraftData }),
          });
          show(await readApiResponse(res));
        });
      }

      async function buildFusion() {
        if (!projectId) return show('请先创建项目');
        await runAction('生成文本音乐执行单', async () => {
          const pre = await apiFetch(`${apiBase()}/analysis/${projectId}/report`);
          const report = await readApiResponse(pre);
          if (!report.audio) {
            throw new Error('请先执行第2步：音乐分析');
          }
          if (!report.text) {
            const textVal = (document.getElementById('textInput').value || '').trim();
            const narrFile = document.getElementById('narrationFile').files[0];
            if (textVal && narrFile) {
              const form = new FormData();
              form.append('text', textVal);
              form.append('file', narrFile);
              const llmProviderOverride = currentLlmProviderOverride();
              if (llmProviderOverride) form.append('llm_provider_override', llmProviderOverride);
              const autoCombined = await apiFetch(`${apiBase()}/analysis/${projectId}/text-narration`, {
                method: 'POST',
                body: form,
              });
              await readApiResponse(autoCombined);
            } else {
              throw new Error('请先执行第9步：文本演绎分析（含时间轴）（需同时提供文本和演绎音频）');
            }
          }
          const llmProviderOverride = currentLlmProviderOverride();
          const fusionUrl = llmProviderOverride
            ? `${apiBase()}/analysis/${projectId}/fusion?llm_provider_override=${encodeURIComponent(llmProviderOverride)}`
            : `${apiBase()}/analysis/${projectId}/fusion`;
          const res = await apiFetch(fusionUrl, { method: 'POST' });
          show(await readApiResponse(res));
        });
      }

      async function fetchReport() {
        if (!projectId) return show('请先创建项目');
        await runAction('拉取完整报告', async () => {
          const res = await apiFetch(`${apiBase()}/analysis/${projectId}/report`);
          show(await readApiResponse(res));
        });
      }

      async function _download(url, fallbackName) {
        const res = await apiFetch(url);
        if (!res.ok) {
          const err = await readApiResponse(res);
          show(err);
          return;
        }
        syncUsageFromDownloadResponse(res);
        const blob = await res.blob();
        const a = document.createElement('a');
        const href = URL.createObjectURL(blob);
        a.href = href;
        const cd = res.headers.get('content-disposition') || '';
        const m = cd.match(/filename=\"?([^\";]+)\"?/i);
        a.download = (m && m[1]) || fallbackName;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(href);
      }

      async function downloadActionSfxAsset(sourceName, label, fileName, downloadApi, sourceDomain = 'action', genre = '', scopeLabel = '', displayName = '') {
        if (!downloadApi) return show('缺少素材下载地址');
        const sep = downloadApi.includes('?') ? '&' : '?';
        const params = [
          `project_id=${encodeURIComponent(projectId || '')}`,
          `label=${encodeURIComponent(label || '')}`,
          `source_domain=${encodeURIComponent(sourceDomain || 'action')}`,
          `genre=${encodeURIComponent(genre || '')}`,
          `scope_label=${encodeURIComponent(scopeLabel || '')}`,
          `display_name=${encodeURIComponent(displayName || label || fileName || '')}`,
        ];
        if (sourceDomain === 'scene') {
          params.push(`scene_name=${encodeURIComponent(sourceName || '')}`);
        } else {
          params.push(`verb=${encodeURIComponent(sourceName || '')}`);
        }
        const url = `${apiBase().replace(/\/api$/, '')}${downloadApi}${sep}${params.join('&')}`;
        await _download(url, fileName || 'sfx.bin');
      }

      async function downloadCueCsv() {
        if (!projectId) return show('请先创建项目');
        await _download(`${apiBase()}/analysis/${projectId}/export?type=cue_csv`, `project_${projectId}_cue.csv`);
      }

      async function graphReason() {
        const term = document.getElementById('graphTerm').value.trim();
        if (!term) return show('请输入图谱推理词');
        await runAction('图谱推理', async () => {
          const pidPart = projectId ? `&project_id=${projectId}` : '';
          const res = await apiFetch(`${apiBase()}/graph/reason?term=${encodeURIComponent(term)}&limit=12&max_hops=2${pidPart}`);
          show(await readApiResponse(res));
        });
      }

      async function graphStatus() {
        await runAction('图谱状态', async () => {
          const res = await apiFetch(`${apiBase()}/graph/status`);
          show(await readApiResponse(res));
        });
      }

      async function graphDashboard() {
        await runAction('图谱看板', async () => {
          const [healthRes, statusRes, metricsRes] = await Promise.all([
            apiFetch(`${apiBase().replace(/\/api$/, '')}/health`),
            apiFetch(`${apiBase()}/graph/status`),
            apiFetch(`${apiBase()}/graph/metrics?days=7`),
          ]);
          const health = await readApiResponse(healthRes);
          const status = await readApiResponse(statusRes);
          const metrics = await readApiResponse(metricsRes);
          show({ health, status, metrics, current_project_id: projectId });
        });
      }

      async function opsFunnel() {
        await runAction('漏斗指标', async () => {
          const res = await apiFetch(`${apiBase()}/ops/funnel?days=7`);
          show(await readApiResponse(res));
        });
      }

      async function opsRecommend() {
        await runAction('优化建议', async () => {
          const res = await apiFetch(`${apiBase()}/ops/recommendations?days=7`);
          show(await readApiResponse(res));
        });
      }

      async function opsLexiconDraft() {
        await runAction('生成词典草稿', async () => {
          const res = await apiFetch(`${apiBase()}/ops/lexicon-draft?days=7`);
          const data = await readApiResponse(res);
          latestLexiconDraft = data;
          renderDraftReview(data);
          show(data);
        });
      }

      async function opsLexiconDraftApplyMerge() {
        if (!latestLexiconDraft || !latestLexiconDraft.draft_lexicon) {
          return show('请先执行“生成词典草稿”');
        }
        await runAction('应用词典草稿(merge)', async () => {
          const res = await apiFetch(`${apiBase()}/ops/lexicon-draft/apply`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              draft_lexicon: latestLexiconDraft.draft_lexicon,
              mode: 'merge',
              dry_run: false,
            }),
          });
          show(await readApiResponse(res));
        });
      }

      async function opsLexiconDraftApplyHighConf() {
        if (!latestLexiconDraft || !latestLexiconDraft.draft_items) {
          return show('请先执行“生成词典草稿”');
        }
        await runAction('应用高置信草稿', async () => {
          const res = await apiFetch(`${apiBase()}/ops/lexicon-draft/apply`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              draft_items: latestLexiconDraft.draft_items,
              min_confidence: 0.45,
              only_selected: false,
              mode: 'merge',
              dry_run: false,
            }),
          });
          show(await readApiResponse(res));
        });
      }

      async function opsLexiconDraftApplyManual() {
        if (!latestLexiconDraft || !latestLexiconDraft.draft_items) {
          return show('请先执行“生成词典草稿”');
        }
        const checks = Array.from(document.querySelectorAll('.draft-item-check'));
        if (!checks.length) return show('审核列表为空，请先生成草稿');
        const selectedIdx = new Set(
          checks.filter(ch => ch.checked).map(ch => Number(ch.dataset.idx))
        );
        const items = (latestLexiconDraft.draft_items || []).map((x, idx) => ({
          ...x,
          selected: selectedIdx.has(idx),
        }));
        if (items.filter(x => x.selected).length === 0) {
          return show('请至少勾选一个候选词');
        }
        await runAction('按勾选候选应用', async () => {
          const res = await apiFetch(`${apiBase()}/ops/lexicon-draft/apply`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              draft_items: items,
              min_confidence: 0,
              only_selected: true,
              mode: 'merge',
              dry_run: false,
            }),
          });
          show(await readApiResponse(res));
        });
      }

      function _buildSelectedReviewItems(status) {
        const checks = Array.from(document.querySelectorAll('.draft-item-check'));
        const selectedIdx = new Set(checks.filter(ch => ch.checked).map(ch => Number(ch.dataset.idx)));
        const src = latestLexiconDraft?.draft_items || [];
        const items = src
          .filter((x, idx) => selectedIdx.has(idx))
          .map(x => ({
            candidate: x.candidate,
            target_head: x.target_head,
            status,
            note: status === 'rejected' ? 'manual_reject' : 'manual_approve',
          }));
        return items;
      }

      async function markSelectedDraftRejected() {
        if (!latestLexiconDraft || !latestLexiconDraft.draft_items) return show('请先生成词典草稿');
        const items = _buildSelectedReviewItems('rejected');
        if (!items.length) return show('请至少勾选一个候选词');
        await runAction('标记候选为拒绝', async () => {
          const res = await apiFetch(`${apiBase()}/ops/lexicon-review`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ items }),
          });
          show(await readApiResponse(res));
        });
      }

      async function markSelectedDraftApproved() {
        if (!latestLexiconDraft || !latestLexiconDraft.draft_items) return show('请先生成词典草稿');
        const items = _buildSelectedReviewItems('approved');
        if (!items.length) return show('请至少勾选一个候选词');
        await runAction('标记候选为通过', async () => {
          const res = await apiFetch(`${apiBase()}/ops/lexicon-review`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ items }),
          });
          show(await readApiResponse(res));
        });
      }

      updateFeatureGateUI();
      updateLeaderboardTabs();
      syncAuthBootstrap();
      refreshSceneActionButtons();
    
